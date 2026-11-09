"""처음 실행할 때 넣는 합성 법인·주문·생산·선적 데모 데이터입니다.

실제 고객 자료가 아니며, 기준 법인이 이미 있으면 사용자가 주문 0건의 빈 프로젝트를
의도적으로 만든 상태일 수 있으므로 데모 주문을 다시 채우지 않습니다.
"""

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import models


# 화면에서 선택할 판매법인 9곳과 생산법인 6곳입니다.
COMPANIES = [
    {"id": "sales-a", "name": "걸프", "kind": "판매법인", "country": "중동"},
    {"id": "sales-b", "name": "사우디", "kind": "판매법인", "country": "사우디아라비아"},
    {"id": "sales-c", "name": "튀르키예", "kind": "판매법인", "country": "튀르키예"},
    {"id": "sales-d", "name": "레반트", "kind": "판매법인", "country": "레반트"},
    {"id": "sales-e", "name": "이집트", "kind": "판매법인", "country": "이집트"},
    {"id": "sales-f", "name": "이스라엘", "kind": "판매법인", "country": "이스라엘"},
    {"id": "sales-g", "name": "남아프리카공화국", "kind": "판매법인", "country": "남아프리카공화국"},
    {"id": "sales-h", "name": "모로코", "kind": "판매법인", "country": "모로코"},
    {"id": "sales-i", "name": "인도", "kind": "판매법인", "country": "인도"},
    {"id": "plant-a", "name": "구미", "kind": "생산법인", "country": "대한민국"},
    {"id": "plant-b", "name": "이집트", "kind": "생산법인", "country": "이집트"},
    {"id": "plant-c", "name": "인도네시아", "kind": "생산법인", "country": "인도네시아"},
    {"id": "plant-d", "name": "베트남", "kind": "생산법인", "country": "베트남"},
    {"id": "plant-e", "name": "중국", "kind": "생산법인", "country": "중국"},
    {"id": "plant-f", "name": "폴란드", "kind": "생산법인", "country": "폴란드"},
]

# 데모 주문의 합성 원본 목록입니다.
SOURCES = [
    {"id": "source-demo-a", "name": "gulf-plan-demo.xlsx", "description": "걸프 판매계획 합성 예시", "received_at": "2026-10-28T09:00:00+09:00", "source_hash": "demo-source-hash-a"},
    {"id": "source-demo-b", "name": "saudi-plan-demo.xlsx", "description": "사우디 판매계획 합성 예시", "received_at": "2026-10-28T09:10:00+09:00", "source_hash": "demo-source-hash-b"},
    {"id": "source-demo-c", "name": "turkiye-plan-demo.xlsx", "description": "튀르키예 판매계획 합성 예시", "received_at": "2026-10-28T09:20:00+09:00", "source_hash": "demo-source-hash-c"},
    {"id": "source-manual-demo", "name": "화면 입력 데모", "description": "브라우저에서 직접 등록한 합성 테스트 주문", "received_at": "2026-10-28T09:30:00+09:00", "source_hash": "demo-source-manual"},
]

# 기존 DB에도 안전하게 추가할 수 있도록 분리한 매칭 검토 사례입니다.
DEMO_REVIEW_ORDER = {
    "id": "demo-order-009",
    "order_number": "SO-2026-009",
    "sales_company_id": "sales-c",
    "production_company_id": "plant-b",
    "target_month": "2026-12",
    "po_number": "PO-610255",
    "model": "OLED83-Z2",
    "customer": "Eastwind Commerce",
    "quantity": 3,
    "currency": "USD",
    "unit_price": 900,
    "erp_status": "미확인",
    "match_status": "미생산",
    "source_id": "source-demo-c",
}

