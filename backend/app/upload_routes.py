"""판매자료 파일의 업로드 → 매핑 → 검증 → 최종 반영 API 흐름입니다.

원본은 먼저 보존하지만, 사용자가 마지막 반영 버튼을 누르기 전에는 주문을 만들지 않습니다.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .ai_mapping import suggest_order_candidate, suggest_with_ai
from .auth import AuthContext, require_context
from .database import get_session
from .operational_matching import match_all_operational_batches, match_operational_batch
from .sample_files import SAMPLES, get_sample
from .upload_deletion import DELETABLE_DATA_TYPES, UploadMaterialNotFound, UploadMaterialOutOfScope, clear_upload_batch_derivatives, delete_upload_material
from .upload_service import inspect_workbook, mapping_context, normalize_header, sanitize_mapping, standardize_rows


# 업로드 API 공통 주소와 최대 파일 크기(10MB)입니다.
router = APIRouter(prefix="/api", tags=["sales uploads"])
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class HeaderSelection(BaseModel):
    """사용자가 선택한 시트와 헤더 행 번호입니다."""
    sheet_name: str = Field(min_length=1, max_length=160)
    header_row: int = Field(ge=1, le=10_000)


class PrepareUpload(HeaderSelection):
    """헤더 선택에 컬럼 매핑과 단가/총액 설정을 더한 검증 요청입니다."""
    mapping: dict[str, int]
    pricing: dict[str, str]


def _batch_summary(batch: models.UploadBatch) -> dict:
    """DB 업로드 배치를 화면에서 필요한 간단한 JSON으로 바꿉니다."""
    return {
        "id": batch.id,
        "file_name": batch.file_name,
        "data_type": batch.data_type,
        "sales_company_id": batch.sales_company_id,
        "sales_company": batch.sales_company.name if batch.sales_company else "여러 판매법인",
        "production_company_id": batch.production_company_id,
        "production_company": batch.production_company.name if batch.production_company else None,
        "target_month": batch.target_month,
        "reference_date": batch.reference_date.isoformat(),
        "scope_type": batch.scope_type,
        "scope_description": batch.scope_description,
        "status": batch.status,
        "application_mode": batch.application_mode,
        "result_message": batch.result_message,
        "selected_sheet": batch.selected_sheet,
        "header_row": batch.header_row,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "created_by": batch.created_by.display_name if batch.created_by else None,
        "applied_at": batch.applied_at.isoformat() if batch.applied_at else None,
        "download_url": f"/api/source-materials/{batch.source_id}/download",
    }


def _overwrite_operational_batch(
    session: Session,
    batch: models.UploadBatch,
    *,
    safe_name: str,
    extension: str,
    content: bytes,
    sales_company_id: str | None,
    production_company_id: str | None,
    target_month: str,
    reference_date: date,
    scope_type: str,
    scope_description: str,
    user_id: str,
) -> None:
    """같은 생산·선적 파일의 기존 항목을 최신 입력값으로 교체합니다."""
    clear_upload_batch_derivatives(session, batch)
    now = datetime.now(timezone.utc)
    source = session.get(models.SourceMaterial, batch.source_id)
    if source is None:
        raise HTTPException(status_code=409, detail="업로드 원본 기록을 찾지 못했습니다.")
    source.name = safe_name
    source.description = f"{batch.data_type} 파일 · {scope_type} · {scope_description}"
    source.received_at = now.isoformat()
    batch.file_name = safe_name
    batch.file_extension = extension
    batch.file_content = content
    batch.sales_company_id = sales_company_id
    batch.production_company_id = production_company_id
    batch.target_month = target_month
    batch.reference_date = reference_date
    batch.scope_type = scope_type
    batch.scope_description = scope_description
    batch.selected_sheet = None
    batch.header_row = None
    batch.header_signature = None
    batch.mapping_json = None
    batch.pricing_json = None
    batch.status = "업로드 완료"
    batch.application_mode = None
    batch.result_message = "기존 항목을 최신 정보로 덮어썼습니다."
    batch.created_at = now
    batch.created_by_user_id = user_id
    batch.applied_at = None


def _serialize_value(value):
    """Decimal·날짜를 JSON으로 전송 가능한 숫자·문자로 바꿉니다."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _create_order_update_proposals(session: Session, batch: models.UploadBatch, results: list[models.StandardizedResult]) -> int:
    """후속 판매자료를 안전하게 기존 주문과 비교하고 자동 변경 대신 검토 후보를 만듭니다.

    판매법인·PO·모델·고객과 제공된 PO라인까지 일치하는 주문이 정확히 하나면 규칙으로
    후보를 만듭니다. 규칙으로 식별하지 못했을 때만 AI가 최소 표준정보를 비교해 검토
    후보를 추천할 수 있습니다. AI 추천은 주문을 자동 변경하지 않습니다.
    """
    created = 0
    for result in results:
        query = select(models.Order).where(
            models.Order.project_id == batch.project_id,
            models.Order.sales_company_id == batch.sales_company_id,
            models.Order.po_number == result.po_number,
            models.Order.model == result.model,
            models.Order.customer == result.customer,
        )
        if result.po_line_number:
            query = query.where(models.Order.po_line_number == result.po_line_number)
        exact_candidates = list(session.scalars(query.limit(2)))
        ai_basis: dict | None = None
        if len(exact_candidates) == 1:
            order = exact_candidates[0]
        else:
            # 금액과 원본 전체 행은 AI에 보내지 않습니다. 같은 프로젝트·판매법인의
            # 주문 중 식별값이 일부라도 닮은 후보만 좁혀 최대 20건을 전달합니다.
            pool = list(session.scalars(select(models.Order).where(
                models.Order.project_id == batch.project_id,
                models.Order.sales_company_id == batch.sales_company_id,
            ).order_by(models.Order.created_at.desc()).limit(200)))
            source_po = normalize_header(result.po_number)
            source_model = normalize_header(str(result.model or "").split(".")[0])
            source_customer = normalize_header(result.customer)

            def candidate_score(candidate: models.Order) -> int:
                score = 0
                candidate_po = normalize_header(candidate.po_number)
                candidate_model = normalize_header(str(candidate.model or "").split(".")[0])
                candidate_customer = normalize_header(candidate.customer)
                if source_po and candidate_po and (source_po == candidate_po or source_po in candidate_po or candidate_po in source_po):
                    score += 5
                if source_model and candidate_model and source_model == candidate_model:
                    score += 4
                if source_customer and candidate_customer and source_customer == candidate_customer:
                    score += 3
                if candidate.target_month == batch.target_month:
                    score += 2
                if result.quantity is not None and candidate.quantity == result.quantity:
                    score += 1
                return score

            shortlist = sorted(
                ((candidate_score(candidate), candidate) for candidate in pool),
                key=lambda item: item[0],
                reverse=True,
            )
            shortlist = [candidate for score, candidate in shortlist if score > 0][:20]
            recommendation = suggest_order_candidate(
                {
                    "po_number": result.po_number,
                    "po_line_number": result.po_line_number,
                    "model": result.model,
                    "customer": result.customer,
                    "sales_company_id": batch.sales_company_id,
                    "target_month": batch.target_month,
                    "quantity": result.quantity,
                },
                [{
                    "order_id": candidate.id,
                    "po_number": candidate.po_number,
                    "po_line_number": candidate.po_line_number,
                    "model": candidate.model,
                    "customer": candidate.customer,
                    "sales_company_id": candidate.sales_company_id,
                    "target_month": candidate.target_month,
                    "order_quantity": candidate.quantity,
                } for candidate in shortlist],
                "판매계획",
            )
            recommended_id = recommendation.get("candidate_order_id")
            order = next((candidate for candidate in shortlist if candidate.id == recommended_id), None)
            if (
                order is None
                or recommendation.get("confidence") not in {"medium", "high"}
                or not recommendation.get("same_business_quantity")
            ):
                continue
            ai_basis = {
                "confidence": recommendation.get("confidence"),
                "reason": recommendation.get("reason"),
                "signals": recommendation.get("signals", []),
            }
        incoming = {
            "production_company_id": result.production_company_id,
            "target_month": batch.target_month,
            "po_number": result.po_number,
            "po_line_number": result.po_line_number,
            "model": result.model,
            "customer": result.customer,
            "quantity": result.quantity,
            "net_sales_amount": float(result.net_sales_total) if result.net_sales_total is not None else None,
            "gross_sales_amount": float(result.gross_sales_total) if result.gross_sales_total is not None else None,
            "production_site_source": result.production_site,
            "sales_region": result.sales_region,
        }
        # 원본에 없는 선택값은 기존 값을 지우는 변경으로 제안하지 않습니다.
        incoming = {field: value for field, value in incoming.items() if value is not None}
        protected = set(order.manual_override.protected_fields if order.manual_override else [])
        differences = {}
        for field, new_value in incoming.items():
            current = getattr(order, field)
            current_value = float(current) if isinstance(current, Decimal) else current
            if current_value != new_value:
                differences[field] = {"current": current_value, "incoming": new_value, "manual_priority": field in protected}
        if not differences:
            continue
        if ai_basis:
            # 화면 표시용 검토 근거입니다. 적용 가능한 주문 필드 목록에는 포함되지 않습니다.
            incoming["_ai_match_basis"] = ai_basis
        existing = session.scalar(select(models.OrderUpdateProposal.id).where(
            models.OrderUpdateProposal.batch_id == batch.id,
            models.OrderUpdateProposal.upload_result_id == result.id,
            models.OrderUpdateProposal.order_id == order.id,
        ))
        if existing is not None:
            continue
        session.add(models.OrderUpdateProposal(
            id=str(uuid4()), project_id=batch.project_id, order_id=order.id,
            batch_id=batch.id, upload_result_id=result.id, source_id=batch.source_id,
            incoming_values=incoming, differences=differences, status="검토 대기",
        ))
        created += 1
    return created


