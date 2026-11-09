"""후속 판매자료 업로드가 기존 ERP Closed 확정을 유지하는지 검증합니다."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal

BASE = os.getenv("SCM_VERIFY_URL", "http://127.0.0.1:8000")


def call(path: str, token: str, project_id: str, *, payload: dict | None = None, file: bytes | None = None, fields: dict | None = None) -> dict:
    headers = {"Cookie": f"scm_session={token}; scm_project={project_id}"}
    if file is not None:
        boundary = f"----ClosedVerify{uuid4().hex}"
        body = bytearray()
        for name, value in (fields or {}).items():
            body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="closed-follow-up.xlsx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'.encode())
        body.extend(file); body.extend(f"\r\n--{boundary}--\r\n".encode())
        request = Request(BASE + path, data=bytes(body), method="POST", headers={**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"})
    else:
        raw = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        request = Request(BASE + path, data=raw, method="POST", headers={**headers, "Content-Type": "application/json; charset=utf-8"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def workbook_for(order: models.Order) -> bytes:
    book = Workbook(); sheet = book.active; sheet.title = "Closed Follow Up"
    sheet.append(["검증 PO", "검증 모델", "검증 고객", "검증 수량", "검증 Net", "검증 Gross", "검증 통화", "검증 생산지", "검증 지역"])
    sheet.append([order.po_number, order.model, order.customer, order.quantity, float(order.net_sales_amount or order.unit_price * order.quantity), float(order.gross_sales_amount or order.unit_price * order.quantity), "USD", order.production_company.name, "Closed 보존 검증"])
    output = BytesIO(); book.save(output); return output.getvalue()


def main() -> None:
    token, login_id = f"verify-closed-{uuid4()}", str(uuid4())
    batch_id = source_id = header_signature = None
    with SessionLocal() as session:
        order = session.scalar(select(models.Order).join(models.ErpConfirmation).order_by(models.Order.id))
        assert order is not None
        membership = session.scalar(select(models.ProjectMember).where(models.ProjectMember.project_id == order.project_id))
        assert membership is not None
        project_id = order.project_id
        before = (order.erp_status, tuple((row.id, row.status, row.confirmed_quantity) for row in order.erp_confirmations))
        content = workbook_for(order)
        fields = {"data_type": "판매계획", "sales_company_id": order.sales_company_id, "target_month": order.target_month, "reference_date": "2026-10-30", "scope_type": "전체 현황", "scope_description": "Closed 보존 자동 검증"}
        order_id = order.id
        company_id = order.sales_company_id
        session.add(models.LoginSession(id=login_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        session.commit()
    try:
        inspected = call("/api/uploads/inspect", token, project_id, file=content, fields=fields)
        batch_id = inspected["batch"]["id"]
        mapping = {"po_number": 0, "model": 1, "customer": 2, "quantity": 3, "net_sales": 4, "gross_sales": 5, "currency": 6, "production_site": 7, "sales_region": 8}
        prepared = call(f"/api/uploads/{batch_id}/prepare", token, project_id, payload={"sheet_name": "Closed Follow Up", "header_row": 1, "mapping": mapping, "pricing": {"net_sales": "총액", "gross_sales": "총액"}})
        assert prepared["application_mode"] == "버전 비교 대기" and prepared["can_apply"] is True
        applied = call(f"/api/uploads/{batch_id}/apply", token, project_id, payload={})
        assert applied["created_orders"] == 0 and "ERP Closed 기록은 변경하지 않았습니다" in applied["message"]
        with SessionLocal() as session:
            order = session.get(models.Order, order_id)
            after = (order.erp_status, tuple((row.id, row.status, row.confirmed_quantity) for row in order.erp_confirmations))
            assert after == before
            batch = session.get(models.UploadBatch, batch_id)
            source_id, header_signature = batch.source_id, batch.header_signature
        print(json.dumps({"result": "PASS", "order_id": order_id, "closed_records": len(before[1]), "status": before[0], "batch_status": applied["batch"]["status"]}, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if batch_id:
                session.execute(delete(models.OrderUpdateProposal).where(models.OrderUpdateProposal.batch_id == batch_id))
                session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id == batch_id))
                session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id == batch_id))
                session.execute(delete(models.UploadBatch).where(models.UploadBatch.id == batch_id))
                if header_signature:
                    session.execute(delete(models.MappingProfile).where(models.MappingProfile.project_id == project_id, models.MappingProfile.sales_company_id == company_id, models.MappingProfile.header_signature == header_signature))
                session.flush()
                if source_id:
                    session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == source_id))
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
