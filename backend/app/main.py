"""FastAPI 서버의 시작점입니다.

앱 시작, 데모 데이터 보완, 주문·대시보드 API, RISK 계산, 원본 다운로드를 담당합니다.
"""

from decimal import Decimal
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from urllib.parse import quote

from alembic import command
from alembic.config import Config
from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import models
from .auth import AuthContext, require_context
from .auth_routes import router as auth_router
from .database import SessionLocal, get_session
from .schemas import CompanyCreate, DashboardOut, DemoOrderCreate, OrderOut
from .reporting import build_psi, dashboard_workbook, effective_detail_status, psi_workbook, sales_master_workbook, schedule_summary
from .seed import DEMO_REVIEW_ORDER, SOURCES, load_orders, seed_demo_data
from .upload_routes import router as upload_router
from .unstructured_routes import router as unstructured_router
from .psi_routes import router as psi_router
from .operations_routes import router as operations_router
from .order_edit_routes import router as order_edit_router

# FastAPI 앱 본체입니다. uvicorn이 app.main:app을 찾아 서버를 시작합니다.
app = FastAPI(title="SCM Shipment Control Tower API", version="0.1.0", description="합성 데모 자료용 API")
# 브라우저 개발 주소에서 API를 호출할 수 있도록 허용합니다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["Content-Type"],
)
# 기능별로 나눈 API 묶음을 하나의 앱에 연결합니다.
app.include_router(upload_router)
app.include_router(auth_router)
app.include_router(unstructured_router)
app.include_router(psi_router)
app.include_router(operations_router)
app.include_router(order_edit_router)


def available_company_condition(project_id: str):
    """기본 법인과 현재 프로젝트가 직접 추가한 법인만 조회합니다."""
    return or_(models.Company.project_id.is_(None), models.Company.project_id == project_id)