def _result_row(result: models.StandardizedResult) -> dict:
    """표준화 결과와 연결된 원본 행 번호를 화면용 JSON으로 바꿉니다."""
    return {
        "id": result.id,
        "row_number": result.raw_row.row_number,
        "status": result.status,
        "issues": result.issues,
        "raw_values": result.raw_row.raw_values,
        "po_number": result.po_number,
        "po_line_number": result.po_line_number,
        "model": result.model,
        "customer": result.customer,
        "quantity": result.quantity,
        "net_sales_value": _serialize_value(result.net_sales_value),
        "gross_sales_value": _serialize_value(result.gross_sales_value),
        "net_sales_total": _serialize_value(result.net_sales_total),
        "gross_sales_total": _serialize_value(result.gross_sales_total),
        "currency": result.currency,
        "production_site": result.production_site,
        "sales_region": result.sales_region,
        "applied_order_id": result.applied_order_id,
    }


def _saved_validation_response(session: Session, batch: models.UploadBatch) -> dict:
    """DB에 저장된 행 상태로 검증 요약과 대사를 다시 계산합니다."""
    results = list(session.scalars(
        select(models.StandardizedResult)
        .where(models.StandardizedResult.batch_id == batch.id)
        .order_by(models.StandardizedResult.id)
    ))
    if not results:
        raise HTTPException(status_code=404, detail="저장된 행별 검증 결과가 없습니다.")
    counts = Counter(result.status for result in results)
    reconciliation: dict[str, dict] = defaultdict(lambda: {
        "original_quantity": Decimal("0"), "planned_quantity": Decimal("0"),
        "original_net": Decimal("0"), "planned_net": Decimal("0"),
        "original_gross": Decimal("0"), "planned_gross": Decimal("0"),
        "net_available": False, "gross_available": False,
    })
    for result in results:
        if result.status == "제외" or not result.currency:
            continue
        item = reconciliation[result.currency]
        if result.quantity is not None:
            item["original_quantity"] += Decimal(result.quantity)
            if result.status == "처리":
                item["planned_quantity"] += Decimal(result.quantity)
        for field, value in (("net", result.net_sales_total), ("gross", result.gross_sales_total)):
            if value is not None:
                item[f"{field}_available"] = True
                item[f"original_{field}"] += value
                if result.status == "처리":
                    item[f"planned_{field}"] += value
    pricing = batch.pricing_json or {}
    mapping = batch.mapping_json or {}
    reconciliation_rows = [{
        "currency": currency,
        **{key: float(value) if isinstance(value, Decimal) else value for key, value in values.items()},
        "net_basis": f"Net Sales {pricing.get('net_sales')} → 총액" if "net_sales" in mapping else "원본 컬럼 없음: 대사 불가",
        "gross_basis": f"Gross Sales {pricing.get('gross_sales')} → 총액" if "gross_sales" in mapping else "원본 컬럼 없음: 대사 불가",
    } for currency, values in sorted(reconciliation.items())]
    processable = [result for result in results if result.status == "처리"]
    blocking = [result for result in results if result.status in {"오류", "검토"}]
    can_apply = bool(processable) and not blocking
    headers: list[str] = []
    if batch.selected_sheet and batch.header_row:
        headers = mapping_context(batch.file_content, batch.file_extension, batch.selected_sheet, batch.header_row)["headers"]
    mode = batch.application_mode or _application_mode(session, batch, processable)
    return {
        "batch": _batch_summary(batch),
        "headers": headers,
        "status_counts": {status: counts.get(status, 0) for status in ("처리", "제외", "오류", "검토")},
        "reconciliation": reconciliation_rows,
        "rows": [_result_row(result) for result in results[:500]],
        "row_count": len(results),
        "can_apply": can_apply,
        "application_mode": mode,
        "apply_label": "버전 비교 대기로 저장" if mode == "버전 비교 대기" else "Sales Master에 반영",
    }


