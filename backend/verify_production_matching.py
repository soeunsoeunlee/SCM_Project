"""생산정보 자동 매칭의 핵심 규칙을 실제 PostgreSQL 트랜잭션에서 검증합니다.

테스트 데이터는 마지막에 rollback하여 사용자 프로젝트에 남기지 않습니다.
"""
from datetime import date
from io import BytesIO
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import select

from app import models
from app import operational_matching
from app import upload_routes
from app.database import SessionLocal
from app.operational_matching import match_operational_batch, match_production_batch
from app.upload_service import standardize_rows


def workbook_bytes(rows: list[list]) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "생산일정"
    for row in rows:
        sheet.append(row)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def main() -> None:
    session = SessionLocal()
    marker = uuid4().hex[:12]
    try:
        project = session.scalar(select(models.Project).limit(1))
        sales = session.scalar(select(models.Company).where(models.Company.kind == "판매법인").limit(1))
        plant = session.scalar(select(models.Company).where(models.Company.kind == "생산법인").limit(1))
        assert project and sales and plant

        sales_file = workbook_bytes([
            ["PO", "Model", "Customer", "Qty", "Net Sales", "Region"],
            ["00047120", "MODEL-X.US", "검증 고객", 10, 100, "중동"],
        ])
        prepared = standardize_rows(
            sales_file, ".xlsx", "생산일정", 1,
            {"po_number": 0, "model": 1, "customer": 2, "quantity": 3, "net_sales": 4, "sales_region": 5},
            {"net_sales": "단가", "gross_sales": ""},
        )
        assert prepared["can_apply"] is True
        assert prepared["rows"][0]["production_company_id"] is None

        sales_source = models.SourceMaterial(
            id=f"test-sales-{marker}", name="test-sales.xlsx", description="검증", received_at="2026-11-04",
            source_hash=uuid4().hex + uuid4().hex,
        )
        production_source = models.SourceMaterial(
            id=f"test-prod-{marker}", name="test-production.xlsx", description="검증", received_at="2026-11-04",
            source_hash=uuid4().hex + uuid4().hex,
        )
        order = models.Order(
            id=f"test-order-{marker}", project_id=project.id, order_number=f"TEST-{marker}",
            sales_company_id=sales.id, production_company_id=None, target_month="2027-01",
            po_number="00047120", model="MODEL-X.US", customer="검증 고객", quantity=10,
            currency="USD", unit_price=100, erp_status="미확인", match_status="미연결",
            source_id=sales_source.id,
        )
        production_file = workbook_bytes([
            ["PO번호", "모델", "판매법인", "생산수량", "PST"],
            ["00047120", "MODEL-X.US", sales.name, 6, date(2027, 1, 10)],
            ["00047120", "MODEL-X.US", sales.name, 4, date(2027, 1, 15)],
        ])
        batch = models.UploadBatch(
            id=f"test-batch-{marker}", project_id=project.id, source_id=production_source.id,
            file_name="test-production.xlsx", file_extension=".xlsx", file_hash=uuid4().hex,
            data_type="생산정보", file_content=production_file, sales_company_id=None,
            production_company_id=plant.id, target_month="2027-01", reference_date=date(2027, 1, 12),
            scope_type="전체 현황", scope_description="자동 매칭 검증", status="업로드 완료",
        )
        session.add_all([sales_source, production_source, order, batch])
        session.flush()
        result = match_production_batch(session, batch)
        session.flush()
        session.refresh(order)
        allocations = list(session.scalars(select(models.ProductionAllocation).where(models.ProductionAllocation.order_id == order.id)))
        assert result["matched_rows"] == 2 and result["review_rows"] == 0 and result["ai_suggested_rows"] == 0
        assert order.production_company_id == plant.id
        assert order.match_status == "생산 실적 연결"
        assert sum(item.quantity for item in allocations) == 10
        assert {(item.quantity, item.status) for item in allocations} == {(6, "실제 완료"), (4, "예정")}
        assert {item.production_date for item in allocations} == {date(2027, 1, 10), date(2027, 1, 15)}

        # 다음 생산계획에서 이미 완료로 확정한 과거 PST 행이 빠져도 완료 6ea는 보존하고,
        # 남은 미래 일정 4ea만 다시 연결합니다.
        batch.file_content = workbook_bytes([
            ["PO번호", "모델", "판매법인", "생산수량", "PST"],
            ["00047120", "MODEL-X.US", sales.name, 4, date(2027, 1, 15)],
        ])
        repeat_result = match_production_batch(session, batch)
        session.flush()
        allocations = list(session.scalars(select(models.ProductionAllocation).where(models.ProductionAllocation.order_id == order.id)))
        assert repeat_result["matched_rows"] == 1 and repeat_result["review_rows"] == 0
        assert {(item.quantity, item.status, item.production_date) for item in allocations} == {
            (6, "실제 완료", date(2027, 1, 10)),
            (4, "예정", date(2027, 1, 15)),
        }

        shipment_source = models.SourceMaterial(
            id=f"test-ship-{marker}", name="test-shipment.xlsx", description="검증", received_at="2026-11-05",
            source_hash=uuid4().hex + uuid4().hex,
        )
        shipment_file = workbook_bytes([
            ["PO번호", "모델", "판매법인", "선적수량", "ETD", "ETA", "선사명", "선박명"],
            ["00047120", "MODEL-X.US", sales.name, 5, date(2027, 1, 20), date(2027, 2, 3), "TEST LINE", "TEST VESSEL"],
            ["00047120", "MODEL-X.US", sales.name, 5, date(2027, 1, 22), date(2027, 2, 5), "TEST LINE", "TEST VESSEL"],
        ])
        shipment_batch = models.UploadBatch(
            id=f"test-ship-batch-{marker}", project_id=project.id, source_id=shipment_source.id,
            file_name="test-shipment.xlsx", file_extension=".xlsx", file_hash=uuid4().hex,
            data_type="선적정보", file_content=shipment_file, sales_company_id=None,
            production_company_id=plant.id, target_month="2027-01", reference_date=date(2027, 1, 21),
            scope_type="전체 현황", scope_description="선적 자동 매칭 검증", status="업로드 완료",
        )
        session.add_all([shipment_source, shipment_batch])
        session.flush()
        shipment_result = match_operational_batch(session, shipment_batch)
        session.flush()
        shipments = list(session.scalars(select(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id == order.id)))
        assert shipment_result["matched_rows"] == 2 and shipment_result["review_rows"] == 0
        assert sum(item.quantity for item in shipments) == 10
        assert {(item.quantity, item.status) for item in shipments} == {(5, "실제 선적"), (5, "예정")}
        assert {item.shipment_date for item in shipments} == {date(2027, 1, 20), date(2027, 1, 22)}
        assert {item.eta_date for item in shipments} == {date(2027, 2, 3), date(2027, 2, 5)}

        # 다음 선적계획에서 완료로 확정한 과거 ETD 행이 빠져도 실제 선적 5ea를 보존합니다.
        shipment_batch.file_content = workbook_bytes([
            ["PO번호", "모델", "판매법인", "선적수량", "ETD", "ETA", "선사명", "선박명"],
            ["00047120", "MODEL-X.US", sales.name, 5, date(2027, 1, 22), date(2027, 2, 5), "TEST LINE", "TEST VESSEL"],
        ])
        repeated_shipment = match_operational_batch(session, shipment_batch)
        session.flush()
        shipments = list(session.scalars(select(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id == order.id)))
        assert repeated_shipment["matched_rows"] == 1 and repeated_shipment["review_rows"] == 0
        assert {(item.quantity, item.status, item.shipment_date) for item in shipments} == {
            (5, "실제 선적", date(2027, 1, 20)),
            (5, "예정", date(2027, 1, 22)),
        }

        ai_sales_source = models.SourceMaterial(
            id=f"test-ai-sales-{marker}", name="test-ai-sales.xlsx", description="검증", received_at="2026-11-05",
            source_hash=uuid4().hex + uuid4().hex,
        )
        ai_order = models.Order(
            id=f"test-ai-order-{marker}", project_id=project.id, order_number=f"TEST-AI-{marker}",
            sales_company_id=sales.id, production_company_id=None, target_month="2027-01",
            po_number="AI-ORIGINAL-PO", model="MODEL-AI.US", customer="AI 검증 고객", quantity=3,
            currency="USD", unit_price=100, erp_status="미확인", match_status="미연결",
            source_id=ai_sales_source.id,
        )
        ai_production_source = models.SourceMaterial(
            id=f"test-ai-prod-{marker}", name="test-ai-production.xlsx", description="검증", received_at="2026-11-05",
            source_hash=uuid4().hex + uuid4().hex,
        )
        ai_file = workbook_bytes([
            ["PO번호", "모델", "판매법인", "생산수량", "PST"],
            ["AI-CHANGED-PO", "MODEL-AI.US", sales.name, 3, date(2027, 1, 25)],
        ])
        ai_batch = models.UploadBatch(
            id=f"test-ai-batch-{marker}", project_id=project.id, source_id=ai_production_source.id,
            file_name="test-ai-production.xlsx", file_extension=".xlsx", file_hash=uuid4().hex,
            data_type="생산정보", file_content=ai_file, sales_company_id=None,
            production_company_id=plant.id, target_month="2027-01", reference_date=date(2027, 1, 6),
            scope_type="전체 현황", scope_description="AI 검토 후보 검증", status="업로드 완료",
        )
        session.add_all([ai_sales_source, ai_order, ai_production_source, ai_batch])
        session.flush()
        original_suggest = operational_matching.suggest_order_candidate
        operational_matching.suggest_order_candidate = lambda source, candidates, data_type: {
            "status": "used", "candidate_order_id": ai_order.id, "confidence": "high",
            "same_business_quantity": True, "reason": "모델·판매법인·수량이 일치하고 PO 형식만 다름", "signals": ["model", "sales_company", "quantity"],
        }
        try:
            ai_result = match_operational_batch(session, ai_batch)
        finally:
            operational_matching.suggest_order_candidate = original_suggest
        session.flush()
        ai_allocations = list(session.scalars(select(models.ProductionAllocation).where(models.ProductionAllocation.order_id == ai_order.id)))
        assert ai_result["matched_rows"] == 0 and ai_result["ai_suggested_rows"] == 1
        assert len(ai_allocations) == 1 and ai_allocations[0].status == "검토 필요"
        assert ai_order.match_status == "검토 필요" and ai_order.production_company_id is None

        # 양식 변경으로 PO 표기가 달라진 후속 판매계획도 AI가 검토 후보만 만들고,
        # 사용자의 선택 전에는 기존 주문을 바꾸지 않는지 확인합니다.
        followup_source = models.SourceMaterial(
            id=f"test-followup-source-{marker}", name="changed-layout.xlsx", description="검증", received_at="2026-11-05",
            source_hash=uuid4().hex + uuid4().hex,
        )
        followup_batch = models.UploadBatch(
            id=f"test-followup-batch-{marker}", project_id=project.id, source_id=followup_source.id,
            file_name="changed-layout.xlsx", file_extension=".xlsx", file_hash=uuid4().hex,
            data_type="판매계획", file_content=b"test", sales_company_id=sales.id,
            production_company_id=None, target_month="2027-01", reference_date=date(2027, 1, 7),
            scope_type="전체 현황", scope_description="후속 판매계획 AI 후보 검증", status="표준화 완료",
        )
        followup_raw = models.UploadRawRow(
            batch=followup_batch, sheet_name="Changed", row_number=3, raw_values=[],
            formula_missing_columns=[], merged_columns=[], status="처리", issues=[],
        )
        followup_result = models.StandardizedResult(
            batch=followup_batch, raw_row=followup_raw, status="처리", issues=[],
            po_number="AI-PO-NEW-FORMAT", model="MODEL-AI.NEW", customer="AI 검증 고객",
            quantity=3, net_sales_total=330, gross_sales_total=360, currency="USD", sales_region="중동",
        )
        session.add_all([followup_source, followup_batch, followup_raw, followup_result])
        session.flush()
        original_followup_suggest = upload_routes.suggest_order_candidate
        upload_routes.suggest_order_candidate = lambda source, candidates, data_type: {
            "status": "used", "candidate_order_id": ai_order.id, "confidence": "high",
            "same_business_quantity": True, "reason": "모델 기본명·고객·월·수량이 일치", "signals": ["model", "customer", "target_month", "quantity"],
        }
        try:
            proposal_count = upload_routes._create_order_update_proposals(session, followup_batch, [followup_result])
        finally:
            upload_routes.suggest_order_candidate = original_followup_suggest
        session.flush()
        proposal = session.scalar(select(models.OrderUpdateProposal).where(
            models.OrderUpdateProposal.batch_id == followup_batch.id,
            models.OrderUpdateProposal.order_id == ai_order.id,
        ))
        assert proposal_count == 1 and proposal is not None
        assert proposal.status == "검토 대기"
        assert proposal.incoming_values["_ai_match_basis"]["confidence"] == "high"
        assert ai_order.po_number == "AI-ORIGINAL-PO" and ai_order.quantity == 3
        print("통과: 과거 PST·ETD 실적 확정과 후속 누락 시 보존, 미래 PST·ETD 예정, 생산 AI 검토 후보, 후속 판매계획 AI 변경 제안(자동 반영 없음)")
    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
