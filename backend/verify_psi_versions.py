"""Verify PSI upload, comparison, review decision, and cleanup over HTTP."""

from datetime import datetime, timedelta, timezone
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import delete, func, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal

BASE_URL = "http://127.0.0.1:8000"


def call(path: str, token: str, project_id: str, *, method: str = "GET", body: bytes | None = None, content_type: str | None = None):
    headers = {"Cookie": f"scm_session={token}; scm_project={project_id}"}
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(f"{BASE_URL}{path}", data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def multipart(fields: dict[str, str], file_name: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----SCMVerify{uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([f"--{boundary}\r\n".encode(), f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(), value.encode(), b"\r\n"])
    chunks.extend([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'.encode(),
        b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n",
        content, b"\r\n", f"--{boundary}--\r\n".encode(),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def upload(token: str, project_id: str, file_name: str, content: bytes) -> tuple[int, dict]:
    body, content_type = multipart({"reference_date": "2026-10-29"}, file_name, content)
    status, _, payload = call("/api/psi/versions", token, project_id, method="POST", body=body, content_type=content_type)
    return status, json.loads(payload)


def main() -> None:
    token, login_id = f"verify-psi-{uuid4()}", str(uuid4())
    created_version_ids: list[str] = []
    created_source_ids: list[str] = []
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None, "No project exists for verification."
        project_id = membership.project_id
        before_orders = session.scalar(select(func.count(models.Order.id)).where(models.Order.project_id == project_id)) or 0
        session.add(models.LoginSession(id=login_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        session.commit()
    try:
        base_status, _, base_file = call("/api/psi/samples/base", token, project_id)
        revised_status, _, revised_file = call("/api/psi/samples/revised", token, project_id)
        assert base_status == 200 and revised_status == 200

        status, base_json = upload(token, project_id, "verify-psi-base.xlsx", base_file)
        assert status == 201, base_json
        created_version_ids.append(base_json["version"]["id"])
        assert base_json["version"]["base_month"] == "2026-10" and base_json["version"]["created_by"]
        assert base_json["version"]["label"] == "verify-psi-base.xlsx"
        assert base_json["version"]["status"] == "검토 대기" and base_json["version"]["value_count"] > 0
        duplicate_status, duplicate_json = upload(token, project_id, "renamed.xlsx", base_file)
        assert duplicate_status == 201 and duplicate_json["overwritten"] is True
        assert duplicate_json["version"]["id"] == base_json["version"]["id"]
        assert duplicate_json["version"]["file_name"] == "renamed.xlsx"

        approved_status, _, approved_body = call(f"/api/psi/versions/{base_json['version']['id']}/approve", token, project_id, method="POST", body=b"")
        assert approved_status == 200 and json.loads(approved_body)["version"]["status"] == "승인"
        repeat_status, _, repeat_body = call(f"/api/psi/versions/{base_json['version']['id']}/approve", token, project_id, method="POST", body=b"")
        assert repeat_status == 200 and json.loads(repeat_body)["duplicate_request"] is True

        revised_status, revised_json = upload(token, project_id, "verify-psi-revised.xlsx", revised_file)
        assert revised_status == 201, revised_json
        created_version_ids.append(revised_json["version"]["id"])
        assert revised_json["previous_version"]["id"] == base_json["version"]["id"]
        assert revised_json["comparison"]["changed_cells"] >= 2 and revised_json["risk_signals"]
        assert revised_json["risk_reviews"], "Risk review rows were not created."

        review = next((item for item in revised_json["risk_reviews"] if item["candidates"]), None)
        assert review is not None, "No safe order candidate was found for a PSI signal."
        chosen_order = review["candidates"][0]
        decision = json.dumps({"decision": "승인", "allocations": [{"order_id": chosen_order["id"], "quantity": review["quantity"]}], "note": "HTTP verification"}, ensure_ascii=False).encode("utf-8")
        decision_status, _, decision_body = call(f"/api/psi/risk-reviews/{review['id']}/decision", token, project_id, method="POST", body=decision, content_type="application/json; charset=utf-8")
        assert decision_status == 200, decision_body.decode("utf-8")
        assert json.loads(decision_body)["review"]["status"] == "승인"

        orders_status, _, orders_body = call("/api/orders", token, project_id)
        assert orders_status == 200
        order_json = next(item for item in json.loads(orders_body) if item["id"] == chosen_order["id"])
        assert any(reason.startswith("PSI 승인:") and "배분" in reason for reason in order_json["risk_reasons"])
        assert order_json["risk_quantity"] >= review["quantity"]

        export_status, _, exported = call(f"/api/psi/versions/{revised_json['version']['id']}/export", token, project_id)
        download_status, _, downloaded = call(f"/api/psi/versions/{revised_json['version']['id']}/download", token, project_id)
        assert export_status == 200 and exported[:2] == b"PK"
        assert download_status == 200 and downloaded == revised_file
        with SessionLocal() as session:
            after_orders = session.scalar(select(func.count(models.Order.id)).where(models.Order.project_id == project_id)) or 0
            assert after_orders == before_orders, "PSI processing changed the Sales Master order count."
            created_source_ids = list(session.scalars(select(models.PsiVersion.source_id).where(models.PsiVersion.id.in_(created_version_ids))))
        print(json.dumps({"result": "PASS", "versions": len(created_version_ids), "changed_cells": revised_json["comparison"]["changed_cells"], "risk_signals": len(revised_json["risk_signals"]), "risk_reviews": len(revised_json["risk_reviews"]), "approved_order": chosen_order["order_number"], "orders_preserved": before_orders}, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if created_version_ids:
                review_ids = select(models.PsiRiskReview.id).where(models.PsiRiskReview.version_id.in_(created_version_ids))
                session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.review_id.in_(review_ids)))
                session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.source_id.in_(select(models.PsiVersion.source_id).where(models.PsiVersion.id.in_(created_version_ids)))))
                session.execute(delete(models.PsiRiskReview).where(models.PsiRiskReview.version_id.in_(created_version_ids)))
                session.execute(delete(models.PsiValue).where(models.PsiValue.version_id.in_(created_version_ids)))
                session.execute(delete(models.PsiVersion).where(models.PsiVersion.id.in_(created_version_ids)))
                session.flush()
            if created_source_ids:
                session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id.in_(created_source_ids)))
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