def _application_mode(session: Session, batch: models.UploadBatch, results: list[models.StandardizedResult]) -> str:
    """최초 반영인지 안전한 버전 비교 대기인지 결정합니다.

    같은 프로젝트·법인·월에 업로드 주문이 있거나 동일 주문 후보가 있으면 덮어쓰지 않습니다.
    """
    previous_upload = session.scalar(select(models.Order.id).where(
        models.Order.project_id == batch.project_id,
        models.Order.sales_company_id == batch.sales_company_id,
        models.Order.target_month == batch.target_month,
        models.Order.upload_batch_id.is_not(None),
        models.Order.upload_batch_id != batch.id,
    ).limit(1))
    if previous_upload:
        return "버전 비교 대기"
    for result in results:
        if result.status != "처리":
            continue
        existing = session.scalar(select(models.Order.id).where(
            models.Order.project_id == batch.project_id,
            models.Order.sales_company_id == batch.sales_company_id,
            models.Order.target_month == batch.target_month,
            models.Order.po_number == result.po_number,
            models.Order.model == result.model,
            models.Order.customer == result.customer,
        ).limit(1))
        if existing:
            return "버전 비교 대기"
    return "최초 반영"


def _closed_preservation(session: Session, batch: models.UploadBatch) -> tuple[int, int]:
    """후속 자료가 절대 덮어쓰면 안 되는 ERP Closed 확정 건수와 수량을 계산합니다."""
    row = session.execute(
        select(
            func.count(func.distinct(models.ErpConfirmation.order_id)),
            func.coalesce(func.sum(models.ErpConfirmation.confirmed_quantity), 0),
        )
        .join(models.Order, models.ErpConfirmation.order_id == models.Order.id)
        .where(
            models.Order.project_id == batch.project_id,
            models.Order.sales_company_id == batch.sales_company_id,
            models.Order.target_month == batch.target_month,
            models.ErpConfirmation.status != "취소·정정",
        )
    ).one()
    return int(row[0] or 0), int(row[1] or 0)


@router.get("/upload-samples")
def list_samples() -> list[dict]:
    """화면에서 내려받을 합성 정상/오류 샘플 목록을 반환합니다."""
    return [{**item, "download_url": f"/api/upload-samples/{item['id']}"} for item in SAMPLES]


@router.get("/upload-samples/{sample_id}")
def download_sample(sample_id: str) -> Response:
    """선택한 샘플 파일을 메모리에서 만들어 다운로드합니다. DB는 변경하지 않습니다."""
    try:
        metadata, content = get_sample(sample_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail="샘플을 찾을 수 없습니다.") from error
    content_types = {"XLSX": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "XLS": "application/vnd.ms-excel", "CSV": "text/csv; charset=utf-8"}
    return Response(content=content, media_type=content_types[metadata["format"]], headers={"Content-Disposition": f'attachment; filename="{metadata["file_name"]}"'})


