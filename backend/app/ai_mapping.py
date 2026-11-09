"""OpenAI Responses API를 이용한 SCM 컬럼·주문 후보 추천입니다.

API 키는 프로젝트 루트 ``.env``의 ``OPENAI_API_KEY``에서만 읽습니다. 파일 바이트나
원본 전체 행은 보내지 않고, 컬럼 매핑에는 헤더명만, 주문 후보 추천에는 표준화된 최소
식별정보와 내부 후보 ID만 전송합니다. AI 결과는 제안이며 수량·중복·수동 우선 규칙은
항상 서버가 다시 검증합니다.
"""
from __future__ import annotations

import json
import os
import base64
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def is_openai_configured() -> bool:
    """키 내용은 노출하지 않고 설정 여부만 반환합니다."""
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _response_text(payload: dict) -> str | None:
    """Responses API 응답의 output_text 조각을 찾아 반환합니다."""
    for output in payload.get("output", []):
        if output.get("type") != "message":
            continue
        for content in output.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                return content["text"]
    return None


def _structured_response(prompt: dict, schema: dict, schema_name: str, model_env: str) -> dict:
    """비밀값을 로그에 남기지 않고 JSON Schema 응답을 한 번 요청합니다."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"status": "not_configured", "data": None, "message": "OpenAI API 키가 설정되지 않았습니다."}
    model = os.getenv(model_env, "").strip() or os.getenv("OPENAI_MAPPING_MODEL", "gpt-5-mini").strip() or "gpt-5-mini"
    body = json.dumps({
        "model": model,
        "store": False,
        "input": [{"role": "user", "content": json.dumps(prompt, ensure_ascii=False)}],
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
    }).encode("utf-8")
    request = Request(
        "https://api.openai.com/v1/responses",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(_response_text(payload) or "{}")
        return {"status": "used", "data": parsed, "message": "OpenAI 추천을 받았습니다."}
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return {"status": "failed", "data": None, "message": "OpenAI 호출에 실패해 규칙 기반 처리만 사용합니다."}


def _structured_content_response(content: list[dict], schema: dict, schema_name: str, model_env: str) -> dict:
    """텍스트 또는 이미지 입력을 JSON Schema 결과로 변환합니다."""
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return {"status": "not_configured", "data": None, "message": "OpenAI API 키가 설정되지 않았습니다."}
    model = os.getenv(model_env, "").strip() or "gpt-5-mini"
    body = json.dumps({
        "model": model,
        "store": False,
        "input": [{"role": "user", "content": content}],
        "text": {"format": {"type": "json_schema", "name": schema_name, "strict": True, "schema": schema}},
    }).encode("utf-8")
    request = Request(
        "https://api.openai.com/v1/responses",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(_response_text(payload) or "{}")
        return {"status": "used", "data": parsed, "message": "OpenAI 구조화 추출을 완료했습니다."}
    except (HTTPError, URLError, TimeoutError, ValueError, json.JSONDecodeError):
        return {"status": "failed", "data": None, "message": "OpenAI 구조화 추출에 실패했습니다."}


def extract_unstructured_with_ai(
    data_type: str,
    *,
    text: str | None = None,
    image_bytes: bytes | None = None,
    image_media_type: str | None = None,
) -> dict:
    """메일·줄글·이미지에서 여러 업무 레코드를 검토용으로 구조화합니다."""
    if not is_openai_configured():
        return {"status": "not_configured", "data": None, "message": "OpenAI API 키가 설정되지 않았습니다."}
    if not (text or image_bytes):
        return {"status": "not_needed", "data": None, "message": "추출할 내용이 없습니다."}

    record_properties = {
        "po_number": {"type": "string"},
        "po_line_number": {"type": "string"},
        "model": {"type": "string"},
        "customer": {"type": "string"},
        "quantity": {"type": "string"},
        "net_sales_usd": {"type": "string"},
        "gross_sales_usd": {"type": "string"},
        "sales_company": {"type": "string"},
        "production_company": {"type": "string"},
        "production_site": {"type": "string"},
        "sales_region": {"type": "string"},
        "target_month": {"type": "string"},
        "pst": {"type": "string"},
        "production_status": {"type": "string"},
        "etd": {"type": "string"},
        "eta": {"type": "string"},
        "carrier": {"type": "string"},
        "vessel_name": {"type": "string"},
        "shipment_status": {"type": "string"},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence": {"type": "string"},
    }
    schema = {
        "type": "object",
        "properties": {
            "document_summary": {"type": "string"},
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": record_properties,
                    "required": list(record_properties),
                    "additionalProperties": False,
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["document_summary", "records", "warnings"],
        "additionalProperties": False,
    }
    instructions = {
        "task": f"Extract every {data_type} business record from this SCM material for human review.",
        "rules": [
            "Preserve leading zeros in PO numbers and model suffixes.",
            "Keep split quantities as separate records; never deduplicate them.",
            "Use an empty string for fields not explicitly supported by the material.",
            "Do not infer completed production or shipment from a planned date alone.",
            "Do not convert currencies. Sales amounts are recorded only when clearly stated as USD.",
            "Treat totals, signatures, email footers and explanatory prose as context, not order records.",
            "Include short source evidence and lower confidence when the association is ambiguous.",
        ],
    }
    content: list[dict] = [{"type": "input_text", "text": json.dumps(instructions, ensure_ascii=False)}]
    if text:
        max_chars = max(1_000, int(os.getenv("OPENAI_UNSTRUCTURED_MAX_CHARS", "60000")))
        content.append({"type": "input_text", "text": text[:max_chars]})
    if image_bytes:
        media_type = image_media_type if image_media_type in {"image/png", "image/jpeg", "image/webp"} else "image/png"
        encoded = base64.b64encode(image_bytes).decode("ascii")
        content.append({"type": "input_image", "image_url": f"data:{media_type};base64,{encoded}", "detail": "high"})
    return _structured_content_response(content, schema, "scm_unstructured_extract", "OPENAI_UNSTRUCTURED_MODEL")


def suggest_with_ai(headers: list[str], unresolved_fields: list[dict], used_indexes: set[int]) -> dict:
    """미해결 헤더만 표준 SCM 필드에 연결하도록 AI 후보를 받습니다."""
    if not unresolved_fields:
        return {"status": "not_needed", "message": "규칙으로 모든 컬럼을 찾았습니다.", "suggestions": {}}
    if not is_openai_configured():
        return {"status": "not_configured", "message": "AI 키가 없어 규칙 기반 추천만 사용합니다.", "suggestions": {}}

    field_keys = [item["key"] for item in unresolved_fields]
    schema = {
        "type": "object",
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "field_key": {"type": "string", "enum": field_keys},
                        "source_index": {"type": "integer"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["field_key", "source_index", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["matches"],
        "additionalProperties": False,
    }
    prompt = {
        "task": "Map spreadsheet header names to unresolved SCM fields. Return no match when ambiguous.",
        "headers": [{"index": index, "name": name} for index, name in enumerate(headers) if index not in used_indexes],
        "fields": unresolved_fields,
        "rules": [
            "Never reuse a source column.",
            "Currency is always USD and is not mapped.",
            "PO line is optional.",
            "Do not infer values from data rows; these strings are header names only.",
        ],
    }
    response = _structured_response(prompt, schema, "scm_column_mapping", "OPENAI_MAPPING_MODEL")
    if response["status"] != "used":
        return {"status": response["status"], "message": response["message"], "suggestions": {}}

    suggestions: dict[str, dict] = {}
    claimed = set(used_indexes)
    for item in response["data"].get("matches", []):
        field = item.get("field_key")
        index = item.get("source_index")
        if field not in field_keys or not isinstance(index, int) or index < 0 or index >= len(headers) or index in claimed:
            continue
        if item.get("confidence") == "low":
            continue
        suggestions[field] = {"source_index": index, "confidence": item["confidence"], "reason": str(item.get("reason", ""))[:200]}
        claimed.add(index)
    return {"status": "used", "message": "규칙으로 찾지 못한 컬럼에 AI 추천을 추가했습니다. 반영 전 확인해 주세요.", "suggestions": suggestions}


def suggest_order_candidate(source_record: dict, candidates: list[dict], data_type: str) -> dict:
    """표준화된 한 행이 어느 Sales Master 주문인지 검토 후보를 추천합니다."""
    if not candidates:
        return {"status": "not_needed", "candidate_order_id": None, "confidence": "low", "reason": "비교할 주문 후보가 없습니다.", "signals": []}
    if not is_openai_configured():
        return {"status": "not_configured", "candidate_order_id": None, "confidence": "low", "reason": "OpenAI API 키가 설정되지 않았습니다.", "signals": []}

    candidate_ids = [str(item["order_id"]) for item in candidates]
    schema = {
        "type": "object",
        "properties": {
            "candidate_order_id": {"type": "string", "enum": [""] + candidate_ids},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            "same_business_quantity": {"type": "boolean"},
            "reason": {"type": "string"},
            "matching_signals": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["candidate_order_id", "confidence", "same_business_quantity", "reason", "matching_signals"],
        "additionalProperties": False,
    }
    prompt = {
        "task": f"Recommend whether this {data_type} row belongs to one existing sales-plan order. This is a review suggestion, not an approval.",
        "source_record": source_record,
        "candidate_orders": candidates,
        "rules": [
            "Return an empty candidate_order_id when evidence is ambiguous.",
            "Do not choose from quantity equality alone or PO equality alone.",
            "Consider normalized PO, model suffix differences, sales company, target month, customer and available quantity together.",
            "A split quantity may belong to one larger sales order.",
        ],
    }
    response = _structured_response(prompt, schema, "scm_order_candidate", "OPENAI_MATCHING_MODEL")
    if response["status"] != "used":
        return {"status": response["status"], "candidate_order_id": None, "confidence": "low", "reason": response["message"], "signals": []}
    data = response["data"]
    candidate_id = data.get("candidate_order_id") or None
    if candidate_id not in candidate_ids:
        candidate_id = None
    return {
        "status": "used",
        "candidate_order_id": candidate_id,
        "confidence": data.get("confidence", "low"),
        "same_business_quantity": bool(data.get("same_business_quantity")),
        "reason": str(data.get("reason", ""))[:300],
        "signals": [str(value)[:100] for value in data.get("matching_signals", [])[:6]],
    }


def classify_row_roles(candidates: list[dict]) -> dict:
    """식별값이 비어 있는 행이 중간 합계인지 검토용으로 분류합니다.

    원본 행 전체와 실제 금액은 보내지 않습니다. 서버가 계산한 합계 일치 여부와 제한된
    텍스트 라벨만 전달하며, 최종 제외 여부는 호출자가 수학 검증과 함께 결정합니다.
    """
    if not candidates:
        return {"status": "not_needed", "message": "분류할 행이 없습니다.", "decisions": []}
    if not is_openai_configured():
        return {"status": "not_configured", "message": "OpenAI API 키가 설정되지 않았습니다.", "decisions": []}

    row_numbers = [int(item["row_number"]) for item in candidates]
    schema = {
        "type": "object",
        "properties": {
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "row_number": {"type": "integer", "enum": row_numbers},
                        "role": {"type": "string", "enum": ["subtotal", "detail", "uncertain"]},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "reason": {"type": "string"},
                    },
                    "required": ["row_number", "role", "confidence", "reason"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["decisions"],
        "additionalProperties": False,
    }
    prompt = {
        "task": "Classify each spreadsheet row as a subtotal, a real detail row, or uncertain.",
        "candidate_rows": candidates,
        "rules": [
            "A subtotal commonly omits order identifiers and repeats the sum of preceding detail rows.",
            "A split order may omit repeated labels, so do not call it subtotal from blanks alone.",
            "Prefer uncertain when the structural and arithmetic evidence is not sufficient.",
            "This classification is advisory; the server separately enforces arithmetic checks.",
        ],
    }
    response = _structured_response(prompt, schema, "scm_row_role", "OPENAI_MAPPING_MODEL")
    if response["status"] != "used":
        return {"status": response["status"], "message": response["message"], "decisions": []}
    valid_rows = set(row_numbers)
    decisions = []
    seen = set()
    for item in response["data"].get("decisions", []):
        row_number = item.get("row_number")
        if row_number not in valid_rows or row_number in seen:
            continue
        seen.add(row_number)
        decisions.append({
            "row_number": row_number,
            "role": item.get("role", "uncertain"),
            "confidence": item.get("confidence", "low"),
            "reason": str(item.get("reason", ""))[:300],
        })
    return {"status": "used", "message": "OpenAI 행 역할 분류를 받았습니다.", "decisions": decisions}