# 미생산·부분 생산·부분 선적·ERP 미확인 등 화면 확인용 주문입니다.
ORDERS = [
    {"id": "demo-order-001", "order_number": "SO-2026-001", "sales_company_id": "sales-a", "production_company_id": "plant-b", "target_month": "2026-11", "po_number": "00047120", "model": "OLED77-C4", "customer": "Harbor Retail", "quantity": 20, "currency": "USD", "unit_price": 1280, "erp_status": "미확인", "match_status": "확정된 데모 연결", "source_id": "source-demo-a"},
    {"id": "demo-order-002", "order_number": "SO-2026-002", "sales_company_id": "sales-a", "production_company_id": "plant-a", "target_month": "2026-11", "po_number": "PO-885102", "model": "QNED65-X9", "customer": "Pacific Home", "quantity": 12, "currency": "USD", "unit_price": 840, "erp_status": "미확인", "match_status": "미연결", "source_id": "source-demo-a"},
    {"id": "demo-order-003", "order_number": "SO-2026-003", "sales_company_id": "sales-a", "production_company_id": "plant-c", "target_month": "2026-11", "po_number": "PO-410076", "model": "WASH-F12", "customer": "Sunline Stores", "quantity": 10, "currency": "USD", "unit_price": 610, "erp_status": "미확인", "match_status": "부분 진행", "source_id": "source-demo-a"},
    {"id": "demo-order-004", "order_number": "SO-2026-004", "sales_company_id": "sales-b", "production_company_id": "plant-b", "target_month": "2026-12", "po_number": "PO-730011", "model": "FRIDGE-G8", "customer": "Morgen Markt", "quantity": 8, "currency": "USD", "unit_price": 1020, "erp_status": "미확인", "match_status": "검토 필요", "source_id": "source-demo-b"},
    {"id": "demo-order-005", "order_number": "SO-2026-005", "sales_company_id": "sales-b", "production_company_id": "plant-a", "target_month": "2026-12", "po_number": "00001209", "model": "TV55-U7", "customer": "Nordlicht GmbH", "quantity": 8, "currency": "USD", "unit_price": 640, "erp_status": "미확인", "match_status": "생산 완료", "source_id": "source-demo-b"},
    {"id": "demo-order-006", "order_number": "SO-2026-006", "sales_company_id": "sales-b", "production_company_id": "plant-c", "target_month": "2026-11", "po_number": "PO-560120", "model": "AC18-SMART", "customer": "Blue Harbor GmbH", "quantity": 12, "currency": "USD", "unit_price": 470, "erp_status": "미확인", "match_status": "일부 선적", "source_id": "source-demo-b"},
    {"id": "demo-order-007", "order_number": "SO-2026-007", "sales_company_id": "sales-c", "production_company_id": "plant-c", "target_month": "2026-10", "po_number": "PO-008830", "model": "WASH-A9", "customer": "Lotus Living", "quantity": 15, "currency": "USD", "unit_price": 410, "erp_status": "일부 확인", "match_status": "선적 완료", "source_id": "source-demo-c"},
    {"id": "demo-order-008", "order_number": "SO-2026-008", "sales_company_id": "sales-c", "production_company_id": "plant-a", "target_month": "2026-11", "po_number": "PO-250140", "model": "FRIDGE-M6", "customer": "Green Leaf Trading", "quantity": 5, "currency": "USD", "unit_price": 500, "erp_status": "미확인", "match_status": "생산 계획 확인", "source_id": "source-demo-c"},
    {"id": "demo-order-009", "order_number": "SO-2026-009", "sales_company_id": "sales-c", "production_company_id": "plant-b", "target_month": "2026-12", "po_number": "PO-610255", "model": "OLED83-Z2", "customer": "Eastwind Commerce", "quantity": 3, "currency": "USD", "unit_price": 900, "erp_status": "미확인", "match_status": "미생산", "source_id": "source-demo-c"},
]