@router.get("/uploads")
def list_uploads(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> list[dict]:
    """현재 프로젝트의 최근 업로드 배치만 반환합니다."""
    batches = session.scalars(select(models.UploadBatch).where(models.UploadBatch.project_id == context.project.id).order_by(models.UploadBatch.created_at.desc()).limit(20)).all()
    return [_batch_summary(batch) for batch in batches]


@router.get("/uploads/{batch_id}/inspect")
def reopen_upload(batch_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """업로드 이력의 판매계획 원본을 다시 읽어 Sales Master 매칭 작업을 이어갑니다."""
    batch = session.get(models.UploadBatch, batch_id)
    if batch is None or batch.project_id != context.project.id:
        raise HTTPException(status_code=404, detail="업로드 배치를 찾을 수 없습니다.")
    if batch.data_type != "판매계획":
        raise HTTPException(status_code=409, detail="Sales Master 매칭은 판매계획 파일에서만 사용합니다.")
    try:
        sheets = inspect_workbook(batch.file_content, batch.file_extension)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"저장된 원본 파일을 다시 읽지 못했습니다: {error}") from error
    return {
        "batch": _batch_summary(batch),
        "sheets": sheets,
        "duplicate_file": True,
        "overwritten": False,
        "message": "저장된 판매계획을 불러왔습니다. 시트와 컬럼을 확인한 뒤 Sales Master에 반영하세요.",
    }


@router.get("/uploads/history")
def upload_history(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> list[dict]:
    """현재 프로젝트의 파일·메일·이미지·텍스트 원본을 최근 순서로 반환합니다."""
    batches = session.scalars(select(models.UploadBatch).where(models.UploadBatch.project_id == context.project.id).order_by(models.UploadBatch.created_at.desc()).limit(100)).all()
    inputs = session.scalars(select(models.UnstructuredInput).where(models.UnstructuredInput.project_id == context.project.id).order_by(models.UnstructuredInput.created_at.desc()).limit(100)).all()
    display_status = {"검사 완료": "업로드 완료", "검증 완료": "표준화 완료", "검증 보류": "확인 필요"}
    rows = [{**_batch_summary(batch), "status": display_status.get(batch.status, batch.status), "input_type": batch.file_extension.removeprefix(".").upper(), "record_kind": "batch", "deletable": batch.data_type in DELETABLE_DATA_TYPES, "created_at": batch.created_at.isoformat()} for batch in batches]
    rows.extend({
        "id": item.id, "file_name": item.file_name or "붙여넣기 텍스트", "data_type": item.data_type,
        "sales_company": None, "production_company": None, "target_month": None, "reference_date": None, "scope_type": None,
        "status": item.extraction_status, "input_type": item.input_type,
        "record_kind": "unstructured", "deletable": item.data_type in DELETABLE_DATA_TYPES,
        "created_by": item.created_by.display_name if item.created_by else None,
        "created_at": item.created_at.isoformat(), "download_url": f"/api/source-materials/{item.source_id}/download",
    } for item in inputs)
    return sorted(rows, key=lambda item: item["created_at"], reverse=True)[:100]


@router.delete("/upload-materials/{record_kind}/{record_id}")
def remove_upload_material(record_kind: str, record_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """현재 프로젝트의 판매계획·생산·선적 업로드와 파생 데이터를 영구 삭제합니다."""
    try:
        result = delete_upload_material(session, context.project.id, record_kind, record_id)
        session.commit()
    except UploadMaterialNotFound:
        session.rollback()
        raise HTTPException(status_code=404, detail="삭제할 업로드 자료를 찾을 수 없습니다.")
    except UploadMaterialOutOfScope as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        session.rollback()
        raise
    return {
        "deleted": True,
        "id": result.record_id,
        "source_id": result.source_id,
        "deleted_order_count": result.deleted_order_count,
        "message": (
            f"판매계획 원본과 이 파일이 만든 Sales Master 주문 {result.deleted_order_count}건을 함께 영구 삭제했습니다."
            if result.data_type == "판매계획"
            else f"{result.data_type} 원본과 파생 데이터를 영구 삭제했습니다."
        ),
    }


@router.post("/uploads/inspect", status_code=status.HTTP_201_CREATED)
async def inspect_upload(
    file: UploadFile = File(...),
    data_type: str = Form(...),
    sales_company_id: str = Form(""),
    production_company_id: str = Form(""),
    target_month: str = Form(...),
    reference_date: str = Form(...),
    scope_type: str = Form(...),
    scope_description: str = Form(...),
    context: AuthContext = Depends(require_context),
    session: Session = Depends(get_session),
) -> dict:
    """파일과 공통값을 검증하고 원본을 보존한 뒤 시트 미리보기를 반환합니다.

    같은 프로젝트의 동일 파일은 새 배치를 만들지 않고 기존 배치를 반환합니다.
    """
    if data_type not in {"판매계획", "생산정보", "선적정보"}:
        raise HTTPException(status_code=422, detail="자료 유형은 판매계획, 생산정보, 선적정보 중에서 선택해 주세요.")
    # 생산·선적정보 한 파일에는 여러 판매법인 물량이 섞일 수 있으므로 특정 판매법인을 강제하지 않습니다.
    # 판매계획만 주문 소유 판매법인을 필수로 받습니다.
    normalized_sales_company_id = sales_company_id.strip() or None
    operational = data_type in {"생산정보", "선적정보"}
    if operational:
        normalized_sales_company_id = None
    else:
        company = session.get(models.Company, normalized_sales_company_id)
        if company is None or company.kind != "판매법인":
            raise HTTPException(status_code=422, detail="판매법인을 확인해 주세요.")
        if company.project_id not in {None, context.project.id}:
            raise HTTPException(status_code=404, detail="현재 프로젝트에서 사용할 수 없는 판매법인입니다.")
    normalized_production_company_id = production_company_id.strip() or None
    if operational:
        plant = session.get(models.Company, normalized_production_company_id)
        if plant is None or plant.kind != "생산법인":
            raise HTTPException(status_code=422, detail="생산정보와 선적정보는 생산법인을 선택해 주세요.")
        if plant.project_id not in {None, context.project.id}:
            raise HTTPException(status_code=404, detail="현재 프로젝트에서 사용할 수 없는 생산법인입니다.")
    else:
        normalized_production_company_id = None
    try:
        parsed_reference_date = date.fromisoformat(reference_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="자료 기준일을 확인해 주세요.") from error
    # 사용자는 기준일만 고릅니다. 대상 월은 항상 같은 연·월로 서버가 확정합니다.
    target_month = parsed_reference_date.strftime("%Y-%m")
    if scope_type not in {"전체 현황", "변경분"}:
        raise HTTPException(status_code=422, detail="전체 현황 또는 변경분을 선택해 주세요.")
    if not scope_description.strip():
        raise HTTPException(status_code=422, detail="적용 범위를 입력해 주세요.")
    # 폴더 경로를 버리고 파일명만 사용하여 경로 조작을 막습니다.
    safe_name = Path(file.filename or "upload").name
    extension = Path(safe_name).suffix.lower()
    if extension not in {".xlsx", ".xls", ".csv"}:
        raise HTTPException(status_code=415, detail="XLSX, XLS, CSV 파일만 지원합니다.")
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일은 업로드할 수 없습니다.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="파일 크기는 10MB 이하여야 합니다.")
    # 파일 내용 자체의 해시로 이름이 달라도 같은 파일인지 판단합니다.
    digest = sha256(content).hexdigest()
    try:
        sheets = inspect_workbook(content, extension)
    except Exception as error:
        raise HTTPException(status_code=422, detail=f"파일을 읽지 못했습니다: {error}") from error
    existing = session.scalar(select(models.UploadBatch).where(models.UploadBatch.project_id == context.project.id, models.UploadBatch.data_type == data_type, models.UploadBatch.file_hash == digest))
    if existing:
        if data_type == "판매계획":
            return {"batch": _batch_summary(existing), "sheets": sheets, "duplicate_file": True, "overwritten": False, "message": "같은 판매계획 파일이 이미 저장되어 기존 배치를 불러왔습니다. 판매계획 동작은 변경하지 않았습니다."}
        _overwrite_operational_batch(
            session, existing, safe_name=safe_name, extension=extension, content=content,
            sales_company_id=normalized_sales_company_id, production_company_id=normalized_production_company_id, target_month=target_month,
            reference_date=parsed_reference_date, scope_type=scope_type,
            scope_description=scope_description.strip(),
            user_id=context.user.id,
        )
        session.commit()
        session.refresh(existing)
        match = match_operational_batch(session, existing) if operational else None
        session.commit()
        message = "기존 항목을 최신 정보로 덮어썼습니다."
        if match:
            message += f" {data_type} 자동 연결 {match['matched_rows']}행, AI 검토 후보 {match['ai_suggested_rows']}행, 검토 필요 {match['review_rows']}행입니다."
        return {"batch": _batch_summary(existing), "sheets": sheets, "duplicate_file": False, "overwritten": True, "message": message}
    batch_id = str(uuid4())
    source_id = f"us-{batch_id}"
    source = models.SourceMaterial(
        id=source_id,
        name=safe_name,
        description=f"{data_type} 파일 · {scope_type} · {scope_description.strip()}",
        received_at=datetime.now(timezone.utc).isoformat(),
        source_hash=sha256(f"{context.project.id}:{data_type}:{digest}".encode()).hexdigest(),
    )
    batch = models.UploadBatch(
        id=batch_id,
        project_id=context.project.id,
        source_id=source_id,
        file_name=safe_name,
        file_extension=extension,
        file_hash=digest,
        data_type=data_type,
        file_content=content,
        sales_company_id=normalized_sales_company_id,
        production_company_id=normalized_production_company_id,
        target_month=target_month,
        reference_date=parsed_reference_date,
        scope_type=scope_type,
        scope_description=scope_description.strip(),
        status="업로드 완료",
        created_by_user_id=context.user.id,
    )
    session.add_all([source, batch])
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        duplicate = session.scalar(select(models.UploadBatch).where(models.UploadBatch.project_id == context.project.id, models.UploadBatch.data_type == data_type, models.UploadBatch.file_hash == digest))
        if duplicate:
            if data_type == "판매계획":
                return {"batch": _batch_summary(duplicate), "sheets": sheets, "duplicate_file": True, "overwritten": False, "message": "동시에 등록된 같은 판매계획 파일의 기존 배치를 불러왔습니다."}
            _overwrite_operational_batch(
                session, duplicate, safe_name=safe_name, extension=extension, content=content,
                sales_company_id=normalized_sales_company_id, production_company_id=normalized_production_company_id, target_month=target_month,
                reference_date=parsed_reference_date, scope_type=scope_type,
                scope_description=scope_description.strip(),
                user_id=context.user.id,
            )
            session.commit()
            session.refresh(duplicate)
            match = match_operational_batch(session, duplicate) if operational else None
            session.commit()
            message = "기존 항목을 최신 정보로 덮어썼습니다."
            if match:
                message += f" {data_type} 자동 연결 {match['matched_rows']}행, AI 검토 후보 {match['ai_suggested_rows']}행, 검토 필요 {match['review_rows']}행입니다."
            return {"batch": _batch_summary(duplicate), "sheets": sheets, "duplicate_file": False, "overwritten": True, "message": message}
        raise HTTPException(status_code=409, detail="업로드 배치를 저장하지 못했습니다.") from error
    session.refresh(batch)
    match = match_operational_batch(session, batch) if operational else None
    if match:
        session.commit()
        message = f"{data_type}를 저장했습니다. 자동 연결 {match['matched_rows']}행, AI 검토 후보 {match['ai_suggested_rows']}행, 검토 필요 {match['review_rows']}행입니다."
    else:
        message = "판매계획 파일을 저장했습니다. 적용할 시트와 컬럼을 이어서 설정하세요." if data_type == "판매계획" else f"{data_type} 파일과 시트 정보를 저장했습니다. 업로드 이력에서 원본을 다시 받을 수 있습니다."
    return {"batch": _batch_summary(batch), "sheets": sheets, "duplicate_file": False, "overwritten": False, "message": message}


@router.post("/uploads/{batch_id}/mapping")
def get_mapping_context(batch_id: str, payload: HeaderSelection, auth_context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """선택한 헤더의 별칭 제안과 정확히 일치하는 저장 매핑을 반환합니다."""
    batch = session.get(models.UploadBatch, batch_id)
    if batch is None or batch.project_id != auth_context.project.id:
        raise HTTPException(status_code=404, detail="업로드 배치를 찾을 수 없습니다.")
    if batch.data_type != "판매계획":
        raise HTTPException(status_code=409, detail="컬럼 표준화와 Sales Master 반영은 판매계획 파일에서만 사용합니다.")
    try:
        context = mapping_context(batch.file_content, batch.file_extension, payload.sheet_name, payload.header_row)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    profile = session.scalar(select(models.MappingProfile).where(
        models.MappingProfile.project_id == auth_context.project.id,
        models.MappingProfile.sales_company_id == batch.sales_company_id,
        models.MappingProfile.header_signature == context["header_signature"],
    ))
    saved_mapping = sanitize_mapping(profile.mapping_json) if profile else None
    ai_result = {"status": "not_needed", "message": "저장된 컬럼 연결을 사용합니다.", "suggestions": {}}
    if not profile:
        unresolved = [field for field in context["standard_fields"] if field["key"] not in context["suggested_mapping"]]
        ai_result = suggest_with_ai(context["headers"], unresolved, set(context["suggested_mapping"].values()))
        for field, suggestion in ai_result["suggestions"].items():
            context["suggested_mapping"][field] = suggestion["source_index"]
    context.update({
        "saved_mapping": saved_mapping,
        "saved_pricing": profile.pricing_json if profile else None,
        "profile_reused": profile is not None,
        "ai_status": ai_result["status"],
        "ai_suggestions": ai_result["suggestions"],
        "message": "같은 법인과 동일한 원본 컬럼 구성의 저장 매핑을 불러왔습니다." if profile else ai_result["message"],
    })
    return context


@router.post("/uploads/{batch_id}/prepare")
def prepare_upload(batch_id: str, payload: PrepareUpload, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """모든 데이터 행을 표준화·검증하고 원본 행과 결과를 DB에 보존합니다."""
    batch = session.get(models.UploadBatch, batch_id)
    if batch is None or batch.project_id != context.project.id:
        raise HTTPException(status_code=404, detail="업로드 배치를 찾을 수 없습니다.")
    if batch.data_type != "판매계획":
        raise HTTPException(status_code=409, detail="컬럼 표준화와 Sales Master 반영은 판매계획 파일에서만 사용합니다.")
    if batch.status in {"반영 완료", "버전 비교 대기"}:
        raise HTTPException(status_code=409, detail="이미 처리가 끝난 배치는 다시 표준화할 수 없습니다.")
    try:
        clean_mapping = sanitize_mapping(payload.mapping)
        prepared = standardize_rows(batch.file_content, batch.file_extension, payload.sheet_name, payload.header_row, clean_mapping, payload.pricing)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    # 같은 미반영 배치를 다시 검증할 때 이전 결과만 교체하고 원본 파일은 유지합니다.
    session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id == batch.id))
    session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id == batch.id))
    session.flush()
    saved_results: list[models.StandardizedResult] = []
    # 제외·오류·검토를 포함한 모든 데이터 행을 빠짐없이 저장합니다.
    for row in prepared["rows"]:
        raw = models.UploadRawRow(
            batch_id=batch.id,
            sheet_name=payload.sheet_name,
            row_number=row["row_number"],
            raw_values=row["raw_values"],
            formula_missing_columns=row.get("formula_missing_columns", []),
            merged_columns=row.get("merged_columns", []),
            status=row["status"],
            issues=row["issues"],
        )
        session.add(raw)
        session.flush()
        result = models.StandardizedResult(
            batch_id=batch.id,
            raw_row_id=raw.id,
            status=row["status"],
            issues=row["issues"],
            po_number=row.get("po_number"),
            po_line_number=row.get("po_line_number"),
            model=row.get("model"),
            customer=row.get("customer"),
            quantity=row.get("quantity"),
            net_sales_value=row.get("net_sales_value"),
            gross_sales_value=row.get("gross_sales_value"),
            net_sales_total=row.get("net_sales_total"),
            gross_sales_total=row.get("gross_sales_total"),
            currency=row.get("currency"),
            production_site=row.get("production_site"),
            production_company_id=row.get("production_company_id"),
            sales_region=row.get("sales_region"),
        )
        session.add(result)
        saved_results.append(result)
    session.flush()
    mode = _application_mode(session, batch, saved_results)
    batch.selected_sheet = payload.sheet_name
    batch.header_row = payload.header_row
    batch.header_signature = prepared["header_signature"]
    batch.mapping_json = clean_mapping
    batch.pricing_json = payload.pricing
    batch.application_mode = mode
    batch.status = "표준화 완료" if prepared["can_apply"] else "확인 필요"
    batch.result_message = "표준화 결과를 만들었습니다. 수량과 금액을 확인한 뒤 반영하세요." if prepared["can_apply"] else "처리 불가 또는 확인 필요 행이 있습니다. 행별 처리 내용을 확인해 주세요."
    profile = session.scalar(select(models.MappingProfile).where(
        models.MappingProfile.project_id == context.project.id,
        models.MappingProfile.sales_company_id == batch.sales_company_id,
        models.MappingProfile.header_signature == prepared["header_signature"],
    ))
    if profile is None:
        session.add(models.MappingProfile(
            project_id=context.project.id,
            sales_company_id=batch.sales_company_id,
            header_signature=prepared["header_signature"],
            headers_json=prepared["headers"],
            mapping_json=clean_mapping,
            pricing_json=payload.pricing,
        ))
    else:
        profile.headers_json = prepared["headers"]
        profile.mapping_json = clean_mapping
        profile.pricing_json = payload.pricing
    session.commit()
    return _saved_validation_response(session, batch)


