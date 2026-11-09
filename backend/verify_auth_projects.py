"""로그인·프로젝트 복사·비정형 입력의 실제 API 흐름을 확인합니다.

임시 자료만 만들며 finally에서 테스트 계정·프로젝트·자료를 정확히 정리합니다.
"""

from __future__ import annotations

from http.cookiejar import CookieJar
import json
import secrets
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener
from uuid import uuid4

from sqlalchemy import delete, select

from app import models
from app.database import SessionLocal
from app.sample_files import get_sample


# 실제 브라우저와 같은 쿠키 저장소를 가진 HTTP 클라이언트를 준비합니다.
BASE = "http://127.0.0.1:8000/api"
email = f"verification-{uuid4().hex[:8]}@example.test"
password = secrets.token_urlsafe(20)
created_project_id: str | None = None
created_source_ids: list[str] = []
user_id: str | None = None
opener = build_opener(HTTPCookieProcessor(CookieJar()))


def request(path: str, payload: dict | None = None, method: str | None = None) -> dict | list | bytes:
    """쿠키를 유지하며 GET/JSON POST를 보내고 JSON 또는 파일 바이트를 읽습니다."""
    data = json.dumps(payload).encode() if payload is not None else None
    req = Request(BASE + path, data=data, method=method or ("POST" if payload is not None else "GET"), headers={"Content-Type": "application/json"} if data else {})
    try:
        with opener.open(req) as response:
            body = response.read()
            return json.loads(body) if response.headers.get_content_type() == "application/json" else body
    except HTTPError as error:
        print(error.read().decode("utf-8", errors="replace"))
        raise


def multipart(path: str, fields: dict[str, str], file: tuple[str, str, bytes] | None = None) -> dict:
    """텍스트나 파일을 실제 multipart/form-data 형식으로 업로드합니다."""
    boundary = f"----scm{uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode())
    if file:
        filename, content_type, content = file
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: {content_type}\r\n\r\n".encode())
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    req = Request(BASE + path, data=bytes(body), method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with opener.open(req) as response:
        return json.loads(response.read())


def cleanup_verification_artifacts() -> None:
    """이 스크립트가 만든 검증용 자료만 ID·이름 조건으로 찾아 삭제합니다."""
    with SessionLocal() as session:
        test_projects = list(session.scalars(select(models.Project.id).where(models.Project.name == "검증용 프로젝트 사본")))
        for project_id in test_projects:
            manual_sources = list(session.scalars(select(models.Order.source_id).where(models.Order.project_id == project_id, models.Order.model == "VERIFY-MODEL")))
            batch_ids = list(session.scalars(select(models.UploadBatch.id).where(models.UploadBatch.project_id == project_id)))
            batch_sources = list(session.scalars(select(models.UploadBatch.source_id).where(models.UploadBatch.project_id == project_id)))
            order_ids = list(session.scalars(select(models.Order.id).where(models.Order.project_id == project_id)))
            if order_ids:
                session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.order_id.in_(order_ids)))
                session.execute(delete(models.ProductionAllocation).where(models.ProductionAllocation.order_id.in_(order_ids)))
                session.execute(delete(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id.in_(order_ids)))
                session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.order_id.in_(order_ids)))
                session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.order_id.in_(order_ids)))
                session.execute(delete(models.Order).where(models.Order.id.in_(order_ids)))
            version_ids = list(session.scalars(select(models.PlanVersion.id).where(models.PlanVersion.project_id == project_id)))
            if version_ids:
                session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.plan_version_id.in_(version_ids)))
                session.execute(delete(models.PlanVersion).where(models.PlanVersion.id.in_(version_ids)))
            if batch_ids:
                session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id.in_(batch_ids)))
                session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id.in_(batch_ids)))
                session.execute(delete(models.UploadBatch).where(models.UploadBatch.id.in_(batch_ids)))
            session.execute(delete(models.MappingProfile).where(models.MappingProfile.project_id == project_id))
            removable_sources = manual_sources + batch_sources
            if removable_sources:
                session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id.in_(removable_sources)))
            session.execute(delete(models.ProjectMember).where(models.ProjectMember.project_id == project_id))
            session.execute(delete(models.Project).where(models.Project.id == project_id))
        test_inputs = list(session.scalars(select(models.UnstructuredInput).where(
            (models.UnstructuredInput.extracted_text.contains("00012345")) | (models.UnstructuredInput.file_name == "mail-capture.png")
        )))
        for record in test_inputs:
            source_id = record.source_id
            session.delete(record)
            session.flush()
            session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == source_id))
        test_users = list(session.scalars(select(models.User).where(models.User.email.like("verification-%@example.test"))))
        for user in test_users:
            session.execute(delete(models.LoginSession).where(models.LoginSession.user_id == user.id))
            session.execute(delete(models.ProjectMember).where(models.ProjectMember.user_id == user.id))
            project = session.get(models.Project, "project-demo")
            if project and project.owner_user_id == user.id:
                project.owner_user_id = None
            session.flush()
            session.delete(user)
        session.commit()


