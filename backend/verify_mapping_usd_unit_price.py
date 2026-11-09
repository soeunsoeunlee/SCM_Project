"""USD 공통값, 기준일→대상 월, Unit Price 표시용 실제 HTTP 회귀 검사입니다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from io import BytesIO
import json
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from openpyxl import Workbook, load_workbook
from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal


BASE = "http://127.0.0.1:8000/api"
opener = build_opener(HTTPCookieProcessor(CookieJar()))


def json_request(path: str, payload: dict | None = None, method: str | None = None):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(BASE + path, data=data, method=method or ("POST" if data else "GET"), headers={"Content-Type": "application/json"} if data else {})
    with opener.open(request, timeout=15) as response:
        body = response.read()
        return json.loads(body) if response.headers.get_content_type() == "application/json" else body


def multipart(path: str, fields: dict[str, str], filename: str, content: bytes) -> dict:
    boundary = f"----scm{uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'.encode("utf-8"))
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = Request(BASE + path, data=bytes(body), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with opener.open(request, timeout=15) as response:
        return json.load(response)


def main() -> None:
    token = f"verify-unit-{uuid4()}"
    session_id = str(uuid4())
    batch_id: str | None = None
    production_batch_id: str | None = None
    shipment_batch_id: str | None = None
    custom_company_ids: list[str] = []
    signature: str | None = None
    unique_po = f"VERIFY-{uuid4().hex[:10]}"
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        session.add(models.LoginSession(id=session_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        session.commit()
    opener.addheaders = [("Cookie", f"scm_session={token}; scm_project={membership.project_id}")]

    book = Workbook(); sheet = book.active; sheet.title = "Plan"
    sheet.append(["PO", "Model", "Customer", "Qty", "Net Sales", "Factory", "Region"])
    sheet.append([unique_po, "VERIFY-MODEL", "VERIFY-CUSTOMER", 2, 125.5, "구미", "걸프"])
    output = BytesIO(); book.save(output)
    try:
        inspected = multipart("/uploads/inspect", {
            "data_type": "판매계획", "sales_company_id": "sales-a", "target_month": "1999-01",
            "reference_date": "2027-02-17", "scope_type": "전체 현황", "scope_description": "자동 검증",
        }, "verify-usd-mapping.xlsx", output.getvalue())
        batch_id = inspected["batch"]["id"]
        assert inspected["batch"]["target_month"] == "2027-02"
        reopened = json_request(f"/uploads/{batch_id}/inspect")
        assert reopened["batch"]["id"] == batch_id and reopened["sheets"][0]["name"] == "Plan"
        context = json_request(f"/uploads/{batch_id}/mapping", {"sheet_name": "Plan", "header_row": 1})
        signature = context["header_signature"]
        assert "currency" not in {field["key"] for field in context["standard_fields"]}
        mapping = {"po_number": 0, "model": 1, "customer": 2, "quantity": 3, "net_sales": 4, "sales_region": 6}
        prepared = json_request(f"/uploads/{batch_id}/prepare", {"sheet_name": "Plan", "header_row": 1, "mapping": mapping, "pricing": {"net_sales": "단가", "gross_sales": ""}})
        assert prepared["can_apply"] and prepared["rows"][0]["currency"] == "USD"
        assert prepared["rows"][0]["production_site"] is None
        json_request(f"/uploads/{batch_id}/apply", {}, "POST")
        order = next(item for item in json_request("/orders") if item["po_number"] == unique_po)
        assert order["unit_price"] == 125.5 and order["target_month"] == "2027-02"
        workbook = load_workbook(BytesIO(json_request("/exports/sales-master.xlsx")))
        headers = [cell.value for cell in workbook.active[2]]
        assert headers[7:10] == ["주문 수량(ea)", "Unit Price (USD)", "매칭 검토 필요"]
        deleted = json_request(f"/upload-materials/batch/{batch_id}", None, "DELETE")
        assert deleted["deleted_order_count"] == 1
        batch_id = None
        assert all(item["po_number"] != unique_po for item in json_request("/orders"))

        production = multipart("/uploads/inspect", {
            "data_type": "생산정보", "sales_company_id": "", "production_company_id": "plant-b", "target_month": "1999-01",
            "reference_date": "2027-03-09", "scope_type": "전체 현황", "scope_description": "복수 판매법인 생산정보 검증",
        }, f"verify-multi-company-{uuid4().hex[:8]}.xlsx", output.getvalue())
        production_batch_id = production["batch"]["id"]
        assert production["batch"]["sales_company_id"] is None
        assert production["batch"]["sales_company"] == "여러 판매법인"
        assert production["batch"]["production_company"] == "이집트"
        assert production["batch"]["target_month"] == "2027-03"
        history = json_request("/uploads/history")
        assert any(item["id"] == production_batch_id and item["sales_company"] == "여러 판매법인" for item in history)
        json_request(f"/upload-materials/batch/{production_batch_id}", None, "DELETE")
        production_batch_id = None

        custom_sales = json_request("/companies", {"name": f"검증판매-{uuid4().hex[:6]}", "kind": "판매법인"})
        custom_plant = json_request("/companies", {"name": f"검증생산-{uuid4().hex[:6]}", "kind": "생산법인"})
        custom_company_ids.extend([custom_sales["id"], custom_plant["id"]])
        dashboard = json_request("/dashboard")
        assert custom_sales["id"] in {item["id"] for item in dashboard["companies"]}
        assert custom_plant["id"] in {item["id"] for item in dashboard["companies"]}
        shipment = multipart("/uploads/inspect", {
            "data_type": "선적정보", "sales_company_id": "", "production_company_id": custom_plant["id"], "target_month": "1999-01",
            "reference_date": "2027-04-11", "scope_type": "전체 현황", "scope_description": "복수 판매법인 선적정보 검증",
        }, f"verify-shipment-{uuid4().hex[:8]}.xlsx", output.getvalue())
        shipment_batch_id = shipment["batch"]["id"]
        assert shipment["batch"]["sales_company"] == "여러 판매법인"
        assert shipment["batch"]["production_company"] == custom_plant["name"]
        json_request(f"/upload-materials/batch/{shipment_batch_id}", None, "DELETE")
        shipment_batch_id = None
        print("통과: Sales Master 매칭 재열기, USD·Unit Price, 생산·선적 복수 판매법인, 생산법인 선택, 프로젝트 법인 추가, 검증 자료 정리")
    finally:
        if batch_id:
            try:
                json_request(f"/upload-materials/batch/{batch_id}", None, "DELETE")
            except Exception:
                pass
        if production_batch_id:
            try:
                json_request(f"/upload-materials/batch/{production_batch_id}", None, "DELETE")
            except Exception:
                pass
        if shipment_batch_id:
            try:
                json_request(f"/upload-materials/batch/{shipment_batch_id}", None, "DELETE")
            except Exception:
                pass
        with SessionLocal() as session:
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == session_id))
            if signature:
                session.execute(delete(models.MappingProfile).where(models.MappingProfile.project_id == membership.project_id, models.MappingProfile.header_signature == signature))
            if custom_company_ids:
                session.execute(delete(models.Company).where(models.Company.id.in_(custom_company_ids)))
            session.commit()


if __name__ == "__main__":
    main()
