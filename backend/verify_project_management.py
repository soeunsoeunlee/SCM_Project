"""빈 프로젝트 생성, 프로젝트 목록, 사본 격리와 영구 삭제를 실제 HTTP로 검증합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import delete, func, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal
from app.project_service import delete_project_data


BASE = os.getenv("SCM_VERIFY_URL", "http://127.0.0.1:8000")


def call(path: str, token: str, project_id: str, *, method: str = "GET", payload: dict | None = None):
    headers = {"Cookie": f"scm_session={token}; scm_project={project_id}"}
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json; charset=utf-8"
    request = Request(BASE + path, data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers
    except HTTPError as error:
        return error.code, error.read(), error.headers


def main() -> None:
    marker = uuid4().hex[:8]
    token = f"verify-project-{uuid4().hex}"
    login_id = str(uuid4())
    created_ids: list[str] = []
    with SessionLocal() as session:
        membership = session.scalar(
            select(models.ProjectMember)
            .join(models.Order, models.Order.project_id == models.ProjectMember.project_id)
            .order_by(models.ProjectMember.id)
        )
        assert membership is not None, "주문이 있는 기준 프로젝트가 필요합니다."
        base_project_id, user_id = membership.project_id, membership.user_id
        base_order_count = session.scalar(select(func.count()).select_from(models.Order).where(models.Order.project_id == base_project_id)) or 0
        base_source_count = session.scalar(select(func.count()).select_from(models.SourceMaterial)) or 0
        session.add(models.LoginSession(id=login_id, user_id=user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)))
        session.commit()

    try:
        blank_name = f"검증용 빈 프로젝트 {marker}"
        status, raw, headers = call("/api/projects", token, base_project_id, method="POST", payload={"name": blank_name})
        assert status == 201, raw
        blank_id = json.loads(raw)["project"]["id"]
        created_ids.append(blank_id)
        assert "scm_project=" in (headers.get("Set-Cookie") or "")

        status, raw, _ = call("/api/auth/status", token, blank_id)
        assert status == 200, raw
        blank = next(item for item in json.loads(raw)["projects"] if item["id"] == blank_id)
        assert (blank["order_count"], blank["upload_count"], blank["psi_count"]) == (0, 0, 0)

        status, _, _ = call(f"/api/projects/{blank_id}", token, blank_id, method="DELETE", payload={"confirmation_name": "다른 이름"})
        assert status == 400
        status, raw, _ = call(f"/api/projects/{blank_id}", token, blank_id, method="DELETE", payload={"confirmation_name": blank_name})
        assert status == 200, raw
        created_ids.remove(blank_id)
        assert json.loads(raw)["next_project_id"] != blank_id

        copy_name = f"검증용 프로젝트 사본 {marker}"
        status, raw, _ = call("/api/projects/copy", token, base_project_id, method="POST", payload={"name": copy_name})
        assert status == 201, raw
        copied = json.loads(raw)
        copy_id = copied["project"]["id"]
        created_ids.append(copy_id)
        assert copied["copied_orders"] == base_order_count

        status, raw, _ = call("/api/auth/status", token, copy_id)
        copy_row = next(item for item in json.loads(raw)["projects"] if item["id"] == copy_id)
        assert copy_row["order_count"] == base_order_count and copy_row["copied_from_id"] == base_project_id

        status, raw, _ = call(f"/api/projects/{copy_id}", token, copy_id, method="DELETE", payload={"confirmation_name": copy_name})
        assert status == 200, raw
        created_ids.remove(copy_id)

        with SessionLocal() as session:
            assert session.get(models.Project, blank_id) is None
            assert session.get(models.Project, copy_id) is None
            assert (session.scalar(select(func.count()).select_from(models.Order).where(models.Order.project_id == base_project_id)) or 0) == base_order_count
            assert (session.scalar(select(func.count()).select_from(models.SourceMaterial)) or 0) == base_source_count

        print(json.dumps({
            "result": "PASS",
            "blank_project_zero_data": True,
            "name_confirmation": True,
            "copy_deleted": True,
            "original_orders_preserved": True,
            "shared_sources_preserved": True,
        }, ensure_ascii=False))
    finally:
        with SessionLocal() as session:
            for project_id in created_ids:
                if session.get(models.Project, project_id) is not None:
                    delete_project_data(session, project_id)
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.commit()


if __name__ == "__main__":
    main()
