"""PSI·생산·선적 업로드의 덮어쓰기/영구 삭제 정책을 실제 HTTP로 검증합니다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import load_workbook
from sqlalchemy import delete, func, select

from app import models
from app.auth import token_digest
from app.database import SessionLocal
from app.upload_deletion import delete_psi_version, delete_upload_material


BASE_URL = "http://127.0.0.1:8000"


def call(path: str, token: str | None, project_id: str | None, *, method: str = "GET", body: bytes | None = None, content_type: str | None = None):
    headers: dict[str, str] = {}
    if token and project_id:
        headers["Cookie"] = f"scm_session={token}; scm_project={project_id}"
    if content_type:
        headers["Content-Type"] = content_type
    request = Request(f"{BASE_URL}{path}", data=body, method=method, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def multipart(fields: dict[str, str], file_name: str, content: bytes) -> tuple[bytes, str]:
    boundary = f"----SCMDelete{uuid4().hex}"
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


def upload_psi(token: str, project_id: str, file_name: str, content: bytes, reference_date: str) -> tuple[int, dict]:
    body, content_type = multipart({"reference_date": reference_date}, file_name, content)
    status, _, payload = call("/api/psi/versions", token, project_id, method="POST", body=body, content_type=content_type)
    return status, json.loads(payload)


def upload_batch(token: str, project_id: str, data_type: str, file_name: str, content: bytes, reference_date: str, target_month: str) -> tuple[int, dict]:
    body, content_type = multipart({
        "data_type": data_type,
        "sales_company_id": "sales-a",
        "target_month": target_month,
        "reference_date": reference_date,
        "scope_type": "전체 현황",
        "scope_description": f"{data_type} 삭제 검증",
    }, file_name, content)
    status, _, payload = call("/api/uploads/inspect", token, project_id, method="POST", body=body, content_type=content_type)
    return status, json.loads(payload)


def upload_unstructured(token: str, project_id: str, data_type: str, file_name: str, content: bytes) -> tuple[int, dict]:
    body, content_type = multipart({"data_type": data_type}, file_name, content)
    status, _, payload = call("/api/unstructured/inspect", token, project_id, method="POST", body=body, content_type=content_type)
    return status, json.loads(payload)


def unique_workbook(content: bytes, title: str, extra_delta: int = 0) -> bytes:
    book = load_workbook(BytesIO(content))
    book.properties.title = title
    if extra_delta:
        sheet = book["PSI"]
        for row in range(3, sheet.max_row + 1):
            if sheet.cell(row, 4).value == "DM":
                for column in range(5, sheet.max_column + 1):
                    value = sheet.cell(row, column).value
                    if isinstance(value, (int, float)):
                        sheet.cell(row, column).value = value + extra_delta
                        break
                break
    output = BytesIO(); book.save(output); return output.getvalue()


def business_snapshot(project_id: str) -> dict:
    """삭제가 바꾸면 안 되는 주문·금액·일정·ERP Closed 값을 비교합니다."""
    with SessionLocal() as session:
        orders = session.scalars(select(models.Order).where(models.Order.project_id == project_id).order_by(models.Order.id)).all()
        order_ids = [order.id for order in orders]
        return {
            "orders": [(o.id, o.quantity, str(o.net_sales_amount), str(o.gross_sales_amount), o.erp_status) for o in orders],
            "production": [(r.id, r.order_id, r.quantity, r.status, str(r.production_date)) for r in session.scalars(select(models.ProductionAllocation).where(models.ProductionAllocation.order_id.in_(order_ids)).order_by(models.ProductionAllocation.id))],
            "shipment": [(r.id, r.order_id, r.quantity, r.status, str(r.shipment_date), str(r.eta_date)) for r in session.scalars(select(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id.in_(order_ids)).order_by(models.ShipmentAllocation.id))],
            "erp": [(r.id, r.order_id, r.status, r.confirmed_quantity, str(r.confirmation_date)) for r in session.scalars(select(models.ErpConfirmation).where(models.ErpConfirmation.order_id.in_(order_ids)).order_by(models.ErpConfirmation.id))],
        }


def main() -> None:
    marker = uuid4().hex
    token, login_id = f"verify-delete-{uuid4()}", str(uuid4())
    other_project_id = f"vp-{uuid4().hex[:32]}"
    tracked_psi: set[str] = set()
    tracked_batches: set[str] = set()
    tracked_unstructured: set[str] = set()
    with SessionLocal() as session:
        membership = session.scalar(select(models.ProjectMember).order_by(models.ProjectMember.id))
        assert membership is not None
        project_id = membership.project_id
        user_id = membership.user_id
        session.add_all([
            models.Project(id=other_project_id, name="삭제 권한 검증용 임시 프로젝트", owner_user_id=user_id),
            models.ProjectMember(user_id=user_id, project_id=other_project_id, role="owner"),
            models.LoginSession(id=login_id, user_id=user_id, token_hash=token_digest(token), expires_at=datetime.now(timezone.utc) + timedelta(minutes=15)),
        ])
        session.commit()
    before = business_snapshot(project_id)
    try:
        base_status, _, base_raw = call("/api/psi/samples/base", token, project_id)
        revised_status, _, revised_raw = call("/api/psi/samples/revised", token, project_id)
        assert base_status == revised_status == 200
        psi_a = unique_workbook(base_raw, f"{marker}-A")
        psi_b = unique_workbook(revised_raw, f"{marker}-B")
        psi_c = unique_workbook(revised_raw, f"{marker}-C", 1)

        # PSI 같은 해시 재업로드: 같은 ID 한 건을 최신 파일명·기준일·기준월로 덮어씁니다.
        status, first = upload_psi(token, project_id, f"{marker}-old.xlsx", psi_a, "2026-10-01")
        assert status == 201
        first_id = first["version"]["id"]; tracked_psi.add(first_id)
        status, overwritten = upload_psi(token, project_id, f"{marker}-renamed.xlsx", psi_a, "2026-11-15")
        assert status == 201 and overwritten["overwritten"] is True
        assert overwritten["version"]["id"] == first_id
        assert overwritten["version"]["file_name"] == f"{marker}-renamed.xlsx"
        assert overwritten["version"]["reference_date"] == "2026-11-15" and overwritten["version"]["base_month"] == "2026-11"
        with SessionLocal() as session:
            first_hash = session.get(models.PsiVersion, first_id).file_hash
            assert session.scalar(select(func.count(models.PsiVersion.id)).where(models.PsiVersion.project_id == project_id, models.PsiVersion.file_hash == first_hash)) == 1

        # 삭제 후 주소는 404이고 같은 파일을 다시 올리면 새 ID가 생깁니다.
        old_source = overwritten["version"]["id"]
        delete_status, _, _ = call(f"/api/psi/versions/{first_id}", token, project_id, method="DELETE")
        assert delete_status == 200
        tracked_psi.discard(first_id)
        assert call(f"/api/psi/versions/{first_id}/download", token, project_id)[0] == 404
        assert call(f"/api/psi/versions/{first_id}/export", token, project_id)[0] == 404
        status, a = upload_psi(token, project_id, f"{marker}-A.xlsx", psi_a, "2026-10-02")
        assert status == 201 and a["version"]["id"] != first_id
        a_id = a["version"]["id"]; tracked_psi.add(a_id)
        assert call(f"/api/psi/versions/{a_id}/approve", token, project_id, method="POST", body=b"")[0] == 200

        status, b = upload_psi(token, project_id, f"{marker}-B.xlsx", psi_b, "2026-10-10")
        b_id = b["version"]["id"]; tracked_psi.add(b_id)
        assert b["previous_version"]["id"] == a_id
        assert call(f"/api/psi/versions/{b_id}/approve", token, project_id, method="POST", body=b"")[0] == 200
        status, c = upload_psi(token, project_id, f"{marker}-C.xlsx", psi_c, "2026-10-20")
        c_id = c["version"]["id"]; tracked_psi.add(c_id)
        assert c["previous_version"]["id"] == b_id

        # 중간 승인본 B 삭제 후 C의 비교 기준이 남은 승인본 A로 다시 계산됩니다.
        assert call(f"/api/psi/versions/{b_id}", token, project_id, method="DELETE")[0] == 200
        tracked_psi.discard(b_id)
        _, _, c_body = call(f"/api/psi/versions/{c_id}", token, project_id)
        c_after = json.loads(c_body)
        assert c_after["previous_version"]["id"] == a_id
        assert call(f"/api/psi/versions/{a_id}/download", token, project_id)[0] == 200  # 다른 자료 보존

        # PSI 삭제 시 값·검토·배분·Sales Master RISK 근거·SourceMaterial까지 사라집니다.
        review = next(item for item in c_after["risk_reviews"] if item["candidates"])
        order = review["candidates"][0]
        decision = json.dumps({"decision": "승인", "allocations": [{"order_id": order["id"], "quantity": review["quantity"]}], "note": "delete verification"}, ensure_ascii=False).encode()
        assert call(f"/api/psi/risk-reviews/{review['id']}/decision", token, project_id, method="POST", body=decision, content_type="application/json; charset=utf-8")[0] == 200
        with SessionLocal() as session:
            c_source = session.get(models.PsiVersion, c_id).source_id
        assert call(f"/api/psi/versions/{c_id}", token, project_id, method="DELETE")[0] == 200
        tracked_psi.discard(c_id)
        with SessionLocal() as session:
            assert session.get(models.PsiVersion, c_id) is None
            assert session.scalar(select(func.count(models.PsiValue.id)).where(models.PsiValue.version_id == c_id)) == 0
            assert session.scalar(select(func.count(models.PsiRiskReview.id)).where(models.PsiRiskReview.version_id == c_id)) == 0
            assert session.scalar(select(func.count(models.ChangeEvent.id)).where(models.ChangeEvent.source_id == c_source)) == 0
            assert session.get(models.SourceMaterial, c_source) is None

        # 생산·선적 구조화 자료: 같은 해시는 덮어쓰기, 유형이 다르면 별도 한 건입니다.
        status, production = upload_batch(token, project_id, "생산정보", f"{marker}-prod-old.xlsx", psi_a, "2026-10-01", "2026-10")
        assert status == 201
        prod_id = production["batch"]["id"]; prod_source = production["batch"]["download_url"].split("/")[-2]; tracked_batches.add(prod_id)
        status, prod_over = upload_batch(token, project_id, "생산정보", f"{marker}-prod-new.xlsx", psi_a, "2026-11-20", "2026-11")
        assert status == 201 and prod_over["overwritten"] is True and prod_over["batch"]["id"] == prod_id
        assert prod_over["batch"]["file_name"] == f"{marker}-prod-new.xlsx" and prod_over["batch"]["reference_date"] == "2026-11-20"
        with SessionLocal() as session:
            assert session.get(models.UploadBatch, prod_id).created_by_user_id == user_id
        status, shipment = upload_batch(token, project_id, "선적정보", f"{marker}-ship.xlsx", psi_a, "2026-10-05", "2026-10")
        assert status == 201
        ship_id = shipment["batch"]["id"]; ship_source = shipment["batch"]["download_url"].split("/")[-2]; tracked_batches.add(ship_id)
        assert call(f"/api/upload-materials/batch/{prod_id}", token, project_id, method="DELETE")[0] == 200
        tracked_batches.discard(prod_id)
        assert call(f"/api/source-materials/{prod_source}/download", token, project_id)[0] == 404
        assert call(f"/api/source-materials/{ship_source}/download", token, project_id)[0] == 200
        status, prod_new = upload_batch(token, project_id, "생산정보", f"{marker}-prod-again.xlsx", psi_a, "2026-12-01", "2026-12")
        assert status == 201 and prod_new["batch"]["id"] != prod_id
        tracked_batches.add(prod_new["batch"]["id"])

        # 메일·이미지·텍스트 계열도 같은 공통 이력/삭제 API를 사용합니다.
        text_bytes = f"PO: {marker}\nModel: DELETE-VERIFY\nQty: 1".encode()
        status, text_first = upload_unstructured(token, project_id, "생산정보", f"{marker}-mail.txt", text_bytes)
        assert status == 201
        text_id = text_first["id"]; text_source = text_first["source_id"]; tracked_unstructured.add(text_id)
        status, text_over = upload_unstructured(token, project_id, "생산정보", f"{marker}-renamed.txt", text_bytes)
        assert status == 201 and text_over["overwritten"] is True and text_over["id"] == text_id
        with SessionLocal() as session:
            assert session.get(models.UnstructuredInput, text_id).created_by_user_id == user_id
        assert call(f"/api/upload-materials/unstructured/{text_id}", token, project_id, method="DELETE")[0] == 200
        tracked_unstructured.discard(text_id)
        assert call(f"/api/source-materials/{text_source}/download", token, project_id)[0] == 404

        # 인증 없는 삭제는 자료 유형과 관계없이 거부됩니다.
        assert call(f"/api/upload-materials/batch/{ship_id}", None, None, method="DELETE")[0] == 401
        # 같은 사용자가 참여한 다른 프로젝트를 선택해도 원본 프로젝트 자료는 찾을 수 없습니다.
        assert call(f"/api/upload-materials/batch/{ship_id}", token, other_project_id, method="DELETE")[0] == 404

        # 모든 삭제/덮어쓰기 후 주문·금액·생산/선적 일정·ERP Closed는 완전히 동일합니다.
        after = business_snapshot(project_id)
        assert after == before
        print(json.dumps({
            "result": "PASS",
            "psi_overwrite": True,
            "psi_delete_and_404": True,
            "comparison_rebased": True,
            "production_overwrite": True,
            "production_delete_and_reupload": True,
            "unstructured_overwrite_and_delete": True,
            "other_upload_preserved": True,
            "sales_master_unchanged": True,
        }, ensure_ascii=False))
    finally:
        # 테스트 도중 실패해도 이번 스크립트가 만든 자료와 세션만 정리합니다.
        with SessionLocal() as session:
            for version_id in list(tracked_psi):
                try: delete_psi_version(session, project_id, version_id)
                except Exception: session.rollback()
            for batch_id in list(tracked_batches):
                try: delete_upload_material(session, project_id, "batch", batch_id)
                except Exception: session.rollback()
            for record_id in list(tracked_unstructured):
                try: delete_upload_material(session, project_id, "unstructured", record_id)
                except Exception: session.rollback()
            session.execute(delete(models.LoginSession).where(models.LoginSession.id == login_id))
            session.execute(delete(models.ProjectMember).where(models.ProjectMember.project_id == other_project_id))
            session.execute(delete(models.Project).where(models.Project.id == other_project_id))
            session.commit()


if __name__ == "__main__":
    main()
