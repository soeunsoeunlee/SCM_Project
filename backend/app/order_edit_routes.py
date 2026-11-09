"""Sales Master 수동 수정과 새 파일 변경 제안 검토 API입니다.

수동 저장값은 자동 업로드보다 우선합니다. 새 파일 값은 주문을 바로 바꾸지 않고
OrderUpdateProposal에 보관하며, 사용자가 선택한 필드만 명시적으로 반영합니다.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import AuthContext, require_context
from .database import get_session


router = APIRouter(prefix="/api", tags=["sales master editing"])

CORE_FIELDS = {
    "order_number", "sales_company_id", "production_company_id", "target_month", "po_number",
    "po_line_number", "model", "customer", "quantity", "net_sales_amount",
    "gross_sales_amount", "production_site_source", "sales_region", "match_status",
}
PRODUCTION_STATUSES = {"예정", "실제 완료", "검토 필요"}
SHIPMENT_STATUSES = {"예정", "실제 선적", "검토 필요"}


class ProductionEdit(BaseModel):
    id: str | None = None
    quantity: int = Field(gt=0, le=1_000_000)
    status: str
    production_date: date | None = None


class ShipmentEdit(BaseModel):
    id: str | None = None
    quantity: int = Field(gt=0, le=1_000_000)
    status: str
    shipment_date: date | None = None
    carrier: str | None = Field(default=None, max_length=100)
    vessel_name: str | None = Field(default=None, max_length=120)


class OrderEdit(BaseModel):
    order_number: str = Field(min_length=1, max_length=40)
    sales_company_id: str
    production_company_id: str
    target_month: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    po_number: str = Field(min_length=1, max_length=80)
    po_line_number: str | None = Field(default=None, max_length=80)
    model: str = Field(min_length=1, max_length=100)
    customer: str = Field(min_length=1, max_length=120)
    quantity: int = Field(gt=0, le=1_000_000)
    net_sales_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    gross_sales_amount: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    production_site_source: str | None = Field(default=None, max_length=120)
    sales_region: str | None = Field(default=None, max_length=120)
    match_status: str = Field(min_length=1, max_length=40)
    production_details: list[ProductionEdit] = Field(default_factory=list)
    shipment_details: list[ShipmentEdit] = Field(default_factory=list)

    @field_validator("order_number", "po_number", "model", "customer", "match_status")
    @classmethod
    def strip_required(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("공백만 입력할 수 없습니다.")
        return value


class ProposalDecision(BaseModel):
    fields: list[str]


def _clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return value


def _manual_source(session: Session, order: models.Order, user: models.User) -> models.SourceMaterial:
    source_id = f"mn-{uuid4().hex}"
    now = datetime.now(timezone.utc)
    source = models.SourceMaterial(
        id=source_id,
        name="Sales Master 수동 수정",
        description=f"{order.order_number} · {user.display_name} 수동 저장",
        received_at=now.isoformat(),
        source_hash=f"manual-order-{uuid4().hex}",
    )
    session.add(source)
    return source


def _validate_stage_totals(order: models.Order, quantity: int) -> None:
    produced = sum(row.quantity for row in order.production_details if row.status == "실제 완료")
    production_planned = sum(row.quantity for row in order.production_details if row.status == "예정")
    shipped = sum(row.quantity for row in order.shipment_details if row.status == "실제 선적")
    shipment_planned = sum(row.quantity for row in order.shipment_details if row.status == "예정")
    closed = sum(row.confirmed_quantity for row in order.erp_confirmations)
    if produced > quantity or produced + production_planned > quantity:
        raise HTTPException(status_code=422, detail="생산 완료와 생산 예정 수량 합계가 주문 수량을 초과합니다.")
    if shipped > quantity or shipped + shipment_planned > quantity:
        raise HTTPException(status_code=422, detail="선적 완료와 선적 예정 수량 합계가 주문 수량을 초과합니다.")
    if closed > quantity:
        raise HTTPException(status_code=422, detail="주문 수량을 기존 ERP Closed 수량보다 작게 저장할 수 없습니다.")


def _upsert_production(session: Session, order: models.Order, rows: list[ProductionEdit], source_id: str) -> None:
    existing = {row.id: row for row in order.production_details}
    for item in rows:
        if item.status not in PRODUCTION_STATUSES:
            raise HTTPException(status_code=422, detail="생산 상태는 예정, 실제 완료, 검토 필요 중에서 선택해 주세요.")
        if item.status == "실제 완료" and item.production_date is None:
            raise HTTPException(status_code=422, detail="실제 생산 완료로 확정하려면 생산 완료일을 입력해 주세요.")
        if item.id:
            row = existing.get(item.id)
            if row is None:
                raise HTTPException(status_code=404, detail="수정할 생산 상세를 찾을 수 없습니다.")
            row.quantity, row.status, row.production_date, row.is_manual = item.quantity, item.status, item.production_date, True
        else:
            session.add(models.ProductionAllocation(
                id=str(uuid4()), order_id=order.id, source_id=source_id,
                quantity=item.quantity, status=item.status, production_date=item.production_date,
                is_demo=False, is_manual=True,
            ))
    session.flush()


def _upsert_shipment(session: Session, order: models.Order, rows: list[ShipmentEdit], source_id: str) -> None:
    existing = {row.id: row for row in order.shipment_details}
    for item in rows:
        if item.status not in SHIPMENT_STATUSES:
            raise HTTPException(status_code=422, detail="선적 상태는 예정, 실제 선적, 검토 필요 중에서 선택해 주세요.")
        if item.status == "실제 선적" and item.shipment_date is None:
            raise HTTPException(status_code=422, detail="실제 선적으로 확정하려면 실제 선적일을 입력해 주세요.")
        if item.id:
            row = existing.get(item.id)
            if row is None:
                raise HTTPException(status_code=404, detail="수정할 선적 상세를 찾을 수 없습니다.")
            row.quantity, row.status = item.quantity, item.status
            # ETA는 현재 업무 화면에서 사용하지 않습니다. 기존 DB 값은 과거 이력 보존을 위해
            # 건드리지 않고, 사용자가 수정할 수 있는 ETD만 갱신합니다.
            row.shipment_date = item.shipment_date
            row.carrier, row.vessel_name, row.is_manual = item.carrier or None, item.vessel_name or None, True
        else:
            session.add(models.ShipmentAllocation(
                id=str(uuid4()), order_id=order.id, source_id=source_id,
                quantity=item.quantity, status=item.status, shipment_date=item.shipment_date,
                eta_date=None, carrier=item.carrier or None, vessel_name=item.vessel_name or None,
                is_demo=False, is_manual=True,
            ))
    session.flush()


@router.patch("/orders/{order_id}")
def edit_order(order_id: str, payload: OrderEdit, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """주문과 생산·선적 상세를 수동 저장하고 해당 필드를 자동 덮어쓰기에서 보호합니다."""
    try:
        order = session.scalar(select(models.Order).where(models.Order.id == order_id, models.Order.project_id == context.project.id).with_for_update())
        if order is None:
            raise HTTPException(status_code=404, detail="수정할 주문을 찾을 수 없습니다.")
        sales = session.get(models.Company, payload.sales_company_id)
        plant = session.get(models.Company, payload.production_company_id)
        if sales is None or sales.kind != "판매법인" or plant is None or plant.kind != "생산법인":
            raise HTTPException(status_code=422, detail="판매법인 또는 생산법인을 확인해 주세요.")
        if sales.project_id not in {None, context.project.id} or plant.project_id not in {None, context.project.id}:
            raise HTTPException(status_code=404, detail="현재 프로젝트에서 사용할 수 없는 법인입니다.")

        before = {field: _clean(getattr(order, field)) for field in CORE_FIELDS}
        source = _manual_source(session, order, context.user)
        for field in CORE_FIELDS:
            value = getattr(payload, field)
            if isinstance(value, str):
                value = value.strip() or None
            setattr(order, field, value)
        order.currency = "USD"
        if payload.net_sales_amount is not None:
            order.unit_price = payload.net_sales_amount / payload.quantity
        elif payload.gross_sales_amount is not None:
            order.unit_price = payload.gross_sales_amount / payload.quantity

        _upsert_production(session, order, payload.production_details, source.id)
        _upsert_shipment(session, order, payload.shipment_details, source.id)
        session.refresh(order, ["production_details", "shipment_details", "erp_confirmations"])
        _validate_stage_totals(order, payload.quantity)

        protected = set(CORE_FIELDS) | {"production_details", "shipment_details"}
        override = session.get(models.OrderManualOverride, order.id)
        if override is None:
            override = models.OrderManualOverride(order_id=order.id, project_id=context.project.id, protected_fields=sorted(protected), updated_by_user_id=context.user.id)
            session.add(override)
        else:
            override.protected_fields = sorted(set(override.protected_fields) | protected)
            override.updated_by_user_id = context.user.id
            override.updated_at = datetime.now(timezone.utc)
        after = {field: _clean(getattr(order, field)) for field in CORE_FIELDS}
        session.add(models.ChangeEvent(
            id=f"me-{uuid4().hex}", order_id=order.id, source_id=source.id,
            event_type="Sales Master 수동 수정", changed_at=datetime.now(timezone.utc),
            before_value=json.dumps(before, ensure_ascii=False)[:500],
            after_value=json.dumps(after, ensure_ascii=False)[:500],
        ))
        session.commit()
        return {"message": "수동 수정값을 저장했습니다. 새 파일보다 이 값이 우선합니다.", "order_id": order.id, "protected_fields": override.protected_fields}
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise


@router.get("/orders/{order_id}/update-proposals")
def list_update_proposals(order_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> list[dict]:
    order = session.scalar(select(models.Order).where(models.Order.id == order_id, models.Order.project_id == context.project.id))
    if order is None:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")
    rows = session.scalars(select(models.OrderUpdateProposal).where(
        models.OrderUpdateProposal.order_id == order_id,
        models.OrderUpdateProposal.project_id == context.project.id,
        models.OrderUpdateProposal.status == "검토 대기",
    ).order_by(models.OrderUpdateProposal.created_at.desc())).all()
    return [{
        "id": row.id, "order_id": row.order_id, "status": row.status,
        "source_name": session.get(models.SourceMaterial, row.source_id).name,
        "differences": row.differences, "created_at": row.created_at.isoformat(),
        "match_basis": row.incoming_values.get("_ai_match_basis"),
    } for row in rows]


@router.post("/order-update-proposals/{proposal_id}/apply")
def apply_update_proposal(proposal_id: str, payload: ProposalDecision, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """사용자가 체크한 새 파일 값만 주문에 반영하고 나머지는 수동값으로 유지합니다."""
    try:
        proposal = session.scalar(select(models.OrderUpdateProposal).where(
            models.OrderUpdateProposal.id == proposal_id,
            models.OrderUpdateProposal.project_id == context.project.id,
        ).with_for_update())
        if proposal is None:
            raise HTTPException(status_code=404, detail="업데이트 검토 항목을 찾을 수 없습니다.")
        if proposal.status != "검토 대기":
            return {"message": "이미 처리된 업데이트 검토 항목입니다.", "applied_fields": []}
        order = session.scalar(select(models.Order).where(models.Order.id == proposal.order_id, models.Order.project_id == context.project.id).with_for_update())
        requested = [field for field in payload.fields if field in proposal.differences and field in CORE_FIELDS]
        if not requested:
            raise HTTPException(status_code=422, detail="반영할 변경 항목을 한 개 이상 선택해 주세요.")
        before = {field: _clean(getattr(order, field)) for field in requested}
        for field in requested:
            setattr(order, field, proposal.incoming_values.get(field))
        if "quantity" in requested:
            session.refresh(order, ["production_details", "shipment_details", "erp_confirmations"])
            _validate_stage_totals(order, order.quantity)
        if "net_sales_amount" in requested and order.net_sales_amount is not None:
            order.unit_price = order.net_sales_amount / order.quantity
        remaining = {key: value for key, value in proposal.differences.items() if key not in requested}
        proposal.differences = remaining
        proposal.status = "적용 완료" if not remaining else "검토 대기"
        proposal.resolved_by_user_id = context.user.id
        proposal.resolved_at = datetime.now(timezone.utc)
        override = session.get(models.OrderManualOverride, order.id)
        if override:
            override.protected_fields = [field for field in override.protected_fields if field not in requested]
        session.add(models.ChangeEvent(
            id=f"pe-{uuid4().hex}", order_id=order.id, source_id=proposal.source_id,
            event_type="업로드 변경 선택 반영", changed_at=datetime.now(timezone.utc),
            before_value=json.dumps(before, ensure_ascii=False)[:500],
            after_value=json.dumps({field: proposal.incoming_values.get(field) for field in requested}, ensure_ascii=False)[:500],
        ))
        session.commit()
        return {"message": f"선택한 {len(requested)}개 항목을 새 파일 값으로 업데이트했습니다.", "applied_fields": requested}
    except HTTPException:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
