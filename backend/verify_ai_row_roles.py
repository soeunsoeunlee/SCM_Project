"""표시 문구 없는 중간 합계를 AI 보조와 서버 산술 검증으로 제외하는 회귀 검사입니다."""
from io import BytesIO

from openpyxl import Workbook

from app import upload_service


def workbook_bytes() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "Plan"
    sheet.append(["Customer", "Region", "PO", "Model", "Memo", "Qty", "Net", "Gross"])
    sheet.append(["A", "TR", "0001", "MODEL-A", "", 31, 57350, 63085])
    sheet.append(["B", "TR", "0002", "MODEL-B", "", 39, 72150, 79365])
    sheet.append(["C", "TR", "0003", "MODEL-C", "", 39, 72150, 79365])
    # Total이라는 표시 없이 고객/그룹명과 합계 숫자만 있는 실제 업무 양식입니다.
    sheet.append(["Group A", "", "", "", "", 109, 201650, 221815])
    sheet.append(["D", "TR", "0004", "MODEL-D", "", 20, 19200, 21120])
    sheet.append(["Group B", "", "", "", "", 20, 19200, 21120])
    sheet.append(["Grand Total", "", "", "", "", 129, 220850, 242935])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def main() -> None:
    original = upload_service.classify_row_roles
    upload_service.classify_row_roles = lambda candidates: {
        "status": "used",
        "message": "모의 AI 분류",
        "decisions": [{
            "row_number": item["row_number"],
            "role": "subtotal",
            "confidence": "high",
            "reason": "식별값이 없고 앞선 상세행 합계와 일치",
        } for item in candidates],
    }
    try:
        result = upload_service.standardize_rows(
            workbook_bytes(), ".xlsx", "Plan", 1,
            {"customer": 0, "sales_region": 1, "po_number": 2, "model": 3, "quantity": 5, "net_sales": 6, "gross_sales": 7},
            {"net_sales": "총액", "gross_sales": "총액"},
        )
    finally:
        upload_service.classify_row_roles = original

    assert result["can_apply"] is True
    assert result["status_counts"] == {"처리": 4, "제외": 3, "오류": 0, "검토": 0}
    ai_excluded = [row for row in result["rows"] if row["row_number"] in {5, 7}]
    assert all(row["status"] == "제외" for row in ai_excluded)
    assert all("합계 검증" in row["issues"][0] for row in ai_excluded)
    print("통과: 표시 없는 중간 합계는 AI 분류와 서버 수량·금액 합계가 모두 일치할 때만 제외")


if __name__ == "__main__":
    main()