@router.get("/uploads/{batch_id}/validation")
def get_saved_validation(batch_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """새로고침 후에도 저장된 행별 검토·제외 결정을 다시 보여 줍니다."""
    batch = session.get(models.UploadBatch, batch_id)
    if batch is None or batch.project_id != context.project.id:
        raise HTTPException(status_code=404, detail="업로드 배치를 찾을 수 없습니다.")
    return _saved_validation_response(session, batch)


@router.post("/uploads/{batch_id}/rows/{result_id}/exclude")
def exclude_upload_row(batch_id: str, result_id: int, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """사용자가 확인한 오류·검토 행을 원본은 보존한 채 반영 대상에서 제외합니다."""
    with session.begin_nested():
        batch = session.scalar(select(models.UploadBatch).where(
            models.UploadBatch.id == batch_id,
            models.UploadBatch.project_id == context.project.id,
        ).with_for_update())
        if batch is None:
            raise HTTPException(status_code=404, detail="업로드 배치를 찾을 수 없습니다.")
        if batch.status in {"반영 완료", "버전 비교 대기"}:
            raise HTTPException(status_code=409, detail="이미 반영된 배치의 행은 제외할 수 없습니다.")
        result = session.scalar(select(models.StandardizedResult).where(
            models.StandardizedResult.id == result_id,
            models.StandardizedResult.batch_id == batch.id,
        ).with_for_update())
        if result is None:
            raise HTTPException(status_code=404, detail="행별 검증 결과를 찾을 수 없습니다.")
        if result.status not in {"오류", "검토", "제외"}:
            raise HTTPException(status_code=409, detail="정상 처리 행은 이 화면에서 제외할 수 없습니다.")
        if result.status != "제외":
            decision = "사용자가 행별 내용을 확인하고 반영 대상에서 제외"
            result.status = "제외"
            result.issues = [*result.issues, decision]
            result.raw_row.status = "제외"
            result.raw_row.issues = [*result.raw_row.issues, decision]
        session.flush()
        remaining = session.scalar(select(func.count(models.StandardizedResult.id)).where(
            models.StandardizedResult.batch_id == batch.id,
            models.StandardizedResult.status.in_(["오류", "검토"]),
        )) or 0
        usable = session.scalar(select(func.count(models.StandardizedResult.id)).where(
            models.StandardizedResult.batch_id == batch.id,
            models.StandardizedResult.status == "처리",
        )) or 0
        batch.status = "표준화 완료" if remaining == 0 and usable > 0 else "확인 필요"
        batch.result_message = (
            "사용자가 확인한 행을 제외했습니다. 나머지 수량·금액을 확인한 뒤 반영하세요."
            if batch.status == "표준화 완료"
            else "제외 결정을 저장했습니다. 아직 확인할 행이 남아 있습니다."
        )
    session.commit()
    return _saved_validation_response(session, batch)


@router.post("/uploads/{batch_id}/apply")
def apply_upload(batch_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """검증을 통과한 최초 자료만 Sales Master에 트랜잭션으로 반영합니다.

    오류·검토 행이 하나라도 있으면 전체를 중단합니다. 후속 자료는 주문을 바꾸지 않고
    버전 비교 대기로 두며, 반복 클릭은 기존 결과를 돌려주어 중복을 막습니다.
    """
    # 인증 조회가 시작한 상위 트랜잭션 안에서 저장점을 열어 부분 저장을 방지합니다.
    with session.begin_nested():
        batch = session.get(models.UploadBatch, batch_id, with_for_update=True)
        if batch is None or batch.project_id != context.project.id:
            raise HTTPException(status_code=404, detail="업로드 배치를 찾을 수 없습니다.")
        if batch.data_type != "판매계획":
            raise HTTPException(status_code=409, detail="Sales Master 반영은 판매계획 파일에서만 사용합니다.")
        if batch.status in {"반영 완료", "버전 비교 대기"}:
            count = session.scalar(select(func.count(models.Order.id)).where(models.Order.upload_batch_id == batch.id)) or 0
            summary = _batch_summary(batch)
            session.commit()
            return {"batch": summary, "created_orders": count, "duplicate_request": True, "message": "이미 처리된 배치입니다. 주문을 중복 생성하지 않았습니다."}
        results = list(session.scalars(select(models.StandardizedResult).where(models.StandardizedResult.batch_id == batch.id).order_by(models.StandardizedResult.id)))
        if not results:
            raise HTTPException(status_code=409, detail="먼저 컬럼 매핑과 검증을 실행해 주세요.")
        blocking = [result for result in results if result.status in {"오류", "검토"}]
        processable = [result for result in results if result.status == "처리"]
        if blocking or not processable:
            raise HTTPException(status_code=409, detail="오류·검토 행이 남아 있어 반영하지 않았습니다. 부분 저장도 발생하지 않았습니다.")
        mode = _application_mode(session, batch, processable)
        batch.application_mode = mode
        if mode == "버전 비교 대기":
            closed_orders, closed_quantity = _closed_preservation(session, batch)
            proposal_count = _create_order_update_proposals(session, batch, processable)
            batch.status = "버전 비교 대기"
            protected = f" Closed 확정 {closed_orders}건·{closed_quantity}ea도 그대로 유지했습니다." if closed_orders else ""
            proposals = f" 안전하게 식별된 주문의 업데이트 검토 {proposal_count}건을 만들었습니다." if proposal_count else " 안전하게 단일 주문으로 식별된 변경은 없어 자동 제안을 만들지 않았습니다."
            batch.result_message = f"원본과 표준화 결과를 보존했습니다. 기존 주문과 ERP Closed 기록은 변경하지 않았습니다.{protected}{proposals}"
            batch.applied_at = datetime.now(timezone.utc)
            session.flush()
            summary = _batch_summary(batch)
            result_message = batch.result_message
            session.commit()
            return {"batch": summary, "created_orders": 0, "duplicate_request": False, "message": result_message}

        version_id = f"pv-{uuid4()}"
        version = models.PlanVersion(
            id=version_id,
            project_id=context.project.id,
            source_id=batch.source_id,
            sales_company_id=batch.sales_company_id,
            reference_date=batch.reference_date.isoformat(),
            scope_type=batch.scope_type,
        )
        session.add(version)
        # 처리 가능한 원본 행 하나마다 주문 하나와 버전 행·변경 이력을 함께 만듭니다.
        for result in processable:
            quantity = result.quantity or 0
            if result.net_sales_value is not None:
                unit_price = result.net_sales_value if batch.pricing_json.get("net_sales") == "단가" else result.net_sales_value / quantity
            elif result.gross_sales_value is not None:
                unit_price = result.gross_sales_value if batch.pricing_json.get("gross_sales") == "단가" else result.gross_sales_value / quantity
            else:
                raise HTTPException(status_code=409, detail="금액이 없는 행이 있어 반영하지 않았습니다.")
            order_id = str(uuid4())
            order = models.Order(
                id=order_id,
                project_id=context.project.id,
                order_number=f"UP-{batch.id[:8].upper()}-{result.raw_row.row_number:05d}",
                sales_company_id=batch.sales_company_id,
                production_company_id=result.production_company_id,
                target_month=batch.target_month,
                po_number=result.po_number,
                po_line_number=result.po_line_number,
                model=result.model,
                customer=result.customer,
                quantity=quantity,
                currency=result.currency,
                unit_price=unit_price,
                net_sales_amount=result.net_sales_total,
                gross_sales_amount=result.gross_sales_total,
                net_sales_type=batch.pricing_json.get("net_sales") if "net_sales" in batch.mapping_json else None,
                gross_sales_type=batch.pricing_json.get("gross_sales") if "gross_sales" in batch.mapping_json else None,
                production_site_source=result.production_site,
                sales_region=result.sales_region,
                upload_batch_id=batch.id,
                upload_result_id=result.id,
                erp_status="미확인",
                match_status="판매자료 반영",
                source_id=batch.source_id,
            )
            session.add(order)
            session.flush()
            result.applied_order_id = order_id
            session.add(models.PlanVersionLine(plan_version_id=version_id, order_id=order_id, quantity=quantity))
            session.add(models.ChangeEvent(
                id=f"ue-{uuid4()}",
                order_id=order_id,
                source_id=batch.source_id,
                event_type="판매자료 최초 반영",
                changed_at=datetime.now(timezone.utc),
                after_value=f"원본 {batch.file_name} {result.raw_row.row_number}행 · {quantity}대",
            ))
        batch.status = "반영 완료"
        batch.applied_at = datetime.now(timezone.utc)
        batch.result_message = f"{len(processable)}개 원본 행을 Sales Master에 반영했습니다."
        session.flush()
        operational_match = match_all_operational_batches(session, context.project.id)
        batch.result_message += f" 생산·선적 자동 연결 {operational_match['matched_rows']}행, AI 검토 후보 {operational_match['ai_suggested_rows']}행, 검토 필요 {operational_match['review_rows']}행입니다."
        summary = _batch_summary(batch)
        result_message = batch.result_message
    session.commit()
    return {"batch": summary, "created_orders": len(processable), "duplicate_request": False, "message": result_message}