@app.on_event("startup")
def initialize_demo_database() -> None:
    """서버 시작 시 최신 DB 마이그레이션과 안전한 데모 초기화를 실행합니다."""
    backend_dir = Path(__file__).resolve().parents[1]
    alembic_config = Config(str(backend_dir / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(backend_dir / "migrations"))
    command.upgrade(alembic_config, "head")
    with SessionLocal() as session:
        seed_demo_data(session)
        upgrade_demo_seed(session)
        session.commit()


def upgrade_demo_seed(session: Session) -> None:
    """Keep existing local orders while adding newly required synthetic review cases."""
    # 사용자가 데모 프로젝트를 삭제하고 빈 실무 프로젝트만 남긴 경우 데모 주문을
    # 다시 만들지 않습니다. 프로젝트 생성·삭제 선택이 서버 재시작보다 우선합니다.
    if session.get(models.Project, "project-demo") is None:
        return
    review_order_exists = session.get(models.Order, "demo-order-009") is not None
    source = session.get(models.SourceMaterial, "source-demo-c")
    if source is None:
        source = models.SourceMaterial(**next(item for item in SOURCES if item["id"] == "source-demo-c"))
        session.add(source)

    if session.get(models.Order, "demo-order-009") is None:
        order = models.Order(**DEMO_REVIEW_ORDER)
        session.add(order)
        session.flush()
        session.add_all([
            models.ProductionAllocation(id="prod-009-a", order_id=order.id, source_id=order.source_id, quantity=3, status="검토 필요"),
            models.ShipmentAllocation(id="ship-009-a", order_id=order.id, source_id=order.source_id, quantity=3, status="검토 필요"),
        ])

    version_id = "version-sales-c-demo"
    if session.get(models.PlanVersion, version_id) is not None and session.scalar(select(models.PlanVersionLine.id).where(models.PlanVersionLine.plan_version_id == version_id, models.PlanVersionLine.order_id == "demo-order-009")) is None:
        session.add(models.PlanVersionLine(plan_version_id=version_id, order_id="demo-order-009", quantity=3))
    if review_order_exists and session.scalar(select(models.ProductionAllocation.id).where(models.ProductionAllocation.id == "prod-009-a")) is None:
        session.add(models.ProductionAllocation(id="prod-009-a", order_id="demo-order-009", source_id="source-demo-c", quantity=3, status="검토 필요"))
    if review_order_exists and session.scalar(select(models.ShipmentAllocation.id).where(models.ShipmentAllocation.id == "ship-009-a")) is None:
        session.add(models.ShipmentAllocation(id="ship-009-a", order_id="demo-order-009", source_id="source-demo-c", quantity=3, status="검토 필요"))
    session.commit()


def calculate_risk(order: models.Order, as_of_date: date | None = None) -> tuple[str, list[str], int]:
    """주문 한 건의 초기 RISK 규칙과 영향 수량을 계산합니다.

    생산·선적·ERP는 서로 다른 단계이므로 합산하지 않고 잔량의 최댓값을 사용합니다.
    """
    today = as_of_date or datetime.now(ZoneInfo("Asia/Seoul")).date()
    reasons: list[str] = []
    completed_production = schedule_summary(order, "production", today)["actual_quantity"]
    completed_shipment = schedule_summary(order, "shipment", today)["actual_quantity"]
    production_dates = [detail.production_date for detail in order.production_details if effective_detail_status(detail, "production", today) == "실제 완료" and detail.production_date]
    has_shipment_plan = any(effective_detail_status(detail, "shipment", today) in {"예정", "실제 선적"} for detail in order.shipment_details)
    target_year, target_month = map(int, order.target_month.split("-"))
    if today.year == target_year and today.month == target_month and today.day >= 15 and completed_production < order.quantity:
        reasons.append("대상 월 15일 이후 생산 미완료 잔량")
    if completed_production > completed_shipment and not has_shipment_plan and production_dates and (today - min(production_dates)).days >= 2:
        reasons.append("생산 완료 후 2일 이상 선적계획 없음")
    if completed_shipment > 0 and order.erp_status in {"미확인", "미완료 확인"}:
        reasons.append("선적 완료 물량의 ERP 매출화 미확인")
    approved_psi = [allocation for allocation in order.psi_risk_allocations if allocation.review.status == "승인"]
    for allocation in approved_psi:
        review = allocation.review
        reasons.append(f"PSI 승인: {review.risk_type} · {review.period_key} · 배분 {allocation.quantity}ea · {review.reason}")
    risk_quantity = max(order.quantity - completed_production if any("생산 미완료" in reason for reason in reasons) else 0,
                        completed_production - completed_shipment if any("선적계획" in reason for reason in reasons) else 0,
                        completed_shipment if any("ERP" in reason for reason in reasons) else 0,
                        max((allocation.quantity for allocation in approved_psi), default=0))
    return ("확인 필요" if reasons else "현재 위험 없음", reasons, risk_quantity)


def serialize_order(order: models.Order, as_of_date: date | None = None) -> dict:
    """SQLAlchemy 주문 객체를 브라우저에 보낼 JSON 형태로 바꿉니다."""
    # 예정 수량은 실적으로 세지 않고 '실제 완료/실제 선적'만 합산합니다.
    as_of_date = as_of_date or date.today()
    production_schedule = schedule_summary(order, "production", as_of_date)
    shipment_schedule = schedule_summary(order, "shipment", as_of_date)
    produced = production_schedule["actual_quantity"]
    shipped = shipment_schedule["actual_quantity"]
    # ERP 확인 자료가 여러 번 들어와도 주문 수량보다 크게 표시하지 않습니다.
    closed = min(order.quantity, sum(detail.confirmed_quantity for detail in order.erp_confirmations))
    # Different stages may describe the same units; never sum them together.
    review_quantity = max(
        sum(d.quantity for d in order.production_details if d.status == "검토 필요"),
        sum(d.quantity for d in order.shipment_details if d.status == "검토 필요"),
    )
    if order.net_sales_amount is not None:
        total_amount = order.net_sales_amount
    elif order.gross_sales_amount is not None:
        total_amount = order.gross_sales_amount
    else:
        total_amount = order.quantity * order.unit_price
    risk_status, risk_reasons, risk_quantity = calculate_risk(order, as_of_date)
    has_download = order.upload_batch_id is not None or order.source_id.startswith("source-demo-")
    return {
        "id": order.id,
        "order_number": order.order_number,
        "sales_company_id": order.sales_company_id,
        "sales_company": order.sales_company.name,
        "production_company_id": order.production_company_id,
        "production_company": order.production_company.name if order.production_company else "미매칭",
        "target_month": order.target_month,
        "po_number": order.po_number,
        "po_line_number": order.po_line_number,
        "model": order.model,
        "customer": order.customer,
        "quantity": order.quantity,
        "currency": order.currency,
        "unit_price": float(order.unit_price),
        "total_amount": float(total_amount.quantize(Decimal("0.01"))),
        "net_sales_amount": float(order.net_sales_amount) if order.net_sales_amount is not None else None,
        "gross_sales_amount": float(order.gross_sales_amount) if order.gross_sales_amount is not None else None,
        "net_sales_type": order.net_sales_type,
        "gross_sales_type": order.gross_sales_type,
        "production_site_source": order.production_site_source,
        "sales_region": order.sales_region,
        "upload_batch_id": order.upload_batch_id,
        "erp_status": order.erp_status,
        "match_status": order.match_status,
        "source_name": order.source.name,
        "source_download_url": f"/api/source-materials/{order.source_id}/download" if has_download else None,
        "source_download_label": "원본 파일",
        "production_quantity": produced,
        "shipment_quantity": shipped,
        "closed_quantity": closed,
        "production_planned_date": production_schedule["planned_date"],
        "production_next_date": production_schedule["next_date"],
        "production_planned_quantity": production_schedule["planned_quantity"],
        "production_remaining_quantity": production_schedule["remaining_quantity"],
        "production_carryover_quantity": production_schedule["carryover_quantity"],
        "production_schedule_status": production_schedule["status"],
        "shipment_planned_date": shipment_schedule["planned_date"],
        "shipment_next_date": shipment_schedule["next_date"],
        "shipment_planned_quantity": shipment_schedule["planned_quantity"],
        "shipment_remaining_quantity": shipment_schedule["remaining_quantity"],
        "shipment_carryover_quantity": shipment_schedule["carryover_quantity"],
        "shipment_schedule_status": shipment_schedule["status"],
        "review_quantity": review_quantity,
        "risk_status": risk_status,
        "risk_reasons": risk_reasons,
        "risk_quantity": risk_quantity,
        "production_details": [{"id": item.id, "quantity": item.quantity, "status": effective_detail_status(item, "production", as_of_date), "production_date": item.production_date, "is_manual": item.is_manual} for item in order.production_details],
        "shipment_details": [{"id": item.id, "quantity": item.quantity, "status": effective_detail_status(item, "shipment", as_of_date), "shipment_date": item.shipment_date, "carrier": item.carrier, "vessel_name": item.vessel_name, "is_manual": item.is_manual} for item in order.shipment_details],
        "manual_fields": order.manual_override.protected_fields if order.manual_override else [],
        "pending_update_count": sum(proposal.status == "검토 대기" for proposal in order.update_proposals),
        "is_demo": order.source_id.startswith("source-demo-"),
    }


@app.get("/health")
def health() -> dict[str, str]:
    """서버가 살아 있는지 시작 스크립트가 확인하는 가벼운 주소입니다."""
    return {"status": "ok", "mode": "synthetic demo"}


@app.get("/api/orders", response_model=list[OrderOut])
def list_orders(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> list[dict]:
    """현재 사용자가 선택한 프로젝트의 주문만 반환합니다."""
    return [serialize_order(order, context.project.as_of_date) for order in load_orders(session, context.project.id)]


@app.post("/api/orders", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_demo_order(payload: DemoOrderCreate, response: Response, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """화면 입력 데모 주문을 원본·변경 이력과 함께 저장합니다."""
    sales_company = session.get(models.Company, payload.sales_company_id)
    production_company = session.get(models.Company, payload.production_company_id)
    if sales_company is None or sales_company.kind != "판매법인":
        raise HTTPException(status_code=422, detail="판매법인을 확인해 주세요.")
    if production_company is None or production_company.kind != "생산법인":
        raise HTTPException(status_code=422, detail="생산법인을 확인해 주세요.")
    if sales_company.project_id not in {None, context.project.id} or production_company.project_id not in {None, context.project.id}:
        raise HTTPException(status_code=404, detail="현재 프로젝트에서 사용할 수 없는 법인입니다.")

    new_id = str(uuid4())
    source_id = f"src-{new_id}"
    source = models.SourceMaterial(
        id=source_id,
        name="화면 입력 데모",
        description="화면에서 등록한 합성 테스트 주문",
        received_at=datetime.now(timezone.utc).isoformat(),
        source_hash=f"demo-manual-{new_id}",
    )
    order = models.Order(
        id=new_id, project_id=context.project.id,
        order_number=f"SO-DEMO-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{new_id[:6].upper()}",
        sales_company_id=payload.sales_company_id,
        production_company_id=payload.production_company_id,
        target_month=payload.target_month,
        po_number=payload.po_number.strip(),
        model=payload.model.strip(),
        customer=payload.customer.strip(),
        quantity=payload.quantity,
        currency=payload.currency,
        unit_price=payload.unit_price,
        net_sales_amount=payload.quantity * payload.unit_price,
        gross_sales_amount=payload.quantity * payload.unit_price * Decimal("1.10"),
        net_sales_type="총액",
        gross_sales_type="총액",
        erp_status="미확인",
        match_status="연결 대기",
        source_id=source_id,
    )
    session.add_all([source, order])
    session.flush()
    session.add(models.ChangeEvent(
        id=f"evt-{new_id}",
        order_id=new_id,
        source_id=source_id,
        event_type="데모 주문 등록",
        changed_at=datetime.now(timezone.utc),
        after_value=f"주문 {payload.quantity}대 등록",
    ))
    session.commit()
    response.headers["Location"] = f"/api/orders/{new_id}"
    saved_order = next(item for item in load_orders(session, context.project.id) if item.id == new_id)
    return serialize_order(saved_order, context.project.as_of_date)


@app.post("/api/companies", status_code=status.HTTP_201_CREATED)
def create_company(payload: CompanyCreate, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """현재 프로젝트 전용 판매법인 또는 생산법인을 추가합니다."""
    name = payload.name.strip()
    country = (payload.country or name).strip() or name
    existing = session.scalar(select(models.Company).where(
        available_company_condition(context.project.id),
        models.Company.kind == payload.kind,
        func.lower(models.Company.name) == name.lower(),
    ).limit(1))
    if existing:
        raise HTTPException(status_code=409, detail="현재 프로젝트에서 사용할 수 있는 같은 이름의 법인이 이미 있습니다.")
    company = models.Company(id=str(uuid4()), name=name, kind=payload.kind, country=country, project_id=context.project.id)
    session.add(company)
    session.commit()
    return {"id": company.id, "name": company.name, "kind": company.kind, "country": company.country}


@app.get("/api/dashboard", response_model=DashboardOut)
def get_dashboard(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """현재 프로젝트의 주문 수량·금액·생산·선적·검토 현황을 요약합니다."""
    orders = load_orders(session, context.project.id)
    # 주문별로 한 번만 요약합니다. 생산/선적 테이블을 서로 직접 조인하지 않습니다.
    values_by_currency: dict[str, Decimal] = {}
    for order in orders:
        if order.net_sales_amount is not None:
            value = order.net_sales_amount
        elif order.gross_sales_amount is not None:
            value = order.gross_sales_amount
        else:
            value = order.quantity * order.unit_price
        values_by_currency[order.currency] = values_by_currency.get(order.currency, Decimal("0")) + value
    companies = session.scalars(select(models.Company).where(available_company_condition(context.project.id)).order_by(models.Company.kind.desc(), models.Company.name)).all()
    return {
        "order_count": len(orders),
        "planned_quantity": sum(order.quantity for order in orders),
        "planned_value_by_currency": {currency: round(float(value), 2) for currency, value in values_by_currency.items()},
        "produced_quantity": sum(schedule_summary(order, "production", context.project.as_of_date)["actual_quantity"] for order in orders),
        "shipped_quantity": sum(schedule_summary(order, "shipment", context.project.as_of_date)["actual_quantity"] for order in orders),
        "erp_unconfirmed_orders": sum(order.erp_status == "미확인" for order in orders),
        "review_orders": sum(
            order.match_status == "검토 필요"
            or any(detail.status == "검토 필요" for detail in order.production_details)
            or any(detail.status == "검토 필요" for detail in order.shipment_details)
            for order in orders
        ),
        "companies": [{"id": company.id, "name": company.name, "kind": company.kind, "country": company.country} for company in companies],
        "recent_orders": [serialize_order(order, context.project.as_of_date) for order in orders[:8]],
        "is_demo": True,
    }


@app.get("/api/psi")
def get_psi(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """현재 프로젝트 주문을 12개월 주차별 PSI 예시 표로 변환합니다."""
    return build_psi(load_orders(session, context.project.id))


def excel_download(content: bytes, filename: str) -> StreamingResponse:
    """브라우저가 한글 파일명으로 XLSX를 내려받게 공통 헤더를 붙입니다."""
    return StreamingResponse(iter([content]), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@app.get("/api/exports/sales-master.xlsx")
def export_sales_master(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)):
    """현재 프로젝트 Sales Master 전체 주문을 Excel로 내려줍니다."""
    return excel_download(sales_master_workbook(load_orders(session, context.project.id), context.project.as_of_date), "sales-master.xlsx")


@app.get("/api/exports/dashboard.xlsx")
def export_dashboard(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)):
    """판매법인별 매출 현황을 Excel로 내려줍니다."""
    orders = load_orders(session, context.project.id)
    companies = list(session.scalars(select(models.Company).where(available_company_condition(context.project.id)).order_by(models.Company.kind.desc(), models.Company.name)))
    return excel_download(dashboard_workbook(orders, companies, context.project.as_of_date), "sales-dashboard.xlsx")


@app.get("/api/exports/psi.xlsx")
def export_psi(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)):
    """화면과 같은 12개월 주차별 PSI 표를 Excel로 내려줍니다."""
    psi = build_psi(load_orders(session, context.project.id))
    return excel_download(psi_workbook(psi), "psi-12months.xlsx")


@app.get("/api/source-materials/{source_id}/download")
def download_source(source_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)):
    """접근 가능한 실제 원본 또는 합성 원본 XLSX를 내려줍니다.

    업로드 파일은 저장한 바이트 그대로, 초기 데모는 합성 주문으로 재구성해 반환합니다.
    """
    allowed = session.scalar(select(models.Order.id).where(models.Order.project_id == context.project.id, models.Order.source_id == source_id).limit(1))
    batch = session.scalar(select(models.UploadBatch).where(models.UploadBatch.project_id == context.project.id, models.UploadBatch.source_id == source_id))
    unstructured = session.scalar(select(models.UnstructuredInput).where(models.UnstructuredInput.project_id == context.project.id, models.UnstructuredInput.source_id == source_id))
    if allowed is None and batch is None and unstructured is None:
        raise HTTPException(status_code=404, detail="이 프로젝트에서 확인할 수 있는 원본이 아닙니다.")
    source = session.get(models.SourceMaterial, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="원본 자료를 찾지 못했습니다.")
    if batch is not None:
        content, media_type, filename = batch.file_content, "application/octet-stream", batch.file_name
    elif unstructured is not None:
        content, media_type, filename = unstructured.raw_content, unstructured.content_type, unstructured.file_name or f"{source_id}.txt"
    elif source_id.startswith("source-demo-"):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Synthetic Raw"
        sheet.append(["SO", "PO", "Model", "Customer", "Qty", "Currency", "Unit Price", "Target Month"])
        for order in load_orders(session, context.project.id):
            if order.source_id == source_id:
                sheet.append([order.order_number, order.po_number, order.model, order.customer, order.quantity, order.currency, float(order.unit_price), order.target_month])
        output = BytesIO()
        workbook.save(output)
        content, media_type, filename = output.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", source.name
    else:
        raise HTTPException(status_code=404, detail="화면 입력 자료에는 다운로드할 원본 파일이 없습니다.")
    safe_name = filename.replace('"', "")
    disposition = f"attachment; filename*=UTF-8''{quote(safe_name)}"
    return StreamingResponse(iter([content]), media_type=media_type, headers={"Content-Disposition": disposition})
