"""대시보드 합계, 생산 확정 보존, 수동 우선값과 업로드 변경 검토를 실제 HTTP로 검증합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal


BASE = os.getenv("SCM_VERIFY_URL", "http://127.0.0.1:8000")


def call(path: str, token: str, project_id: str, *, method: str = "GET", payload: dict | None = None, body: bytes | None = None, content_type: str | None = None):
    headers = {"Cookie": f"scm_session={token}; scm_project={project_id}"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        content_type = "application/json; charset=utf-8"
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as error:
        return error.code, error.read()


def multipart(fields: dict[str, str], file_name: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----SalesImprove{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode(), b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n", content, b"\r\n", f"--{boundary}--\r\n".encode()])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def workbook(marker: str, po: str, model: str, customer: str) -> tuple[bytes, list[str]]:
    headers = [f"PO-{marker}", f"MODEL-{marker}", f"CUSTOMER-{marker}", f"QTY-{marker}", f"NET-{marker}", f"GROSS-{marker}", f"CURRENCY-{marker}", f"PLANT-{marker}", f"REGION-{marker}"]
    book = Workbook(); sheet = book.active; sheet.title = "Update"
    sheet.append(headers)
    sheet.append([po, model, customer, 11, 1100, 1210, "USD", "구미", "검증지역"])
    output = BytesIO(); book.save(output)
    return output.getvalue(), headers


def main() -> None:
    marker = uuid4().hex[:10]
    token, login_id = f"verify-improve-{uuid4()}", str(uuid4())
    order_id = batch_id = batch_source = header_signature = None
    source_ids: set[str] = set()
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        project_id, user_id = membership.project_id, membership.user_id
        session.add(models.LoginSession(id=login_id, user_id=user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)))
        session.commit()
    try:
        # 검증 전용 주문을 만들고 과거 날짜의 실제 6ea와 예정 4ea를 수동 저장합니다.
        status, raw = call("/api/orders", token, project_id, method="POST", payload={
            "sales_company_id": "sales-a", "production_company_id": "plant-a", "target_month": "2026-12",
            "po_number": f"00{marker}", "model": f"MODEL-{marker}", "customer": f"CUSTOMER-{marker}",
            "quantity": 10, "currency": "USD", "unit_price": 100,
        })
        assert status == 201, raw
        created = json.loads(raw); order_id = created["id"]
        edit = {
            "order_number": created["order_number"], "sales_company_id": "sales-a", "production_company_id": "plant-a",
            "target_month": "2026-12", "po_number": f"00{marker}", "po_line_number": None,
            "model": f"MODEL-{marker}", "customer": f"CUSTOMER-{marker}", "quantity": 10,
            "net_sales_amount": 1000, "gross_sales_amount": 1100, "production_site_source": "구미",
            "sales_region": "검증지역", "match_status": "연결 완료",
            "production_details": [
                {"quantity": 6, "status": "실제 완료", "production_date": "2026-10-01"},
                {"quantity": 4, "status": "예정", "production_date": "2026-10-02"},
            ],
            "shipment_details": [],
        }
        status, raw = call(f"/api/orders/{order_id}", token, project_id, method="PATCH", payload=edit)
        assert status == 200, raw
        status, raw = call("/api/orders", token, project_id)
        order = next(row for row in json.loads(raw) if row["id"] == order_id)
        assert order["production_quantity"] == 6
        assert order["production_planned_quantity"] == 4
        assert sorted(row["status"] for row in order["production_details"]) == ["실제 완료", "예정"]
        assert "production_details" in order["manual_fields"] and order["pending_update_count"] == 0

        # 같은 주문을 포함한 후속 판매자료는 주문을 바꾸지 않고 변경 제안만 만듭니다.
        content, headers = workbook(marker, edit["po_number"], edit["model"], edit["customer"])
        form, content_type = multipart({
            "data_type": "판매계획", "sales_company_id": "sales-a", "target_month": "2026-12",
            "reference_date": "2026-11-02", "scope_type": "전체 현황", "scope_description": f"수동 우선 검증 {marker}",
        }, f"update-{marker}.xlsx", content)
        status, raw = call("/api/uploads/inspect", token, project_id, method="POST", body=form, content_type=content_type)
        assert status == 201, raw
        inspected = json.loads(raw); batch_id = inspected["batch"]["id"]
        mapping = {"po_number": 0, "model": 1, "customer": 2, "quantity": 3, "net_sales": 4, "gross_sales": 5, "currency": 6, "production_site": 7, "sales_region": 8}
        status, raw = call(f"/api/uploads/{batch_id}/prepare", token, project_id, method="POST", payload={"sheet_name": "Update", "header_row": 1, "mapping": mapping, "pricing": {"net_sales": "총액", "gross_sales": "총액"}})
        assert status == 200, raw
        prepared = json.loads(raw); assert prepared["application_mode"] == "버전 비교 대기"
        status, raw = call(f"/api/uploads/{batch_id}/apply", token, project_id, method="POST", payload={})
        assert status == 200 and "업데이트 검토 1건" in json.loads(raw)["message"], raw
        status, raw = call(f"/api/orders/{order_id}/update-proposals", token, project_id)
        proposals = json.loads(raw); assert status == 200 and len(proposals) == 1
        proposal = proposals[0]
        assert proposal["differences"]["quantity"]["manual_priority"] is True
        status, raw = call("/api/orders", token, project_id)
        unchanged = next(row for row in json.loads(raw) if row["id"] == order_id)
        assert unchanged["quantity"] == 10 and unchanged["production_quantity"] == 6

        # 사용자가 수량만 선택했을 때만 11ea로 바뀌고 실제 생산 6ea는 유지됩니다.
        status, raw = call(f"/api/order-update-proposals/{proposal['id']}/apply", token, project_id, method="POST", payload={"fields": ["quantity"]})
        assert status == 200, raw
        status, raw = call("/api/orders", token, project_id)
        updated = next(row for row in json.loads(raw) if row["id"] == order_id)
        assert updated["quantity"] == 11 and updated["production_quantity"] == 6 and updated["production_planned_quantity"] == 4
        assert "quantity" not in updated["manual_fields"] and "model" in updated["manual_fields"]

        # Excel의 전체 합계는 수식 캐시가 아닌 실제 숫자로 맨 위 데이터 행에 저장됩니다.
        status, dashboard_bytes = call("/api/exports/dashboard.xlsx", token, project_id)
        assert status == 200
        sheet = load_workbook(BytesIO(dashboard_bytes), data_only=True)["Dashboard"]
        assert sheet["A3"].value == "전체 합계"
        assert all(isinstance(sheet.cell(3, column).value, (int, float)) for column in range(2, 7))
        print(json.dumps({"result": "PASS", "dashboard_total_values": True, "past_plan_not_actual": True, "actual_production_preserved": True, "manual_priority": True, "selective_update": True}, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if order_id:
                source_ids.update(session.scalars(select(models.ChangeEvent.source_id).where(models.ChangeEvent.order_id == order_id)).all())
                source = session.scalar(select(models.Order.source_id).where(models.Order.id == order_id))
                if source: source_ids.add(source)
                session.execute(delete(models.OrderUpdateProposal).where(models.OrderUpdateProposal.order_id == order_id))
                session.execute(delete(models.OrderManualOverride).where(models.OrderManualOverride.order_id == order_id))
                session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.order_id == order_id))
                session.execute(delete(models.ProductionAllocation).where(models.ProductionAllocation.order_id == order_id))
                session.execute(delete(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id == order_id))
                session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.order_id == order_id))
                session.execute(delete(models.Order).where(models.Order.id == order_id))
            if batch_id:
                batch = session.get(models.UploadBatch, batch_id)
                if batch:
                    batch_source, header_signature = batch.source_id, batch.header_signature
                    source_ids.add(batch_source)
                session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id == batch_id))
                session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id == batch_id))
                session.execute(delete(models.UploadBatch).where(models.UploadBatch.id == batch_id))
            if header_signature:
                session.execute(delete(models.MappingProfile).where(models.MappingProfile.project_id == project_id, models.MappingProfile.sales_company_id == "sales-a", models.MappingProfile.header_signature == header_signature))
            session.flush()
            for source_id in source_ids:
                session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == source_id))
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
