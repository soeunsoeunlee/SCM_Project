"""EML·HTML·TXT·이미지·붙여넣기 텍스트의 원본 저장과 추출 API입니다.

추출값은 검토용이며 이 API는 Sales Master를 자동 변경하지 않습니다.
"""

from __future__ import annotations

from email import policy
from email.parser import BytesParser
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .ai_mapping import extract_unstructured_with_ai
from .auth import AuthContext, require_context
from .database import get_session
from .upload_deletion import delete_source_dependents


# 서버에서도 파일 크기와 확장자를 제한하여 잘못된 입력을 막습니다.
router = APIRouter(prefix="/api", tags=["unstructured inputs"])
MAX_BYTES = 10 * 1024 * 1024
ALLOWED = {".eml", ".html", ".htm", ".txt", ".png", ".jpg", ".jpeg", ".webp"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


class TextExtractor(HTMLParser):
    """HTML 태그를 버리고 사람이 읽는 본문 글자만 모으는 간단한 파서입니다."""
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        value = data.strip()
        if value:
            self.parts.append(value)


def html_to_text(value: str) -> str:
    """HTML 문자열을 줄바꿈된 일반 텍스트로 바꿉니다."""
    parser = TextExtractor()
    parser.feed(value)
    return "\n".join(parser.parts)


def decode_bytes(content: bytes) -> str:
    """한국어 파일에서 흔한 UTF-8과 CP949를 순서대로 시도합니다."""
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def eml_to_text(content: bytes) -> str:
    """EML 제목과 본문을 읽되 첨부파일은 본문으로 취급하지 않습니다."""
    message = BytesParser(policy=policy.default).parsebytes(content)
    parts: list[str] = []
    subject = str(message.get("subject", "")).strip()
    if subject:
        parts.append(f"제목: {subject}")
    candidates = message.walk() if message.is_multipart() else [message]
    for part in candidates:
        content_type = part.get_content_type()
        if part.get_content_disposition() == "attachment" or content_type not in {"text/plain", "text/html"}:
            continue
        try:
            body = part.get_content()
        except Exception:
            payload = part.get_payload(decode=True) or b""
            body = decode_bytes(payload)
        parts.append(html_to_text(str(body)) if content_type == "text/html" else str(body))
        if content_type == "text/plain":
            break
    return "\n".join(part.strip() for part in parts if part.strip())


# AI 없이 '항목: 값' 형식에서 찾을 명시적인 별칭 목록입니다.
FIELD_ALIASES = {
    "po_number": ["po", "po번호", "purchase order"],
    "model": ["model", "모델", "model name"],
    "customer": ["customer", "고객", "client"],
    "quantity": ["qty", "quantity", "수량"],
    "currency": ["currency", "통화"],
    "production_site": ["factory", "plant", "생산사이트", "site"],
    "sales_region": ["region", "판매지역"],
}


def extract_fields(text: str) -> dict[str, str]:
    """각 줄의 'PO: 값', 'Qty: 값' 같은 명시적 표현만 추출합니다."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        for field, aliases in FIELD_ALIASES.items():
            alias_pattern = "|".join(re.escape(alias) for alias in sorted(aliases, key=len, reverse=True))
            match = re.match(rf"^(?:{alias_pattern})\s*[:=\-]\s*(.+)$", cleaned, flags=re.IGNORECASE)
            if match and field not in result:
                result[field] = match.group(1).strip()
    return result


def ai_preview_text(data: dict) -> str:
    """이미지 AI 추출 결과를 화면에서 읽을 수 있는 짧은 본문으로 바꿉니다."""
    lines = [str(data.get("document_summary") or "").strip()]
    for index, record in enumerate(data.get("records", []), start=1):
        values = [f"{key}={value}" for key, value in record.items() if value and key not in {"confidence", "evidence"}]
        lines.append(f"{index}. " + " · ".join(values))
        if record.get("evidence"):
            lines.append(f"   근거: {record['evidence']}")
    return "\n".join(line for line in lines if line)[:20_000]


@router.post("/unstructured/inspect", status_code=status.HTTP_201_CREATED)
async def inspect_unstructured(
    data_type: str = Form(...),
    pasted_text: str = Form(""),
    file: UploadFile | None = File(None),
    context: AuthContext = Depends(require_context),
    session: Session = Depends(get_session),
) -> dict:
    """비정형 원본을 보존하고 가능한 경우 텍스트·명시 필드를 추출합니다.

    OCR 도구가 없거나 실패해도 원본은 보존하고 실제 상태를 사용자에게 알립니다.
    """
    if data_type not in {"판매계획", "생산정보", "선적정보"}:
        raise HTTPException(status_code=422, detail="자료 유형을 확인해 주세요.")
    raw: bytes
    extracted_text: str | None = None
    input_type: str
    content_type: str
    file_name: str | None
    extraction_status: str
    # 파일과 붙여넣기 중 실제로 전달된 입력 방식을 판별합니다.
    if file is not None and file.filename:
        file_name = Path(file.filename).name
        extension = Path(file_name).suffix.lower()
        if extension not in ALLOWED:
            raise HTTPException(status_code=415, detail="EML, HTML, HTM, TXT, PNG, JPG, JPEG, WEBP만 지원합니다.")
        raw = await file.read(MAX_BYTES + 1)
        if not raw:
            raise HTTPException(status_code=422, detail="빈 파일은 업로드할 수 없습니다.")
        if len(raw) > MAX_BYTES:
            raise HTTPException(status_code=413, detail="파일 크기는 10MB 이하여야 합니다.")
        content_type = file.content_type or "application/octet-stream"
        if extension == ".eml":
            input_type, extracted_text, extraction_status = "메일", eml_to_text(raw), "텍스트 추출 완료"
        elif extension in {".html", ".htm"}:
            input_type, extracted_text, extraction_status = "HTML", html_to_text(decode_bytes(raw)), "텍스트 추출 완료"
        elif extension == ".txt":
            input_type, extracted_text, extraction_status = "텍스트 파일", decode_bytes(raw), "텍스트 추출 완료"
        else:
            input_type = "이미지"
            if shutil.which("tesseract"):
                try:
                    from PIL import Image
                    import pytesseract
                    from io import BytesIO
                    extracted_text = pytesseract.image_to_string(Image.open(BytesIO(raw)), lang="kor+eng")
                    extraction_status = "OCR 추출 완료"
                except Exception:
                    extraction_status = "OCR 실행 실패 · 원본 보존"
            else:
                extraction_status = "OCR 도구 필요 · 원본 보존"
    elif pasted_text.strip():
        extracted_text = pasted_text.strip()
        raw = extracted_text.encode("utf-8")
        input_type, content_type, file_name, extraction_status = "붙여넣기", "text/plain; charset=utf-8", None, "텍스트 추출 완료"
    else:
        raise HTTPException(status_code=422, detail="파일을 선택하거나 텍스트를 붙여넣어 주세요.")

    # 키가 있으면 줄글·메일은 본문을, 이미지는 원본 이미지를 OpenAI에 보내 여러 업무
    # 레코드로 구조화합니다. 결과는 검토용으로만 저장하고 Sales Master에는 반영하지 않습니다.
    image_media_types = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    ai_result = extract_unstructured_with_ai(
        data_type,
        text=extracted_text if input_type != "이미지" else None,
        image_bytes=raw if input_type == "이미지" else None,
        image_media_type=image_media_types.get(Path(file_name or "").suffix.lower()),
    )
    if ai_result["status"] == "used":
        fields = ai_result["data"]
        extraction_status = "OpenAI 구조화 추출 완료 · 사용자 확인 필요"
        if input_type == "이미지":
            extracted_text = ai_preview_text(fields)
    else:
        fields = extract_fields(extracted_text or "")
        if ai_result["status"] == "failed":
            extraction_status = f"OpenAI 호출 실패 · {extraction_status}"
        elif ai_result["status"] == "not_configured":
            extraction_status = f"OpenAI 미연결 · {extraction_status}"

    # 같은 원본도 생산정보와 선적정보로 각각 분류할 수 있습니다. 같은 유형으로
    # 반복한 경우에만 중복으로 보고, 다른 유형이면 별도 검토 기록을 만듭니다.
    legacy_digest = sha256(context.project.id.encode() + b":" + raw).hexdigest()
    digest = sha256(context.project.id.encode() + b":" + data_type.encode() + b":" + raw).hexdigest()
    existing_source = session.scalar(select(models.SourceMaterial).where(models.SourceMaterial.source_hash == digest))
    if existing_source is None:
        legacy_source = session.scalar(select(models.SourceMaterial).where(models.SourceMaterial.source_hash == legacy_digest))
        if legacy_source:
            legacy_record = session.scalar(select(models.UnstructuredInput).where(models.UnstructuredInput.source_id == legacy_source.id))
            if legacy_record and legacy_record.data_type == data_type:
                existing_source = legacy_source
    if existing_source:
        existing = session.scalar(select(models.UnstructuredInput).where(models.UnstructuredInput.source_id == existing_source.id))
        delete_source_dependents(session, existing.source_id)
        now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
        existing.input_type = input_type
        existing.file_name = file_name
        existing.content_type = content_type
        existing.raw_content = raw
        existing.extracted_text = extracted_text
        existing.extracted_fields = fields
        existing.extraction_status = extraction_status
        existing.created_at = now
        existing.created_by_user_id = context.user.id
        existing_source.name = file_name or "붙여넣기 텍스트"
        existing_source.description = f"{data_type} 비정형 자료 · {input_type}"
        existing_source.received_at = now.isoformat()
        existing_source.source_hash = digest
        session.commit()
        return {"id": existing.id, "source_id": existing.source_id, "data_type": existing.data_type, "input_type": existing.input_type, "file_name": existing.file_name,
                "extraction_status": existing.extraction_status, "extracted_text": existing.extracted_text, "extracted_fields": existing.extracted_fields,
                "duplicate": False, "overwritten": True, "message": "기존 항목을 최신 정보로 덮어썼습니다."}
    record_id = str(uuid4())
    source_id = f"ui-{record_id}"
    source = models.SourceMaterial(id=source_id, name=file_name or "붙여넣기 텍스트", description=f"{data_type} 비정형 자료 · {input_type}", received_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), source_hash=digest)
    record = models.UnstructuredInput(id=record_id, project_id=context.project.id, source_id=source_id, data_type=data_type,
        input_type=input_type, file_name=file_name, content_type=content_type, raw_content=raw, extracted_text=extracted_text,
        extracted_fields=fields, extraction_status=extraction_status, created_by_user_id=context.user.id)
    session.add_all([source, record])
    session.commit()
    return {"id": record.id, "source_id": source_id, "data_type": data_type, "input_type": input_type, "file_name": file_name,
            "extraction_status": extraction_status, "extracted_text": extracted_text, "extracted_fields": fields,
            "duplicate": False, "overwritten": False, "message": "원본과 OpenAI/규칙 기반 추출 결과를 저장했습니다. Sales Master에는 자동 반영하지 않았으므로 내용을 검토해 주세요."}
