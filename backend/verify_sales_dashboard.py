"""Sales Master와 새 대시보드에 필요한 API·집계 규칙을 실제 PostgreSQL로 검증합니다."""

from datetime import datetime, timedelta, timezone
import json
from io import BytesIO
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import delete, select
from openpyxl import load_workbook

from app import models
from app.auth import token_digest
from app.database import SessionLocal


def get_json(path: str, token: str, project_id: str):
    request = Request(f"http://127.0.0.1:8000{path}", headers={"Cookie": f"scm_session={token}; scm_project={project_id}"})
    with urlopen(request, timeout=10) as response:
        assert response.status == 200
        return json.load(response)


def get_bytes(path: str, token: str, project_id: str) -> bytes:
    """인증 쿠키로 실제 다운로드 응답을 받아 바이트를 반환합니다."""
    request = Request(f"http://127.0.0.1:8000{path}", headers={"Cookie": f"scm_session={token}; scm_project={project_id}"})
    with urlopen(request, timeout=10) as response:
        assert response.status == 200
        return response.read()


def main() -> None:
    token = f"verify-{uuid4()}"
    session_id = str(uuid4())
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None, "검증할 로그인 사용자/프로젝트가 없습니다."
        session.add(models.LoginSession(
            id=session_id,
            user_id=membership.user_id,
            token_hash=token_digest(token),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ))
        session.commit()

    try:
        orders = get_json("/api/orders", token, membership.project_id)
        dashboard = get_json("/api/dashboard", token, membership.project_id)
        psi = get_json("/api/psi", token, membership.project_id)
        assert orders, "주문 API가 비었습니다."
        assert all(order["currency"] == "USD" for order in orders)
        assert all(order["source_download_label"] == "원본 파일" for order in orders)
        assert any(order["closed_quantity"] > 0 for order in orders)
        assert all("production_schedule_status" in order and "shipment_schedule_status" in order for order in orders)
        shipment_rows = [detail for order in orders for detail in order["shipment_details"]]
        assert shipment_rows and all("eta_date" in detail and "vessel_name" in detail for detail in shipment_rows)
        assert any(detail["eta_date"] and detail["carrier"] and detail["vessel_name"] for detail in shipment_rows)
        assert len([company for company in dashboard["companies"] if company["kind"] == "판매법인"]) == 9
        assert len([company for company in dashboard["companies"] if company["kind"] == "생산법인"]) == 6

        # 주문금액은 상세 생산/선적 행 수와 관계없이 주문 한 건당 한 번만 더합니다.
        net_total = sum(order["net_sales_amount"] for order in orders)
        assert round(net_total, 2) == round(dashboard["planned_value_by_currency"]["USD"], 2)
        for order in orders:
            completed = order["net_sales_amount"] * min(order["closed_quantity"], order["quantity"]) / order["quantity"]
            pending = order["net_sales_amount"] - completed
            assert completed >= 0 and pending >= 0

        assert len(psi["periods"]) >= 48
        assert len(psi["rows"]) % 5 == 0
        assert {row["metric"] for row in psi["rows"]} == {"DM", "SA", "SP", "Inventory", "Warehouse"}
        for path, sheet_name in (("/api/exports/dashboard.xlsx", "Dashboard"), ("/api/exports/sales-master.xlsx", "Sales Master"), ("/api/exports/psi.xlsx", "PSI")):
            workbook = load_workbook(BytesIO(get_bytes(path, token, membership.project_id)), read_only=True, data_only=False)
            assert sheet_name in workbook.sheetnames
            assert workbook[sheet_name].max_row >= 3
            if sheet_name == "Sales Master":
                headers = [cell.value for cell in next(workbook[sheet_name].iter_rows(min_row=2, max_row=2))]
                assert all(name in headers for name in ("PST", "ETD", "ETA", "선사명", "선박명"))
                assert headers.index("Status") < headers.index("Closed")

        print(f"통과: 주문 {len(orders)}건, PSI {len(psi['rows'])}행, Excel 3종, Net USD 합계 {net_total:,.2f}")
    finally:
        with SessionLocal() as session:
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == session_id))
            session.commit()


if __name__ == "__main__":
    main()
