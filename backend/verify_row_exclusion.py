"""확인 필요 행을 사용자가 제외한 뒤 정상 행만 반영되는 실제 HTTP 회귀 검사입니다."""
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from io import BytesIO
import json
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from openpyxl import Workbook
from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal


BASE = "http://127.0.0.1:8000/api"
opener = build_opener(HTTPCookieProcessor(CookieJar()))


def request_json(path: str, payload: dict | None = None, method: str | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(BASE + path, data=data, method=method or ("POST" if data else "GET"), headers={"Content-Type": "application/json"} if data else {})
    with opener.open(request, timeout=120) as response:
        return json.load(response)


def multipart(path: str, fields: dict[str, str], filename: str, content: bytes) -> dict:
    boundary = f"----exclude{uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'.encode())
    body.extend(content); body.extend(f"\r\n--{boundary}--\r\n".encode())
    req = Request(BASE + path, data=bytes(body), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with opener.open(req, timeout=20) as response:
        return json.load(response)


def main() -> None:
    marker = uuid4().hex[:10]
    token, session_id = f"verify-exclude-{uuid4()}", str(uuid4())
    batch_id = None
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership
        session.add(models.LoginSession(id=session_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        session.commit()
    opener.addheaders = [("Cookie", f"scm_session={token}; scm_project={membership.project_id}")]
    book = Workbook(); sheet = book.active; sheet.title = "Plan"
    sheet.append(["PO", "Model", "Customer", "Qty", "Net", "Region"])
    sheet.append([f"EX-{marker}", "MODEL-X", "CUSTOMER-X", 2, 100, "걸프"])
    sheet.append(["", "", "Group Summary", 2, 200, ""])
    stream = BytesIO(); book.save(stream)
    try:
        inspected = multipart("/uploads/inspect", {
            "data_type": "판매계획", "sales_company_id": "sales-a", "target_month": "2026-11",
            "reference_date": "2026-11-06", "scope_type": "전체 현황", "scope_description": "행 제외 검증",
        }, f"exclude-{marker}.xlsx", stream.getvalue())
        batch_id = inspected["batch"]["id"]
        prepared = request_json(f"/uploads/{batch_id}/prepare", {
            "sheet_name": "Plan", "header_row": 1,
            "mapping": {"po_number": 0, "model": 1, "customer": 2, "quantity": 3, "net_sales": 4, "sales_region": 5},
            "pricing": {"net_sales": "단가", "gross_sales": ""},
        })
        blocked = [row for row in prepared["rows"] if row["status"] in {"오류", "검토"}]
        assert len(blocked) == 1 and prepared["can_apply"] is False
        excluded = request_json(f"/uploads/{batch_id}/rows/{blocked[0]['id']}/exclude", None, "POST")
        assert excluded["can_apply"] is True
        assert excluded["status_counts"] == {"처리": 1, "제외": 1, "오류": 0, "검토": 0}
        restored = request_json(f"/uploads/{batch_id}/validation")
        assert restored["can_apply"] is True and restored["status_counts"]["제외"] == 1
        applied = request_json(f"/uploads/{batch_id}/apply", {}, "POST")
        assert applied["created_orders"] == 1
        deleted = request_json(f"/upload-materials/batch/{batch_id}", None, "DELETE")
        assert deleted["deleted_order_count"] == 1
        batch_id = None
        print("통과: 확인 행 표시→사용자 제외→결정 재조회→정상 1행만 반영→검증자료 삭제")
    finally:
        if batch_id:
            try: request_json(f"/upload-materials/batch/{batch_id}", None, "DELETE")
            except Exception: pass
        with SessionLocal() as session:
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == session_id))
            session.commit()


if __name__ == "__main__":
    main()
