"""Closed 수동 확인, 변경 이력, 연동 상태를 실제 HTTP 요청으로 검증합니다."""

from datetime import datetime, timedelta, timezone
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import delete, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal

BASE_URL = os.getenv("SCM_VERIFY_URL", "http://127.0.0.1:8000")


def call(path: str, token: str, project_id: str, *, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    request = Request(f"{BASE_URL}{path}", data=body, method=method, headers={"Cookie": f"scm_session={token}; scm_project={project_id}", "Content-Type": "application/json; charset=utf-8"})
    try:
        with urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read() or b"{}")
    except HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return error.code, {"detail": raw.decode("utf-8", errors="replace") or error.reason}


def main() -> None:
    token, login_id = f"verify-operations-{uuid4()}", str(uuid4())
    confirmation_id = source_id = None
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        project_id = membership.project_id
        orders = list(session.scalars(select(models.Order).where(models.Order.project_id == project_id)))
        order = next(item for item in orders if item.quantity - sum(row.confirmed_quantity for row in item.erp_confirmations if row.status != "취소·정정") > 0)
        previous_status = order.erp_status
        before_closed = sum(row.confirmed_quantity for row in order.erp_confirmations if row.status != "취소·정정")
        session.add(models.LoginSession(id=login_id, user_id=membership.user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)))
        session.commit()
        order_id = order.id
    try:
        status, result = call(f"/api/orders/{order_id}/erp-confirmations", token, project_id, method="POST", payload={"status": "일부 완료", "confirmed_quantity": 1, "confirmation_date": "2026-10-29", "note": "자동 검증 후 삭제"})
        assert status == 201, result
        confirmation_id = result["id"]
        assert result["closed_quantity"] == before_closed + 1
        history_status, history = call("/api/history", token, project_id)
        assert history_status == 200 and any(row["order_id"] == order_id and row["event_type"] == "ERP Closed 확인" for row in history)
        integration_status, integration = call("/api/integration-status", token, project_id)
        assert integration_status == 200
        assert next(item for item in integration["connectors"] if item["name"] == "Gmail")["status"] == "연결 안 됨"
        # Excel 응답은 JSON이 아니므로 별도 요청으로 파일 서명을 확인합니다.
        request = Request(f"{BASE_URL}/api/history.xlsx", headers={"Cookie": f"scm_session={token}; scm_project={project_id}"})
        with urlopen(request, timeout=20) as response:
            excel = response.read()
            assert response.status == 200 and excel[:2] == b"PK"
        print(json.dumps({"result": "PASS", "order_id": order_id, "closed_before": before_closed, "closed_after": result["closed_quantity"], "history_count": len(history)}, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            if confirmation_id:
                confirmation = session.get(models.ErpConfirmation, confirmation_id)
                if confirmation:
                    source_id = confirmation.source_id
                session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.source_id == source_id))
                session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.id == confirmation_id))
                session.flush()
                if source_id:
                    session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == source_id))
                stored_order = session.get(models.Order, order_id)
                if stored_order:
                    stored_order.erp_status = previous_status
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
