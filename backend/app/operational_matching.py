"""생산·선적정보 원본을 Sales Master 주문에 안전하게 연결합니다.

규칙으로 유일하게 확인되는 행만 자동 확정합니다. 모호한 행은 OpenAI가 후보를 추천할
수 있지만 언제나 ``검토 필요`` 연결로만 보존하며, 서버가 프로젝트 소속·수량 잔량·
생산법인 충돌·수동 우선값을 다시 검사합니다.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import InvalidOperation
import os
from uuid import uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from . import models
from .ai_mapping import suggest_order_candidate, suggest_with_ai
from .upload_service import normalize_header, parse_decimal, read_sheet


COMMON_FIELDS = {
    "po_number": {"label": "PO번호", "aliases": ["po", "pono", "po no", "po번호", "purchase order", "order no"]},
    "model": {"label": "모델명", "aliases": ["model", "model name", "모델", "모델명", "product model"]},
    "quantity": {"label": "물량", "aliases": ["qty", "quantity", "수량", "주문 수량", "order qty"]},
    "sales_company": {"label": "판매법인", "aliases": ["판매법인", "sales sub.", "sales sub", "sales company", "subsidiary"]},
    "customer": {"label": "고객명", "aliases": ["customer", "customer name", "고객", "고객명"]},
    "target_month": {"label": "대상 월", "aliases": ["month", "target month", "대상월", "대상 월", "납기월"]},
}

PRODUCTION_FIELDS = {
    **COMMON_FIELDS,
    "quantity": {"label": "생산수량", "aliases": COMMON_FIELDS["quantity"]["aliases"] + ["생산수량", "production qty"]},
    "pst": {"label": "PST/생산일", "aliases": ["pst", "pst(생산일)", "생산일", "생산예정일", "production date"]},
}

SHIPMENT_FIELDS = {
    **COMMON_FIELDS,
    "quantity": {"label": "선적수량", "aliases": COMMON_FIELDS["quantity"]["aliases"] + ["선적수량", "shipment qty", "shipping qty"]},
    "etd": {"label": "ETD/선적일", "aliases": ["etd", "etd(선적일)", "선적일", "선적예정일", "shipment date"]},
    "carrier": {"label": "선사명", "aliases": ["carrier", "shipping line", "선사", "선사명"]},
    "vessel": {"label": "선박명", "aliases": ["vessel", "vessel name", "선박", "선박명"]},
}

SALES_ALIASES = {
    "걸프": {"걸프", "gulf"}, "사우디": {"사우디", "saudi"},
    "튀르키예": {"튀르키예", "turkiye", "turkey"}, "레반트": {"레반트", "levant"},
    "이집트": {"이집트", "egypt"}, "이스라엘": {"이스라엘", "israel"},
    "남아프리카공화국": {"남아프리카공화국", "southafrica", "south africa"},
    "모로코": {"모로코", "morocco"}, "인도": {"인도", "india"},
}


def _text(value) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _identifier(value: str | None) -> str:
    return normalize_header(value).upper()


def _model(value: str | None) -> str:
    return (value or "").strip().upper()


def _model_base(value: str | None) -> str:
    return _model(value).split(".", 1)[0]


def _company_key(value: str | None) -> str:
    normalized = normalize_header(value)
    for canonical, aliases in SALES_ALIASES.items():
        if normalized in {normalize_header(item) for item in aliases | {canonical}}:
            return canonical
    return normalized


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text or normalize_header(text) in {"미정", "notyetscheduled", "tbd", "na"}:
        return None
    for candidate in (text[:10], text.replace(".", "-").replace("/", "-")[:10]):
        try:
            return date.fromisoformat(candidate)
        except ValueError:
            pass
    return None


def _field_value(values: list, mapping: dict[str, int], key: str):
    index = mapping.get(key)
    return values[index] if index is not None and index < len(values) else None


def _header_mapping(headers: list, fields: dict, use_ai: bool = False) -> tuple[dict[str, int], str]:
    normalized = [normalize_header(value) for value in headers]
    result: dict[str, int] = {}
    for key, field in fields.items():
        aliases = {normalize_header(value) for value in field["aliases"]}
        matches = [index for index, value in enumerate(normalized) if value in aliases]
        if len(matches) == 1:
            result[key] = matches[0]
    ai_status = "not_needed"
    if use_ai:
        unresolved = [
            {"key": key, "label": field["label"], "required": key in {"po_number", "model", "quantity"}}
            for key, field in fields.items() if key not in result
        ]
        ai = suggest_with_ai([str(value or "") for value in headers], unresolved, set(result.values()))
        ai_status = ai["status"]
        for key, suggestion in ai.get("suggestions", {}).items():
            result[key] = suggestion["source_index"]
    return result, ai_status


def _header_likelihood(row: list) -> int:
    values = [_text(value) for value in row if _text(value)]
    if len(values) < 2:
        return 0
    text_values = [value for value in values if value and not any(character.isdigit() for character in value)]
    unique = len({normalize_header(value) for value in values})
    return len(text_values) * 2 + unique


def read_sheet_names(batch: models.UploadBatch) -> list[str]:
    from .upload_service import inspect_workbook
    return [item["name"] for item in inspect_workbook(batch.file_content, batch.file_extension)]


def _operational_table(batch: models.UploadBatch) -> tuple[str, int, list[list], dict[str, int], str] | None:
    fields = PRODUCTION_FIELDS if batch.data_type == "생산정보" else SHIPMENT_FIELDS
    stage_words = ("생산", "production") if batch.data_type == "생산정보" else ("선적", "shipment", "shipping")
    best = None
    for sheet_name in read_sheet_names(batch):
        parsed = read_sheet(batch.file_content, batch.file_extension, sheet_name)
        rows = parsed["rows"]
        row_candidates = []
        for header_index, headers in enumerate(rows[:15]):
            mapping, _ = _header_mapping(headers, fields)
            required_score = len({"po_number", "model", "quantity"} & set(mapping))
            name_score = 4 if any(word in sheet_name.lower() for word in stage_words) else 0
            score = name_score + required_score * 5 + len(mapping) + _header_likelihood(headers)
            if required_score or _header_likelihood(headers) >= 6:
                row_candidates.append((score, header_index, headers, mapping))
        if not row_candidates:
            continue
        _, header_index, headers, mapping = max(row_candidates, key=lambda item: item[0])
        ai_status = "not_needed"
        if not {"po_number", "model", "quantity"}.issubset(mapping):
            mapping, ai_status = _header_mapping(headers, fields, use_ai=True)
        if {"po_number", "model", "quantity"}.issubset(mapping):
            score = (4 if any(word in sheet_name.lower() for word in stage_words) else 0) + len(mapping)
            candidate = (score, sheet_name, header_index, rows, mapping, ai_status)
            if best is None or candidate[0] > best[0]:
                best = candidate
    if best is None:
        return None
    _, sheet_name, header_index, rows, mapping, ai_status = best
    return sheet_name, header_index, rows, mapping, ai_status


def _stage_allocated(session: Session, order_id: str, data_type: str) -> int:
    model = models.ProductionAllocation if data_type == "생산정보" else models.ShipmentAllocation
    # AI 검토 후보는 아직 확정 배분이 아니므로 가용수량을 소모하지 않습니다.
    return int(session.scalar(select(func.coalesce(func.sum(model.quantity), 0)).where(
        model.order_id == order_id,
        model.status != "검토 필요",
    )) or 0)


def _rule_candidates(orders: list[models.Order], po: str | None, model: str | None, sales_company: str | None) -> list[models.Order]:
    candidates = orders
    signals = 0
    if po:
        candidates = [order for order in candidates if _identifier(order.po_number) == _identifier(po)]
        signals += 1
    if model:
        exact = [order for order in candidates if _model(order.model) == _model(model)]
        candidates = exact or [order for order in candidates if _model_base(order.model) == _model_base(model)]
        signals += 1
    if sales_company:
        candidates = [order for order in candidates if _company_key(order.sales_company.name) == _company_key(sales_company)]
        signals += 1
    return candidates if signals >= 2 else []


def _ai_candidate_pool(session: Session, orders: list[models.Order], batch: models.UploadBatch, po: str | None, model: str | None, sales_company: str | None, quantity: int) -> list[dict]:
    scored = []
    for order in orders:
        remaining = order.quantity - _stage_allocated(session, order.id, batch.data_type)
        if remaining <= 0:
            continue
        score = 0
        if po and _identifier(order.po_number) == _identifier(po):
            score += 5
        if model and _model(order.model) == _model(model):
            score += 5
        elif model and _model_base(order.model) == _model_base(model):
            score += 3
        if sales_company and _company_key(order.sales_company.name) == _company_key(sales_company):
            score += 3
        if order.target_month == batch.target_month:
            score += 1
        if quantity <= remaining:
            score += 1
        if score >= 3:
            scored.append((score, order, remaining))
    scored.sort(key=lambda item: (-item[0], item[1].order_number))
    return [{
        "order_id": order.id,
        "so_number": order.order_number,
        "po_number": order.po_number,
        "model": order.model,
        "customer": order.customer,
        "sales_company": order.sales_company.name,
        "target_month": order.target_month,
        "order_quantity": order.quantity,
        "available_quantity": remaining,
    } for _, order, remaining in scored[:20]]


def _safe_ai_limit() -> int:
    try:
        return max(0, min(int(os.getenv("OPENAI_MATCH_MAX_ROWS", "20")), 100))
    except ValueError:
        return 20


def _clear_source_allocations(session: Session, batch: models.UploadBatch) -> list[models.ProductionAllocation | models.ShipmentAllocation]:
    """원본을 재처리하되 이미 확정한 생산·선적 실적은 보존합니다.

    생산계획에 과거 PST가 계속 남아 있어 확정된 ``실제 완료`` 물량은 후속 파일에서
    같은 행이 빠졌다는 이유만으로 지우지 않습니다. 선적계획의 과거 ETD로 확정된
    ``실제 선적``도 같은 방식으로 보존합니다. 동일 행이 다시 들어오면 아래 매칭
    과정에서 기존 실적으로 소비하여 중복 생성하지 않습니다.
    """
    model = models.ProductionAllocation if batch.data_type == "생산정보" else models.ShipmentAllocation
    actual_status = "실제 완료" if batch.data_type == "생산정보" else "실제 선적"
    previous_order_ids = list(session.scalars(select(model.order_id).where(model.source_id == batch.source_id, model.is_manual.is_(False))))
    preserved_actuals = list(session.scalars(select(model).where(
        model.source_id == batch.source_id,
        model.is_manual.is_(False),
        model.status == actual_status,
    )))
    delete_condition = [model.source_id == batch.source_id, model.is_manual.is_(False)]
    delete_condition.append(model.status != actual_status)
    session.execute(delete(model).where(*delete_condition))
    session.flush()
    if batch.data_type != "생산정보":
        return preserved_actuals
    for order_id in set(previous_order_ids):
        order = session.get(models.Order, order_id)
        if order is None:
            continue
        remaining = session.scalar(select(models.ProductionAllocation.id).where(models.ProductionAllocation.order_id == order_id).limit(1))
        protected = set(order.manual_override.protected_fields if order.manual_override else [])
        if remaining is None and "production_company_id" not in protected:
            order.production_company_id = None
            order.production_site_source = None
    return preserved_actuals


def _validate_candidate(session: Session, batch: models.UploadBatch, order: models.Order, quantity: int) -> str | None:
    allocated = _stage_allocated(session, order.id, batch.data_type)
    if allocated + quantity > order.quantity:
        return f"주문 가용수량 초과: 주문 {order.quantity}ea, 기존 연결 {allocated}ea"
    if batch.data_type == "생산정보":
        protected = set(order.manual_override.protected_fields if order.manual_override else [])
        if order.production_company_id not in {None, batch.production_company_id}:
            return "기존 생산법인과 충돌"
        if "production_company_id" in protected and order.production_company_id != batch.production_company_id:
            return "수동 저장한 생산법인 우선"
    return None


def _add_allocation(session: Session, batch: models.UploadBatch, order: models.Order, quantity: int, values: list, mapping: dict[str, int], review: bool) -> None:
    if batch.data_type == "생산정보":
        production_date = _date(_field_value(values, mapping, "pst"))
        # 현업 생산계획에는 별도 완료 플래그가 없으므로, 자료 기준일보다 과거인 PST가
        # 최신 파일에 계속 기재된 확정 매칭 행은 해당 수량이 생산된 것으로 봅니다.
        status = "검토 필요" if review else "실제 완료" if production_date and production_date < batch.reference_date else "예정"
        if not review:
            order.production_company_id = batch.production_company_id
            order.production_site_source = batch.production_company.name if batch.production_company else None
            if order.match_status in {"판매자료 반영", "미연결", "연결 대기"}:
                order.match_status = "생산 실적 연결" if status == "실제 완료" else "생산 예정 연결"
        else:
            order.match_status = "검토 필요"
        session.add(models.ProductionAllocation(
            id=str(uuid4()), order_id=order.id, source_id=batch.source_id,
            quantity=quantity, status=status, production_date=production_date,
            is_demo=False, is_manual=False,
        ))
    else:
        shipment_date = _date(_field_value(values, mapping, "etd"))
        # 선적계획도 별도 완료 플래그가 없으므로, 자료 기준일보다 과거인 ETD가
        # 최신 파일에 계속 기재된 확정 매칭 행은 해당 수량이 선적된 것으로 봅니다.
        status = "검토 필요" if review else "실제 선적" if shipment_date and shipment_date < batch.reference_date else "예정"
        if review:
            order.match_status = "검토 필요"
        elif order.match_status in {"판매자료 반영", "미연결", "연결 대기", "생산 예정 연결"}:
            order.match_status = "선적 실적 연결" if status == "실제 선적" else "선적 예정 연결"
        session.add(models.ShipmentAllocation(
            id=str(uuid4()), order_id=order.id, source_id=batch.source_id,
            quantity=quantity, status=status,
            shipment_date=shipment_date,
            eta_date=None,
            carrier=_text(_field_value(values, mapping, "carrier")),
            vessel_name=_text(_field_value(values, mapping, "vessel")),
            is_demo=False, is_manual=False,
        ))


def match_operational_batch(session: Session, batch: models.UploadBatch) -> dict:
    """생산 또는 선적 파일 한 건을 규칙 우선·AI 검토 보조 방식으로 연결합니다."""
    empty = {"matched_rows": 0, "review_rows": 0, "ai_suggested_rows": 0, "ignored_rows": 0, "ai_status": "not_needed"}
    if batch.data_type not in {"생산정보", "선적정보"} or not batch.production_company_id:
        return empty

    preserved_actuals = _clear_source_allocations(session, batch)
    consumed_actual_ids: set[str] = set()
    session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id == batch.id))
    table = _operational_table(batch)
    if table is None:
        batch.result_message = f"{batch.data_type}에서 PO·모델·수량 헤더를 찾지 못했습니다. 컬럼명 확인이 필요합니다."
        return empty

    sheet_name, header_index, rows, mapping, header_ai_status = table
    orders = list(session.scalars(select(models.Order).where(models.Order.project_id == batch.project_id)))
    matched = review = ai_suggested = ignored = ai_attempted = 0
    ai_limit = _safe_ai_limit()
    for row_number, values in enumerate(rows[header_index + 1:], start=header_index + 2):
        po = _text(_field_value(values, mapping, "po_number"))
        model = _text(_field_value(values, mapping, "model"))
        sales_value = _text(_field_value(values, mapping, "sales_company"))
        customer = _text(_field_value(values, mapping, "customer"))
        raw_quantity = _field_value(values, mapping, "quantity")
        if not po and not model and raw_quantity in {None, ""}:
            continue
        issues: list[str] = []
        try:
            parsed_quantity = parse_decimal(raw_quantity)
            if parsed_quantity is None or parsed_quantity <= 0 or parsed_quantity != parsed_quantity.to_integral_value():
                raise InvalidOperation
            quantity = int(parsed_quantity)
        except (InvalidOperation, ValueError, TypeError):
            quantity = None
            issues.append("물량 형식 확인 필요")

        candidates = _rule_candidates(orders, po, model, sales_value) if quantity else []
        order = candidates[0] if len(candidates) == 1 else None
        review_link = False
        preserved_actual = None
        if order and quantity:
            date_key = "pst" if batch.data_type == "생산정보" else "etd"
            date_field = "production_date" if batch.data_type == "생산정보" else "shipment_date"
            stage_date = _date(_field_value(values, mapping, date_key))
            preserved_actual = next((item for item in preserved_actuals if
                item.id not in consumed_actual_ids and item.order_id == order.id and
                item.quantity == quantity and getattr(item, date_field) == stage_date
            ), None)
            if preserved_actual is not None:
                consumed_actual_ids.add(preserved_actual.id)
            else:
                conflict = _validate_candidate(session, batch, order, quantity)
                if conflict:
                    issues.append(conflict)
                    order = None
        elif quantity and ai_attempted < ai_limit:
            pool = _ai_candidate_pool(session, orders, batch, po, model, sales_value, quantity)
            if pool:
                ai_attempted += 1
                ai = suggest_order_candidate({
                    "po_number": po, "model": model, "customer": customer,
                    "sales_company": sales_value, "target_month": _text(_field_value(values, mapping, "target_month")) or batch.target_month,
                    "quantity": quantity,
                }, pool, batch.data_type)
                if ai["status"] == "used" and ai["candidate_order_id"] and ai["confidence"] in {"high", "medium"}:
                    suggested = session.get(models.Order, ai["candidate_order_id"])
                    conflict = _validate_candidate(session, batch, suggested, quantity) if suggested and suggested.project_id == batch.project_id else "프로젝트 후보 확인 실패"
                    if suggested and not conflict:
                        order = suggested
                        review_link = True
                        ai_suggested += 1
                        issues.append(f"AI 검토 후보: {suggested.order_number} · {ai['confidence']} · {ai['reason']}")
                    elif conflict:
                        issues.append(f"AI 후보 보류: {conflict}")
                elif ai["status"] == "used":
                    issues.append("AI도 단일 후보를 확정하지 못함")

        if order and quantity:
            if preserved_actual is None:
                _add_allocation(session, batch, order, quantity, values, mapping, review_link)
            elif batch.data_type == "생산정보":
                order.production_company_id = batch.production_company_id
                order.production_site_source = batch.production_company.name if batch.production_company else None
                if order.match_status in {"판매자료 반영", "미연결", "연결 대기", "생산 예정 연결"}:
                    order.match_status = "생산 실적 연결"
            elif order.match_status in {"판매자료 반영", "미연결", "연결 대기", "생산 예정 연결", "선적 예정 연결"}:
                order.match_status = "선적 실적 연결"
            if review_link:
                status = "검토"
                review += 1
            else:
                status = "처리"
                matched += 1
        else:
            status = "검토"
            review += 1
            if not issues:
                issues.append("일치 주문 없음" if not candidates else "일치 주문이 여러 개라 자동 확정 보류")
        session.add(models.UploadRawRow(
            batch_id=batch.id, sheet_name=sheet_name, row_number=row_number,
            raw_values=list(values), formula_missing_columns=[], merged_columns=[],
            status=status, issues=issues,
        ))

    batch.selected_sheet = sheet_name
    batch.header_row = header_index + 1
    ai_status = "used" if header_ai_status == "used" or ai_suggested else header_ai_status
    batch.result_message = f"{batch.data_type} 자동 연결 {matched}행 · AI 검토 후보 {ai_suggested}행 · 검토 필요 {review}행"
    return {"matched_rows": matched, "review_rows": review, "ai_suggested_rows": ai_suggested, "ignored_rows": ignored, "ai_status": ai_status}


def match_production_batch(session: Session, batch: models.UploadBatch) -> dict:
    """기존 호출부와 테스트를 위한 생산정보 전용 호환 함수입니다."""
    return match_operational_batch(session, batch)


def match_all_operational_batches(session: Session, project_id: str) -> dict:
    totals = {"matched_rows": 0, "review_rows": 0, "ai_suggested_rows": 0, "ignored_rows": 0}
    batches = session.scalars(select(models.UploadBatch).where(
        models.UploadBatch.project_id == project_id,
        models.UploadBatch.data_type.in_(["생산정보", "선적정보"]),
    ).order_by(models.UploadBatch.created_at)).all()
    for batch in batches:
        result = match_operational_batch(session, batch)
        for key in totals:
            totals[key] += int(result.get(key, 0))
    unmatched_orders = session.scalars(select(models.Order).where(
        models.Order.project_id == project_id,
        models.Order.production_company_id.is_(None),
        models.Order.match_status == "판매자료 반영",
    )).all()
    for order in unmatched_orders:
        order.match_status = "검토 필요"
    return totals


def match_all_production_batches(session: Session, project_id: str) -> dict:
    """이전 이름을 유지하되 이제 생산·선적 배치를 모두 재처리합니다."""
    return match_all_operational_batches(session, project_id)
