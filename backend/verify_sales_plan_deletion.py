"""판매계획 파일과 그 파일이 만든 Sales Master 주문의 영구 삭제를 실제 HTTP로 검증합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import delete, func, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal
from app.upload_deletion import delete_upload_material


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
    boundary = f"----PlanDelete{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode(), b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n", content, b"\r\n", f"--{boundary}--\r\n".encode()])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def make_workbook(marker: str) -> bytes:
    book = Workbook(); sheet = book.active; sheet.title = "SalesPlan"
    sheet.append([f"PO-{marker}", f"MODEL-{marker}", f"CUSTOMER-{marker}", f"QTY-{marker}", f"NET-{marker}", f"GROSS-{marker}", f"CURRENCY-{marker}", f"PLANT-{marker}", f"REGION-{marker}"])
    sheet.append([f"00{marker}", f"DELETE-{marker}", f"CUSTOMER-{marker}", 5, 5000, 5500, "USD", "구미", "검증지역"])
    output = BytesIO(); book.save(output); return output.getvalue()


def main() -> None:
    marker = uuid4().hex[:9]
    token, login_id = f"verify-plan-delete-{uuid4().hex}", str(uuid4())
    batch_id = order_id = control_order_id = header_signature = None
    cleanup_sources: set[str] = set()
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        project_id, user_id = membership.project_id, membership.user_id
        session.add(models.LoginSession(id=login_id, user_id=user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)))
        session.commit()

    try:
        # 다른 원본으로 만든 통제 주문은 판매계획 삭제 뒤에도 남아야 합니다.
        status, raw = call("/api/orders", token, project_id, method="POST", payload={
            "sales_company_id": "sales-b", "production_company_id": "plant-b", "target_month": "2098-12",
            "po_number": f"CONTROL-{marker}", "model": f"CONTROL-{marker}", "customer": "CONTROL CUSTOMER",
            "quantity": 1, "currency": "USD", "unit_price": 100,
        })
        assert status == 201, raw
        control_order_id = json.loads(raw)["id"]
        with SessionLocal() as session:
            original_order_ids = set(session.scalars(select(models.Order.id).where(models.Order.project_id == project_id)))

        content = make_workbook(marker)
        form, content_type = multipart({
            "data_type": "판매계획", "sales_company_id": "sales-a", "target_month": "2099-01",
            "reference_date": "2026-11-04", "scope_type": "전체 현황", "scope_description": f"판매계획 삭제 검증 {marker}",
        }, f"sales-plan-delete-{marker}.xlsx", content)
        status, raw = call("/api/uploads/inspect", token, project_id, method="POST", body=form, content_type=content_type)
        assert status == 201, raw
        inspected = json.loads(raw); batch_id = inspected["batch"]["id"]
        old_source_id = inspected["batch"]["download_url"].split("/")[-2]
        cleanup_sources.add(old_source_id)
        mapping = {"po_number": 0, "model": 1, "customer": 2, "quantity": 3, "net_sales": 4, "gross_sales": 5, "currency": 6, "production_site": 7, "sales_region": 8}
        status, raw = call(f"/api/uploads/{batch_id}/prepare", token, project_id, method="POST", payload={"sheet_name": "SalesPlan", "header_row": 1, "mapping": mapping, "pricing": {"net_sales": "총액", "gross_sales": "총액"}})
        assert status == 200 and json.loads(raw)["application_mode"] == "최초 반영", raw
        with SessionLocal() as session:
            header_signature = session.get(models.UploadBatch, batch_id).header_signature

        status, raw = call(f"/api/uploads/{batch_id}/apply", token, project_id, method="POST", payload={})
        assert status == 200 and json.loads(raw)["created_orders"] == 1, raw
        status, raw = call("/api/uploads/history", token, project_id)
        history_item = next(item for item in json.loads(raw) if item["id"] == batch_id)
        assert history_item["data_type"] == "판매계획" and history_item["deletable"] is True
        with SessionLocal() as session:
            order = session.scalar(select(models.Order).where(models.Order.upload_batch_id == batch_id))
            assert order is not None
            order_id = order.id
            plan_version_id = session.scalar(select(models.PlanVersion.id).where(models.PlanVersion.source_id == old_source_id))

        status, raw = call(f"/api/orders/{order_id}/erp-confirmations", token, project_id, method="POST", payload={"status": "일부 완료", "confirmed_quantity": 2, "confirmation_date": "2026-11-04", "note": "판매계획 삭제 검증"})
        assert status == 201, raw
        with SessionLocal() as session:
            erp_source = session.scalar(select(models.ErpConfirmation.source_id).where(models.ErpConfirmation.order_id == order_id))
            if erp_source: cleanup_sources.add(erp_source)

        status, raw = call(f"/api/upload-materials/batch/{batch_id}", token, project_id, method="DELETE")
        result = json.loads(raw)
        assert status == 200 and result["deleted_order_count"] == 1, raw
        batch_id = None

        status, raw = call("/api/orders", token, project_id)
        assert all(row["id"] != order_id for row in json.loads(raw))
        assert call(f"/api/source-materials/{old_source_id}/download", token, project_id)[0] == 404
        assert call(f"/api/upload-materials/batch/{inspected['batch']['id']}", token, project_id, method="DELETE")[0] == 404
        status, raw = call("/api/uploads/history", token, project_id)
        assert all(item["id"] != inspected["batch"]["id"] for item in json.loads(raw))

        with SessionLocal() as session:
            assert session.get(models.UploadBatch, inspected["batch"]["id"]) is None
            assert session.scalar(select(func.count()).select_from(models.UploadRawRow).where(models.UploadRawRow.batch_id == inspected["batch"]["id"])) == 0
            assert session.scalar(select(func.count()).select_from(models.StandardizedResult).where(models.StandardizedResult.batch_id == inspected["batch"]["id"])) == 0
            assert plan_version_id is None or session.get(models.PlanVersion, plan_version_id) is None
            remaining_ids = set(session.scalars(select(models.Order.id).where(models.Order.project_id == project_id)))
            assert remaining_ids == original_order_ids
            assert session.scalar(select(func.count()).select_from(models.ErpConfirmation).where(models.ErpConfirmation.order_id == order_id)) == 0

        print(json.dumps({
            "result": "PASS",
            "sales_plan_file_deleted": True,
            "raw_and_standardized_deleted": True,
            "plan_version_deleted": True,
            "sales_master_order_deleted": True,
            "order_dependencies_deleted": True,
            "other_orders_preserved": True,
            "download_returns_404": True,
            "duplicate_delete_returns_404": True,
        }, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if batch_id:
                try:
                    delete_upload_material(session, project_id, "batch", batch_id)
                    session.commit()
                except Exception:
                    session.rollback()
            if order_id:
                cleanup_sources.update(session.scalars(select(models.ChangeEvent.source_id).where(models.ChangeEvent.order_id == order_id)))
                session.execute(delete(models.OrderUpdateProposal).where(models.OrderUpdateProposal.order_id == order_id))
                session.execute(delete(models.OrderManualOverride).where(models.OrderManualOverride.order_id == order_id))
                session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.order_id == order_id))
                session.execute(delete(models.ProductionAllocation).where(models.ProductionAllocation.order_id == order_id))
                session.execute(delete(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id == order_id))
                session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.order_id == order_id))
                session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.order_id == order_id))
                session.execute(delete(models.Order).where(models.Order.id == order_id))
            if control_order_id:
                control_source = session.scalar(select(models.Order.source_id).where(models.Order.id == control_order_id))
                session.execute(delete(models.OrderUpdateProposal).where(models.OrderUpdateProposal.order_id == control_order_id))
                session.execute(delete(models.OrderManualOverride).where(models.OrderManualOverride.order_id == control_order_id))
                session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.order_id == control_order_id))
                session.execute(delete(models.ProductionAllocation).where(models.ProductionAllocation.order_id == control_order_id))
                session.execute(delete(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id == control_order_id))
                session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.order_id == control_order_id))
                session.execute(delete(models.Order).where(models.Order.id == control_order_id))
                if control_source:
                    cleanup_sources.add(control_source)
            if header_signature:
                session.execute(delete(models.MappingProfile).where(models.MappingProfile.project_id == project_id, models.MappingProfile.sales_company_id == "sales-a", models.MappingProfile.header_signature == header_signature))
            session.flush()
            for source_id in cleanup_sources:
                referenced = session.scalar(select(models.Order.id).where(models.Order.source_id == source_id).limit(1))
                if referenced is None:
                    session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == source_id))
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