def seed_demo_data(session: Session) -> None:
    """DB가 비어 있을 때만 전체 데모 자료를 한 번 저장합니다."""
    # 주문 건수로 판단하면 사용자가 모든 기존 프로젝트를 지우고 만든 빈 프로젝트에
    # 데모가 되살아납니다. 기준 법인 존재 여부를 최초 초기화의 안정된 표지로 사용합니다.
    if session.scalar(select(models.Company.id).limit(1)):
        return

    session.add_all([models.Company(**company) for company in COMPANIES])
    session.add_all([models.SourceMaterial(**source) for source in SOURCES])
    session.flush()

    orders = [models.Order(**order) for order in ORDERS]
    session.add_all(orders)
    session.flush()

    # 주문 하나에 생산 상세가 여러 건 연결되는 예시입니다(20 = 15 + 5 포함).
    allocations = [
        models.ProductionAllocation(id="prod-001-a", order_id="demo-order-001", source_id="source-demo-a", quantity=15, status="실제 완료", production_date=date(2026, 10, 23)),
        models.ProductionAllocation(id="prod-001-b", order_id="demo-order-001", source_id="source-demo-a", quantity=5, status="실제 완료", production_date=date(2026, 10, 27)),
        models.ProductionAllocation(id="prod-003-a", order_id="demo-order-003", source_id="source-demo-a", quantity=6, status="실제 완료", production_date=date(2026, 10, 26)),
        models.ProductionAllocation(id="prod-003-b", order_id="demo-order-003", source_id="source-demo-a", quantity=4, status="예정", production_date=date(2026, 10, 31)),
        models.ProductionAllocation(id="prod-005-a", order_id="demo-order-005", source_id="source-demo-b", quantity=8, status="실제 완료", production_date=date(2026, 10, 25)),
        models.ProductionAllocation(id="prod-006-a", order_id="demo-order-006", source_id="source-demo-b", quantity=12, status="실제 완료", production_date=date(2026, 10, 25)),
        models.ProductionAllocation(id="prod-007-a", order_id="demo-order-007", source_id="source-demo-c", quantity=15, status="실제 완료", production_date=date(2026, 10, 8)),
        models.ProductionAllocation(id="prod-008-a", order_id="demo-order-008", source_id="source-demo-c", quantity=5, status="실제 완료", production_date=date(2026, 10, 27)),
        models.ProductionAllocation(id="prod-009-a", order_id="demo-order-009", source_id="source-demo-c", quantity=3, status="검토 필요", production_date=None),
    ]
    # 생산과 별도로 선적 상세를 저장합니다(20 = 10 + 10 포함).
    shipments = [
        models.ShipmentAllocation(id="ship-001-a", order_id="demo-order-001", source_id="source-demo-a", quantity=10, status="실제 선적", shipment_date=date(2026, 10, 25), eta_date=date(2026, 11, 8), carrier="Maersk", vessel_name="MAERSK SANA"),
        models.ShipmentAllocation(id="ship-001-b", order_id="demo-order-001", source_id="source-demo-a", quantity=10, status="실제 선적", shipment_date=date(2026, 10, 28), eta_date=date(2026, 11, 12), carrier="Maersk", vessel_name="MAERSK SANA"),
        models.ShipmentAllocation(id="ship-003-a", order_id="demo-order-003", source_id="source-demo-a", quantity=4, status="실제 선적", shipment_date=date(2026, 10, 28), eta_date=date(2026, 11, 15), carrier="Hapag-Lloyd", vessel_name="AL MASHRAB"),
        models.ShipmentAllocation(id="ship-003-b", order_id="demo-order-003", source_id="source-demo-a", quantity=4, status="예정", shipment_date=date(2026, 11, 3), eta_date=date(2026, 11, 20), carrier="Hapag-Lloyd", vessel_name="AL MASHRAB"),
        models.ShipmentAllocation(id="ship-006-a", order_id="demo-order-006", source_id="source-demo-b", quantity=6, status="실제 선적", shipment_date=date(2026, 10, 27), eta_date=date(2026, 11, 6), carrier="MSC", vessel_name="MSC RANIA"),
        models.ShipmentAllocation(id="ship-007-a", order_id="demo-order-007", source_id="source-demo-c", quantity=15, status="실제 선적", shipment_date=date(2026, 10, 15), eta_date=date(2026, 10, 30), carrier="CMA CGM", vessel_name="CMA CGM TOSCA"),
        models.ShipmentAllocation(id="ship-009-a", order_id="demo-order-009", source_id="source-demo-c", quantity=3, status="검토 필요", shipment_date=None, carrier=None),
    ]
    session.add_all(allocations + shipments)
    session.add(models.ErpConfirmation(id="erp-007-a", order_id="demo-order-007", status="일부 완료", confirmed_quantity=10, confirmation_date=date(2026, 10, 20), source_id="source-demo-c"))

    for company_id, source_id in (("sales-a", "source-demo-a"), ("sales-b", "source-demo-b"), ("sales-c", "source-demo-c")):
        order_ids = [order.id for order in orders if order.sales_company_id == company_id]
        version_id = f"version-{company_id}-demo"
        session.add(models.PlanVersion(id=version_id, source_id=source_id, sales_company_id=company_id, reference_date="2026-10-28", scope_type="전체 현황"))
        session.flush()
        session.add_all([models.PlanVersionLine(plan_version_id=version_id, order_id=order_id, quantity=next(order.quantity for order in orders if order.id == order_id)) for order_id in order_ids])

    session.commit()


def load_orders(session: Session, project_id: str | None = None) -> list[models.Order]:
    """주문과 화면에 필요한 관계 데이터를 함께 조회합니다."""
    query = select(models.Order)
    if project_id is not None:
        query = query.where(models.Order.project_id == project_id)
    return list(session.scalars(query.options(
        selectinload(models.Order.sales_company),
        selectinload(models.Order.production_company),
        selectinload(models.Order.source),
        selectinload(models.Order.production_details),
        selectinload(models.Order.shipment_details),
        selectinload(models.Order.erp_confirmations),
        selectinload(models.Order.psi_risk_reviews),
        selectinload(models.Order.psi_risk_allocations).selectinload(models.PsiRiskAllocation.review),
        selectinload(models.Order.manual_override),
        selectinload(models.Order.update_proposals),
    ).order_by(models.Order.target_month, models.Order.order_number)))
