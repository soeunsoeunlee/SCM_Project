"""같은 비정형 원본을 생산·선적 유형으로 각각 저장할 수 있는지 검증합니다."""

from datetime import datetime, timedelta, timezone
import json
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal


def upload(token: str, project_id: str, data_type: str, text: str) -> dict:
    boundary = f"----SCMType{uuid4().hex}"
    body = bytearray()
    for name, value in (("data_type", data_type), ("pasted_text", text)):
        body.extend(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8"))
    body.extend(f"--{boundary}--\r\n".encode())
    request = Request("http://127.0.0.1:8000/api/unstructured/inspect", data=bytes(body), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Cookie": f"scm_session={token}; scm_project={project_id}"})
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def main() -> None:
    marker = f"TYPE-VERIFY-{uuid4().hex}"
    token, login_id = f"verify-types-{uuid4()}", str(uuid4())
    source_ids: list[str] = []
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        project_id = membership.project_id
        session.add(models.LoginSession(id=login_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        session.commit()
    try:
        text = f"PO: {marker}\nModel: VERIFY-MODEL\nQty: 1"
        production = upload(token, project_id, "생산정보", text)
        shipment = upload(token, project_id, "선적정보", text)
        production_repeat = upload(token, project_id, "생산정보", text)
        source_ids.extend([production["source_id"], shipment["source_id"]])
        assert production["data_type"] == "생산정보" and shipment["data_type"] == "선적정보"
        assert production["source_id"] != shipment["source_id"]
        assert production_repeat["overwritten"] is True and production_repeat["source_id"] == production["source_id"]
        print(json.dumps({"result": "PASS", "production_source": production["source_id"], "shipment_source": shipment["source_id"], "same_type_overwritten": True}, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if source_ids:
                session.execute(delete(models.UnstructuredInput).where(models.UnstructuredInput.source_id.in_(source_ids)))
                session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id.in_(source_ids)))
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
