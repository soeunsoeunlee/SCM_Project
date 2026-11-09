"""판매자료의 실제 파싱·매핑·검증·반영·중복 방지를 끝까지 확인합니다."""
from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from openpyxl import load_workbook

from app.sample_files import get_sample
from app.upload_service import inspect_workbook, mapping_context, standardize_rows
from app.database import SessionLocal
from app import models
from sqlalchemy import select


BASE_URL = "http://127.0.0.1:8000"


def get_json(path: str):
    """로컬 API의 JSON을 GET으로 읽습니다."""
    with urlopen(BASE_URL + path, timeout=10) as response:
        return json.load(response)


def post_json(path: str, payload: dict, expected: int = 200):
    """JSON POST 응답이 예상 상태코드인지 확인합니다."""
    request = Request(BASE_URL + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            assert response.status == expected, (response.status, path)
            return json.load(response)
    except HTTPError as error:
        if error.code == expected:
            return json.load(error)
        raise


def post_file(path: str, content: bytes, filename: str, fields: dict):
    """브라우저 파일 업로드와 같은 multipart 요청을 직접 만듭니다."""
    boundary = f"----SCM{uuid4().hex}"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode("utf-8"))
    body.extend(content)
    body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
    request = Request(BASE_URL + path, data=bytes(body), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}, method="POST")
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def variant(sample_id: str, marker: str) -> tuple[bytes, str]:
    """공개 샘플 DB 상태에 영향을 주지 않도록 문서 속성만 다른 검증 파일을 만듭니다."""
    metadata, content = get_sample(sample_id)
    book = load_workbook(BytesIO(content))
    book.properties.subject = marker
    output = BytesIO()
    book.save(output)
    return output.getvalue(), metadata["file_name"]


def unused_month(company_id: str) -> str:
    """기존 주문과 충돌하지 않는 먼 미래 대상 월을 찾습니다."""
    with SessionLocal() as session:
        used = set(session.scalars(select(models.Order.target_month).where(
            models.Order.sales_company_id == company_id,
            models.Order.upload_batch_id.is_not(None),
        )))
    for year in range(2098, 2029, -1):
        for month in range(12, 0, -1):
            candidate = f"{year}-{month:02d}"
            if candidate not in used:
                return candidate
    raise RuntimeError("검증에 사용할 빈 대상 월이 없습니다.")


def main() -> None:
    """정상·오류·후속 파일의 전체 업로드 시나리오를 검증합니다."""
    # Parse every supported format from actual bytes before API verification.
    for sample_id in ("northstar-normal", "bluewave-normal", "sunrise-normal", "validation-errors"):
        metadata, content = get_sample(sample_id)
        extension = "." + metadata["file_name"].rsplit(".", 1)[1]
        sheets = inspect_workbook(content, extension)
        assert sheets and sheets[0]["row_count"] >= 1

    target_month = unused_month("sales-a")
    fields = {
        "data_type": "판매계획", "sales_company_id": "sales-a", "target_month": target_month, "reference_date": "2026-10-28",
        "scope_type": "전체 현황", "scope_description": "자동 검증 전용 합성 범위",
    }
    content, filename = variant("northstar-normal", f"upload-verification-first-{target_month}")
    inspected = post_file("/api/uploads/inspect", content, "verify_" + filename, fields)
    batch_id = inspected["batch"]["id"]
    mapping = post_json(f"/api/uploads/{batch_id}/mapping", {"sheet_name": "Sales Plan", "header_row": 3})
    assert mapping["suggested_mapping"]["po_number"] == 0
    prepared = post_json(f"/api/uploads/{batch_id}/prepare", {
        "sheet_name": "Sales Plan", "header_row": 3, "mapping": mapping["suggested_mapping"],
        "pricing": {"net_sales": "단가", "gross_sales": "총액"},
    })
    assert prepared["can_apply"] is True
    assert prepared["status_counts"] == {"처리": 3, "제외": 0, "오류": 0, "검토": 0}
    assert prepared["rows"][0]["po_number"] == "00070031"
    assert prepared["rows"][0]["model"] == "OLED77-C4.AUS"
    assert prepared["reconciliation"][0]["original_quantity"] == 28
    assert prepared["reconciliation"][0]["original_net"] == 32320
    orders_before = len(get_json("/api/orders"))
    applied = post_json(f"/api/uploads/{batch_id}/apply", {})
    assert applied["created_orders"] == 3 and applied["batch"]["status"] == "반영 완료"
    assert len(get_json("/api/orders")) == orders_before + 3
    repeated = post_json(f"/api/uploads/{batch_id}/apply", {})
    assert repeated["duplicate_request"] is True
    assert len(get_json("/api/orders")) == orders_before + 3
    duplicate_file = post_file("/api/uploads/inspect", content, "verify_again.xlsx", fields)
    assert duplicate_file["duplicate_file"] is True and duplicate_file["batch"]["id"] == batch_id

    # A second file with the same company/month is preserved but not added as duplicate orders.
    follow_content, _ = variant("northstar-normal", f"upload-verification-follow-up-{target_month}")
    follow = post_file("/api/uploads/inspect", follow_content, "verify_follow_up.xlsx", fields)
    follow_mapping = post_json(f"/api/uploads/{follow['batch']['id']}/mapping", {"sheet_name": "Sales Plan", "header_row": 3})
    assert follow_mapping["profile_reused"] is True
    follow_prepared = post_json(f"/api/uploads/{follow['batch']['id']}/prepare", {
        "sheet_name": "Sales Plan", "header_row": 3, "mapping": follow_mapping["saved_mapping"],
        "pricing": follow_mapping["saved_pricing"],
    })
    assert follow_prepared["application_mode"] == "버전 비교 대기"
    follow_applied = post_json(f"/api/uploads/{follow['batch']['id']}/apply", {})
    assert follow_applied["created_orders"] == 0 and follow_applied["batch"]["status"] == "버전 비교 대기"
    assert len(get_json("/api/orders")) == orders_before + 3

    # Error sample must show every category and block the entire transaction.
    error_target_month = unused_month("sales-b")
    error_content, error_filename = variant("validation-errors", f"upload-verification-errors-{error_target_month}")
    error_fields = {**fields, "sales_company_id": "sales-b", "target_month": error_target_month}
    error_batch = post_file("/api/uploads/inspect", error_content, "verify_" + error_filename, error_fields)
    error_mapping = post_json(f"/api/uploads/{error_batch['batch']['id']}/mapping", {"sheet_name": "Error Cases", "header_row": 3})
    error_prepared = post_json(f"/api/uploads/{error_batch['batch']['id']}/prepare", {
        "sheet_name": "Error Cases", "header_row": 3, "mapping": error_mapping["suggested_mapping"],
        "pricing": {"net_sales": "단가", "gross_sales": "총액"},
    })
    assert error_prepared["can_apply"] is False
    assert error_prepared["status_counts"]["오류"] >= 2
    assert error_prepared["status_counts"]["검토"] >= 3
    assert error_prepared["status_counts"]["제외"] == 1
    blocked = post_json(f"/api/uploads/{error_batch['batch']['id']}/apply", {}, expected=409)
    assert "부분 저장" in blocked["detail"]
    assert len(get_json("/api/orders")) == orders_before + 3
    print("통과: XLSX/XLS/CSV 파싱, 매핑 재사용, 행 검증, 통화별 대사, 최초 반영, 후속 비교 대기, 파일·버튼 중복 방지, 트랜잭션 차단")


if __name__ == "__main__":
    main()