try:
    cleanup_verification_artifacts()
    initial = request("/auth/status")
    assert initial["needs_setup"] is True
    request("/auth/setup", {"display_name": "검증 사용자", "email": email, "password": password, "project_name": "202610 SCM 프로세스 관리프로젝트"})
    status = request("/auth/status")
    user_id = status["user"]["id"]
    orders = request("/orders")
    assert len(orders) == 13
    source_bytes = request("/source-materials/source-demo-a/download")
    assert isinstance(source_bytes, bytes) and len(source_bytes) > 1000

    copied = request("/projects/copy", {"name": "검증용 프로젝트 사본"})
    created_project_id = copied["project"]["id"]
    assert copied["copied_orders"] == 13
    request("/projects/select", {"project_id": created_project_id})
    assert len(request("/orders")) == 13
    sample_meta, sample_content = get_sample("northstar-normal")
    upload = multipart("/uploads/inspect", {"data_type": "판매계획", "sales_company_id": "sales-a", "target_month": "2097-01", "reference_date": "2026-10-29", "scope_type": "전체 현황", "scope_description": "프로젝트 분리 검증"}, (sample_meta["file_name"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", sample_content))
    mapping = request(f"/uploads/{upload['batch']['id']}/mapping", {"sheet_name": "Sales Plan", "header_row": 3})
    prepared = request(f"/uploads/{upload['batch']['id']}/prepare", {"sheet_name": "Sales Plan", "header_row": 3, "mapping": mapping["suggested_mapping"], "pricing": {"net_sales": "단가", "gross_sales": "총액"}})
    assert prepared["can_apply"] is True
    applied = request(f"/uploads/{upload['batch']['id']}/apply", {})
    assert applied["created_orders"] == 3
    assert len(request("/orders")) == 16
    request("/orders", {"sales_company_id": "sales-a", "production_company_id": "plant-a", "target_month": "2027-02", "po_number": "00009999", "model": "VERIFY-MODEL", "customer": "Verification", "quantity": 1, "currency": "USD", "unit_price": 1})
    assert len(request("/orders")) == 17
    request("/projects/select", {"project_id": "project-demo"})
    assert len(request("/orders")) == 13

    text_result = multipart("/unstructured/inspect", {"data_type": "생산정보", "pasted_text": "PO: 00012345\nModel: OLED77-C4.AUS\nQty: 20\nCustomer: Demo Harbor"})
    created_source_ids.append(text_result["source_id"])
    assert text_result["extracted_fields"]["po_number"] == "00012345"
    assert text_result["extracted_fields"]["quantity"] == "20"
    image_result = multipart("/unstructured/inspect", {"data_type": "선적정보", "pasted_text": ""}, ("mail-capture.png", "image/png", b"\x89PNG\r\n\x1a\nverification"))
    created_source_ids.append(image_result["source_id"])
    assert "원본 보존" in image_result["extraction_status"]
    print("통과: 최초 로그인, 세션, 프로젝트 13건 복사, 프로젝트별 주문 분리, 원본 다운로드, 텍스트 추출, 이미지 원본 보존")
finally:
    cleanup_verification_artifacts()
