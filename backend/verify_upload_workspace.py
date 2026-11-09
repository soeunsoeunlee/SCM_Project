"""실무형 업로드 화면의 자료유형·이력·원본 다운로드를 실제 HTTP로 검증합니다."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import load_workbook
from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal
from app.sample_files import get_sample

BASE = "http://127.0.0.1:8000"


def post_file(token: str, project_id: str, data_type: str, content: bytes) -> dict:
    boundary = f"----Workspace{uuid4().hex}"
    fields = {"data_type": data_type, "sales_company_id": "sales-a", "target_month": "2099-01", "reference_date": "2026-10-30", "scope_type": "전체 현황", "scope_description": f"{data_type} 업로드 화면 자동 확인"}
    body = bytearray()
    for name, value in fields.items():
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="workspace.xlsx"\r\nContent-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n'.encode())
    body.extend(content); body.extend(f"\r\n--{boundary}--\r\n".encode())
    request = Request(f"{BASE}/api/uploads/inspect", data=bytes(body), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Cookie": f"scm_session={token}; scm_project={project_id}"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def get(token: str, project_id: str, path: str) -> tuple[str, bytes]:
    request = Request(BASE + path, headers={"Cookie": f"scm_session={token}; scm_project={project_id}"})
    with urlopen(request, timeout=20) as response:
        return response.headers.get_content_type(), response.read()


def main() -> None:
    token, login_id = f"verify-workspace-{uuid4()}", str(uuid4())
    batch_ids: list[str] = []
    source_ids: list[str] = []
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        project_id = membership.project_id
        session.add(models.LoginSession(id=login_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        session.commit()
    _, sample = get_sample("northstar-normal")
    book = load_workbook(BytesIO(sample)); book.properties.subject = f"workspace-{uuid4()}"; stream = BytesIO(); book.save(stream); content = stream.getvalue()
    try:
        production = post_file(token, project_id, "생산정보", content)
        shipment = post_file(token, project_id, "선적정보", content)
        production_repeat = post_file(token, project_id, "생산정보", content)
        for result in (production, shipment):
            batch_ids.append(result["batch"]["id"]); source_ids.append(result["batch"]["download_url"].split("/")[-2])
        assert production["batch"]["id"] != shipment["batch"]["id"]
        assert production_repeat["overwritten"] is True and production_repeat["batch"]["id"] == production["batch"]["id"]
        content_type, history_raw = get(token, project_id, "/api/uploads/history")
        history = json.loads(history_raw)
        assert content_type == "application/json" and all(any(row["id"] == batch_id for row in history) for batch_id in batch_ids)
        _, downloaded = get(token, project_id, production["batch"]["download_url"])
        assert downloaded == content
        print(json.dumps({"result": "PASS", "types": [production["batch"]["data_type"], shipment["batch"]["data_type"]], "history_found": True, "download_equal": True}, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if batch_ids:
                session.execute(delete(models.UploadBatch).where(models.UploadBatch.id.in_(batch_ids)))
            if source_ids:
                session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id.in_(source_ids)))
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
