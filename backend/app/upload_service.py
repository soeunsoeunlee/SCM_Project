"""판매자료 엑셀을 읽고 표준 컬럼으로 바꾸며 행별 오류를 검사합니다.

AI 추측 없이 사용자가 선택한 매핑과 명시적 별칭 규칙만 사용합니다. 오류·검토 행도
삭제하지 않고 모두 결과에 남기는 것이 이 파일의 가장 중요한 원칙입니다.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import BytesIO, StringIO
import csv
import json
import re
from typing import Any

from openpyxl import load_workbook
import xlrd

from .ai_mapping import classify_row_roles


# 지나치게 큰 파일로 서버가 멈추지 않도록 처리·미리보기 범위를 제한합니다.
MAX_ROWS = 10_000
MAX_COLUMNS = 100
PREVIEW_ROWS = 15
PREVIEW_COLUMNS = 15

# Sales Master로 바꿀 표준 항목과 필수 여부입니다.
STANDARD_FIELDS = [
    {"key": "po_number", "label": "PO번호", "required": True},
    {"key": "po_line_number", "label": "PO라인번호", "required": False},
    {"key": "model", "label": "모델명", "required": True},
    {"key": "customer", "label": "고객명", "required": True},
    {"key": "quantity", "label": "수량", "required": True},
    {"key": "net_sales", "label": "Net Sales", "required": False},
    {"key": "gross_sales", "label": "Gross Sales", "required": False},
    {"key": "sales_region", "label": "판매지역", "required": True},
]

# 법인마다 다른 원본 컬럼명을 제안하기 위한 명시적 별칭입니다.
ALIASES = {
    "po_number": ["po", "pono", "ponumber", "po번호", "bestellnummer", "purchaseorder", "주문번호"],
    "po_line_number": ["poline", "polinenumber", "line", "라인", "pos", "position"],
    "model": ["model", "modelcode", "모델", "모델명", "modell", "제품모델"],
    "customer": ["customer", "customername", "고객", "고객명", "거래처", "kunde"],
    "quantity": ["qty", "quantity", "수량", "판매수량", "menge", "orderqty"],
    "net_sales": ["netsales", "netunit", "nettotal", "netto", "nettogesamt", "순매출"],
    "gross_sales": ["grosssales", "grossunit", "grosstotal", "brutto", "bruttostück", "총매출"],
    "sales_region": ["region", "salesregion", "판매지역", "지역", "gebiet"],
}

# 원본 생산사이트 문자열을 기준 생산법인 ID로 연결하는 규칙입니다.
PLANT_ALIASES = {
    "plant-a": {"plant-a", "구미", "구미생산법인", "gumi"},
    "plant-b": {"plant-b", "이집트", "이집트생산법인", "egypt"},
    "plant-c": {"plant-c", "인도네시아", "인도네시아생산법인", "indonesia"},
    "plant-d": {"plant-d", "베트남", "베트남생산법인", "vietnam"},
    "plant-e": {"plant-e", "중국", "중국생산법인", "china"},
    "plant-f": {"plant-f", "폴란드", "폴란드생산법인", "poland"},
}


def normalize_header(value: Any) -> str:
    """대소문자·공백·기호 차이를 없애 컬럼명과 별칭을 비교하기 쉽게 합니다."""
    return re.sub(r"[^0-9a-z가-힣äöüß]+", "", str(value or "").strip().lower())


def header_signature(headers: list[str]) -> str:
    """헤더 목록 전체의 해시를 만들어 컬럼 구성이 바뀌었는지 확인합니다."""
    return sha256(json.dumps(headers, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def suggested_mapping(headers: list[str]) -> dict[str, int]:
    """별칭과 정확히 하나만 일치할 때만 해당 표준 필드의 컬럼을 제안합니다."""
    normalized = [normalize_header(header) for header in headers]
    result: dict[str, int] = {}
    for field, aliases in ALIASES.items():
        matches = [index for index, header in enumerate(normalized) if header in {normalize_header(alias) for alias in aliases}]
        if len(matches) == 1:
            result[field] = matches[0]
    return result


def sanitize_mapping(mapping: dict[str, int] | None) -> dict[str, int]:
    """현재 지원하는 표준 항목만 남깁니다.

    예전 저장 설정에 있던 ``currency`` 같은 항목은 조용히 제거합니다. 통화는 이제
    원본 컬럼과 연결하지 않고 모든 판매자료에 USD 공통값으로 적용합니다.
    """
    allowed = {item["key"] for item in STANDARD_FIELDS}
    return {key: value for key, value in (mapping or {}).items() if key in allowed}


def _json_value(value: Any) -> Any:
    """엑셀 값을 JSON에 안전하게 넣을 수 있는 날짜·문자·숫자로 바꿉니다."""
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _formatted_excel_value(formula_cell: Any, value_cell: Any) -> Any:
    """XLSX 셀의 계산값을 읽고 000000 같은 서식의 앞자리 0을 복원합니다."""
    value = value_cell.value if formula_cell.data_type == "f" else formula_cell.value
    if value is None:
        return None
    number_format = str(formula_cell.number_format or "")
    if isinstance(value, (int, float)) and re.fullmatch(r"0+", number_format):
        return f"{int(value):0{len(number_format)}d}"
    return _json_value(value)


def _xlsx_sheet(content: bytes, sheet_name: str) -> dict:
    """XLSX 시트의 값, 계산값 없는 수식, 병합셀 위치를 함께 읽습니다."""
    # 수식 자체를 보는 통합문서와 저장된 계산 결과를 보는 통합문서를 둘 다 엽니다.
    formula_book = load_workbook(BytesIO(content), data_only=False, read_only=False)
    value_book = load_workbook(BytesIO(content), data_only=True, read_only=False)
    if sheet_name not in formula_book.sheetnames:
        raise ValueError("선택한 시트를 찾을 수 없습니다.")
    formula_sheet = formula_book[sheet_name]
    value_sheet = value_book[sheet_name]
    row_count = min(formula_sheet.max_row, MAX_ROWS)
    column_count = min(formula_sheet.max_column, MAX_COLUMNS)
    rows: list[list[Any]] = []
    formula_missing: set[tuple[int, int]] = set()
    for row_number in range(1, row_count + 1):
        row: list[Any] = []
        for column in range(1, column_count + 1):
            formula_cell = formula_sheet.cell(row_number, column)
            value_cell = value_sheet.cell(row_number, column)
            if formula_cell.data_type == "f" and value_cell.value is None:
                formula_missing.add((row_number, column - 1))
            row.append(_formatted_excel_value(formula_cell, value_cell))
        rows.append(row)
    merged_followers: set[tuple[int, int]] = set()
    for merged in formula_sheet.merged_cells.ranges:
        for row_number in range(merged.min_row, merged.max_row + 1):
            for column in range(merged.min_col, merged.max_col + 1):
                if row_number != merged.min_row or column != merged.min_col:
                    merged_followers.add((row_number, column - 1))
    return {"rows": rows, "formula_missing": formula_missing, "merged_followers": merged_followers}


def _xls_value(book: xlrd.book.Book, sheet: xlrd.sheet.Sheet, row: int, column: int) -> Any:
    """구형 XLS 셀을 날짜·숫자·문자 형식에 맞게 Python 값으로 바꿉니다."""
    cell = sheet.cell(row, column)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if cell.ctype == xlrd.XL_CELL_DATE:
        return datetime(*xlrd.xldate_as_tuple(cell.value, book.datemode)).isoformat()
    if cell.ctype == xlrd.XL_CELL_NUMBER:
        try:
            format_string = book.format_map[book.xf_list[cell.xf_index].format_key].format_str
        except (KeyError, IndexError, AttributeError):
            format_string = ""
        if re.fullmatch(r"0+", format_string):
            return f"{int(cell.value):0{len(format_string)}d}"
        return int(cell.value) if float(cell.value).is_integer() else cell.value
    return _json_value(cell.value)


def _xls_sheet(content: bytes, sheet_name: str) -> dict:
    """구형 XLS 시트와 병합셀 위치를 읽습니다."""
    book = xlrd.open_workbook(file_contents=content, formatting_info=True)
    try:
        sheet = book.sheet_by_name(sheet_name)
    except xlrd.biffh.XLRDError as error:
        raise ValueError("선택한 시트를 찾을 수 없습니다.") from error
    rows = [
        [_xls_value(book, sheet, row, column) for column in range(min(sheet.ncols, MAX_COLUMNS))]
        for row in range(min(sheet.nrows, MAX_ROWS))
    ]
    merged_followers: set[tuple[int, int]] = set()
    for row_low, row_high, col_low, col_high in sheet.merged_cells:
        for row in range(row_low, row_high):
            for column in range(col_low, col_high):
                if row != row_low or column != col_low:
                    merged_followers.add((row + 1, column))
    return {"rows": rows, "formula_missing": set(), "merged_followers": merged_followers}


def _csv_sheet(content: bytes) -> dict:
    """CSV를 UTF-8 또는 CP949로 해석하여 한 개의 가상 시트로 읽습니다."""
    decoded = None
    for encoding in ("utf-8-sig", "cp949", "utf-8"):
        try:
            decoded = content.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("CSV 문자 인코딩을 읽을 수 없습니다. UTF-8 또는 CP949로 저장해 주세요.")
    rows = [[value if value != "" else None for value in row[:MAX_COLUMNS]] for row in list(csv.reader(StringIO(decoded)))[:MAX_ROWS]]
    return {"rows": rows, "formula_missing": set(), "merged_followers": set()}


def read_sheet(content: bytes, extension: str, sheet_name: str) -> dict:
    """확장자에 맞는 실제 파서를 선택하는 공통 진입점입니다."""
    extension = extension.lower()
    if extension == ".xlsx":
        return _xlsx_sheet(content, sheet_name)
    if extension == ".xls":
        return _xls_sheet(content, sheet_name)
    if extension == ".csv":
        if sheet_name != "CSV":
            raise ValueError("CSV에는 하나의 시트만 있습니다.")
        return _csv_sheet(content)
    raise ValueError("XLSX, XLS, CSV 파일만 지원합니다.")


def inspect_workbook(content: bytes, extension: str) -> list[dict]:
    """파일의 시트 목록과 앞부분 미리보기를 화면용으로 만듭니다."""
    extension = extension.lower()
    if extension == ".xlsx":
        book = load_workbook(BytesIO(content), data_only=False, read_only=False)
        sheet_names = book.sheetnames
    elif extension == ".xls":
        book = xlrd.open_workbook(file_contents=content, on_demand=True)
        sheet_names = book.sheet_names()
    elif extension == ".csv":
        sheet_names = ["CSV"]
    else:
        raise ValueError("XLSX, XLS, CSV 파일만 지원합니다.")
    result = []
    for name in sheet_names:
        parsed = read_sheet(content, extension, name)
        rows = parsed["rows"]
        result.append({
            "name": name,
            "row_count": len(rows),
            "column_count": max((len(row) for row in rows), default=0),
            "preview": [row[:PREVIEW_COLUMNS] for row in rows[:PREVIEW_ROWS]],
        })
    return result


def mapping_context(content: bytes, extension: str, sheet_name: str, header_row: int) -> dict:
    """사용자가 고른 헤더 행에서 컬럼명·서명·자동 제안을 만듭니다."""
    parsed = read_sheet(content, extension, sheet_name)
    rows = parsed["rows"]
    if header_row < 1 or header_row > len(rows):
        raise ValueError("헤더 행 번호가 파일 범위를 벗어났습니다.")
    raw_headers = rows[header_row - 1]
    last_nonempty = max((index for index, value in enumerate(raw_headers) if str(value or "").strip()), default=-1)
    if last_nonempty < 0:
        raise ValueError("선택한 헤더 행이 비어 있습니다.")
    headers = [str(value).strip() if value is not None else f"열 {index + 1}" for index, value in enumerate(raw_headers[: last_nonempty + 1])]
    return {
        "headers": headers,
        "header_signature": header_signature(headers),
        "suggested_mapping": suggested_mapping(headers),
        "standard_fields": STANDARD_FIELDS,
    }


def parse_decimal(value: Any) -> Decimal | None:
    """국가별 숫자 표기를 Decimal로 바꿉니다.

    예: 1,280 / 1.280,50 / 1,280.50 / (25.50). 괄호 금액은 음수로 처리합니다.
    """
    if value is None or str(value).strip() == "":
        return None
    if isinstance(value, bool):
        raise InvalidOperation
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip()
    negative_parentheses = text.startswith("(") and text.endswith(")")
    cleaned = re.sub(r"[^0-9,.-]", "", text.strip("()"))
    if not cleaned:
        raise InvalidOperation
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") and not cleaned.count("."):
        parts = cleaned.split(",")
        if len(parts[-1]) in (1, 2) and len(parts) == 2:
            cleaned = ".".join(parts)
        else:
            cleaned = "".join(parts)
    elif cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = ".".join(parts) if len(parts[-1]) in (1, 2) else "".join(parts)
    else:
        cleaned = cleaned.replace(",", "")
    number = Decimal(cleaned)
    return -number if negative_parentheses else number


def resolve_plant(value: str | None) -> str | None:
    """원본 생산사이트 별칭이 기준 생산법인 하나와 일치하면 그 ID를 반환합니다."""
    normalized = normalize_header(value)
    for company_id, aliases in PLANT_ALIASES.items():
        if normalized in {normalize_header(alias) for alias in aliases}:
            return company_id
    return None


def _text(value: Any) -> str | None:
    """셀 값을 앞뒤 공백 없는 문자열로 바꾸며 빈 값은 None으로 통일합니다."""
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _is_total_row(values: list[Any]) -> bool:
    """행 안에 합계/Total 표시가 있는지 확인합니다."""
    markers = {"합계", "총계", "total", "subtotal", "grandtotal"}
    for value in values:
        normalized = normalize_header(value)
        if normalized in markers:
            return True
    return False


def _row_fingerprint(row: dict) -> str:
    """주요 값 전체를 직렬화해 완전히 같은 행을 찾을 비교값을 만듭니다."""
    keys = ("po_number", "po_line_number", "model", "customer", "quantity", "net_sales_value", "gross_sales_value", "currency", "production_site", "sales_region")
    return json.dumps([str(row.get(key) or "") for key in keys], ensure_ascii=False, separators=(",", ":"))


def _same_decimal(left: Decimal | int | None, right: Decimal | int | None) -> bool:
    """엑셀 숫자의 표현 차이를 허용하면서 합계가 같은지 비교합니다."""
    if left is None or right is None:
        return False
    return abs(Decimal(str(left)) - Decimal(str(right))) <= Decimal("0.0001")


def _classify_unlabelled_totals(prepared: list[dict]) -> None:
    """표시 문구가 없는 중간 합계를 AI와 서버 산술 검증으로 안전하게 제외합니다."""
    candidates: list[dict] = []
    candidate_rows: dict[int, dict] = {}
    current_details: list[dict] = []
    identifier_keys = ("po_number", "model", "sales_region")

    for row in prepared:
        if row["status"] == "제외":
            current_details = []
            continue
        missing = [key for key in identifier_keys if not row.get(key)]
        is_candidate = (
            row["status"] == "오류"
            and len(missing) >= 2
            and row.get("quantity") is not None
            and bool(current_details)
        )
        if not is_candidate:
            if row["status"] in {"처리", "검토"}:
                current_details.append(row)
            continue

        quantity_match = _same_decimal(row.get("quantity"), sum((item.get("quantity") or 0) for item in current_details))
        net_values = [item.get("net_sales_total") for item in current_details]
        gross_values = [item.get("gross_sales_total") for item in current_details]
        net_match = row.get("net_sales_total") is not None and all(value is not None for value in net_values) and _same_decimal(row.get("net_sales_total"), sum(net_values, Decimal("0")))
        gross_match = row.get("gross_sales_total") is not None and all(value is not None for value in gross_values) and _same_decimal(row.get("gross_sales_total"), sum(gross_values, Decimal("0")))
        amount_checks = [value for value in (net_match, gross_match) if value]
        math_verified = quantity_match and bool(amount_checks)
        safe_labels = [str(value)[:120] for value in (row.get("customer"), row.get("sales_region")) if value]
        item = {
            "row_number": row["row_number"],
            "missing_identifiers": missing,
            "text_labels": safe_labels,
            "preceding_detail_count": len(current_details),
            "quantity_sum_matches": quantity_match,
            "net_sum_matches": net_match,
            "gross_sum_matches": gross_match,
            "server_math_verified": math_verified,
        }
        candidates.append(item)
        candidate_rows[row["row_number"]] = row
        # 합계 후보 자체가 다음 그룹의 상세로 합산되지 않도록 경계를 나눕니다.
        current_details = []

    if not candidates:
        return
    response = classify_row_roles(candidates[:50])
    decisions = {item["row_number"]: item for item in response.get("decisions", [])}
    candidate_context = {item["row_number"]: item for item in candidates}
    for row_number, row in candidate_rows.items():
        context = candidate_context[row_number]
        decision = decisions.get(row_number)
        if (
            context["server_math_verified"]
            and decision
            and decision.get("role") == "subtotal"
            and decision.get("confidence") in {"medium", "high"}
        ):
            row["status"] = "제외"
            row["issues"] = [f"AI 행 역할 판별과 수량·금액 합계 검증으로 중간 합계행 제외: {decision.get('reason') or '상세행 합계와 일치'}"]
        elif decision:
            row["issues"].append(f"AI 행 역할 판별: {decision.get('role')} ({decision.get('confidence')}) · {decision.get('reason')}")
        elif response.get("status") == "not_configured":
            row["issues"].append("합계 후보이지만 OpenAI API가 연결되지 않아 자동 제외하지 않음")


def standardize_rows(
    content: bytes,
    extension: str,
    sheet_name: str,
    header_row: int,
    mapping: dict[str, int],
    pricing: dict[str, str],
) -> dict:
    """선택한 시트의 모든 데이터 행을 표준화·검증·대사합니다.

    결과 행은 반드시 처리/제외/오류/검토 중 하나가 되며, 중복 의심 행을 임의 삭제하지
    않습니다. 마지막에는 USD 원본 수량·금액과 반영 예정 값을 비교해 반환합니다.
    """
    # 1단계: 필수 매핑과 단가/총액 선택이 완전한지 먼저 검사합니다.
    mapping = sanitize_mapping(mapping)
    context = mapping_context(content, extension, sheet_name, header_row)
    headers = context["headers"]
    required_mapping = {item["key"] for item in STANDARD_FIELDS if item["required"]}
    missing_mapping = sorted(required_mapping - set(mapping))
    if missing_mapping:
        labels = [item["label"] for item in STANDARD_FIELDS if item["key"] in missing_mapping]
        raise ValueError(f"필수 컬럼 매핑이 없습니다: {', '.join(labels)}")
    for field, index in mapping.items():
        if not isinstance(index, int) or index < 0 or index >= len(headers):
            raise ValueError(f"{field} 컬럼 위치가 올바르지 않습니다.")
    if "net_sales" in mapping and pricing.get("net_sales") not in {"단가", "총액"}:
        raise ValueError("Net Sales가 단가인지 총액인지 선택해 주세요.")
    if "gross_sales" in mapping and pricing.get("gross_sales") not in {"단가", "총액"}:
        raise ValueError("Gross Sales가 단가인지 총액인지 선택해 주세요.")

    # 2단계: 헤더 아래의 모든 행을 읽고 빈 행·합계행도 '제외' 상태로 기록합니다.
    parsed = read_sheet(content, extension, sheet_name)
    rows = parsed["rows"]
    prepared: list[dict] = []
    for offset, values in enumerate(rows[header_row:], start=header_row + 1):
        values = list(values[: len(headers)]) + [None] * max(0, len(headers) - len(values))
        if not any(_text(value) for value in values):
            prepared.append({"row_number": offset, "raw_values": values, "status": "제외", "issues": ["빈 행"], "formula_missing_columns": [], "merged_columns": []})
            continue
        if _is_total_row(values):
            prepared.append({"row_number": offset, "raw_values": values, "status": "제외", "issues": ["합계행으로 판단되어 주문 반영 대상에서 제외"], "formula_missing_columns": [], "merged_columns": []})
            continue

        # 3단계: 원본값을 표준 필드로 옮기고 필수값·수량·가격을 검증합니다.
        formula_columns = sorted(index for row_number, index in parsed["formula_missing"] if row_number == offset and index in mapping.values())
        merged_columns = sorted(index for row_number, index in parsed["merged_followers"] if row_number == offset and index in mapping.values())
        standard = {field: values[index] for field, index in mapping.items()}
        issues: list[str] = []
        status = "처리"
        row = {
            "row_number": offset,
            "raw_values": values,
            "po_number": _text(standard.get("po_number")),
            "po_line_number": _text(standard.get("po_line_number")),
            "model": _text(standard.get("model")),
            "customer": _text(standard.get("customer")),
            # 이 프로젝트의 판매금액 기준 통화는 USD 하나이므로 파일 통화 컬럼을 요구하지 않습니다.
            "currency": "USD",
            "production_site": None,
            "sales_region": _text(standard.get("sales_region")),
            "formula_missing_columns": formula_columns,
            "merged_columns": merged_columns,
        }
        required_values = {"PO번호": row["po_number"], "모델명": row["model"], "고객명": row["customer"], "판매지역": row["sales_region"]}
        for label, value in required_values.items():
            if value is None:
                issues.append(f"필수값 누락: {label}")
                status = "오류"
        try:
            quantity_decimal = parse_decimal(standard.get("quantity"))
            if quantity_decimal is None or quantity_decimal != quantity_decimal.to_integral_value() or quantity_decimal == 0:
                raise InvalidOperation
            row["quantity"] = int(quantity_decimal)
            if row["quantity"] < 0:
                issues.append("음수·반품 수량은 일반 주문으로 반영하지 않음")
                status = "검토" if status != "오류" else status
        except (InvalidOperation, ValueError):
            row["quantity"] = None
            issues.append("수량 형식 오류: 0이 아닌 정수만 허용")
            status = "오류"
        # 생산법인은 판매계획에서 입력하거나 추측하지 않습니다. 생산정보 업로드의
        # PO·모델·수량과 안전하게 일치할 때 operational_matching에서 연결합니다.
        row["production_company_id"] = None
        for field in ("net_sales", "gross_sales"):
            key = f"{field}_value"
            try:
                row[key] = parse_decimal(standard.get(field)) if field in mapping else None
            except (InvalidOperation, ValueError):
                row[key] = None
                issues.append(f"{'Net Sales' if field == 'net_sales' else 'Gross Sales'} 숫자 형식 오류")
                status = "오류"
            if row[key] is not None and row[key] < 0 and (row.get("quantity") or 0) >= 0:
                issues.append(f"{'Net Sales' if field == 'net_sales' else 'Gross Sales'} 음수 금액 검토 필요")
                status = "검토" if status != "오류" else status
            if row[key] is not None and row.get("quantity") is not None:
                row[f"{field}_total"] = row[key] * row["quantity"] if pricing.get(field) == "단가" else row[key]
            else:
                row[f"{field}_total"] = None
        if row["net_sales_value"] is None and row["gross_sales_value"] is None:
            issues.append("원본 금액 없음: 금액 대사 불가")
            status = "검토" if status != "오류" else status
        if formula_columns:
            issues.append("수식 셀의 저장된 계산값이 없어 값을 읽을 수 없음")
            status = "오류"
        if merged_columns:
            issues.append("병합셀 하위 행은 값을 자동 복사하지 않음: 적용 범위 검토 필요")
            status = "검토" if status != "오류" else status
        row.update({"status": status, "issues": issues})
        prepared.append(row)

    # 4단계: 명시적 합계 문구가 없는 행은 AI 분류와 서버의 수량·금액 합계가 모두
    # 일치할 때만 제외합니다. API가 없거나 모호하면 기존 오류를 그대로 유지합니다.
    _classify_unlabelled_totals(prepared)

    # 5단계: 완전히 같은 행도 실제 분할일 수 있으므로 삭제하지 않고 '검토'로만 표시합니다.
    fingerprints = Counter(_row_fingerprint(row) for row in prepared if row["status"] != "제외")
    for row in prepared:
        if row["status"] != "제외" and fingerprints[_row_fingerprint(row)] > 1:
            row["issues"].append("동일 행 중복 의심: 실제 분할 가능성이 있어 삭제하지 않고 검토 보류")
            if row["status"] == "처리":
                row["status"] = "검토"

    # 6단계: USD 원본 합계와 실제 반영 가능한 '처리' 행 합계를 따로 계산합니다.
    status_counts = Counter(row["status"] for row in prepared)
    reconciliation: dict[str, dict] = defaultdict(lambda: {
        "original_quantity": Decimal("0"), "planned_quantity": Decimal("0"),
        "original_net": Decimal("0"), "planned_net": Decimal("0"),
        "original_gross": Decimal("0"), "planned_gross": Decimal("0"),
        "net_available": False, "gross_available": False,
    })
    for row in prepared:
        if row["status"] == "제외" or not row.get("currency"):
            continue
        item = reconciliation[row["currency"]]
        if row.get("quantity") is not None:
            item["original_quantity"] += Decimal(row["quantity"])
            if row["status"] == "처리":
                item["planned_quantity"] += Decimal(row["quantity"])
        for field, total_key, availability in (("net", "net_sales_total", "net_available"), ("gross", "gross_sales_total", "gross_available")):
            if row.get(total_key) is not None:
                item[availability] = True
                item[f"original_{field}"] += row[total_key]
                if row["status"] == "처리":
                    item[f"planned_{field}"] += row[total_key]
    reconciliation_rows = []
    for currency, values in sorted(reconciliation.items()):
        reconciliation_rows.append({
            "currency": currency,
            **{key: float(value) if isinstance(value, Decimal) else value for key, value in values.items()},
            "net_basis": f"Net Sales {pricing.get('net_sales')} → 총액" if "net_sales" in mapping else "원본 컬럼 없음: 대사 불가",
            "gross_basis": f"Gross Sales {pricing.get('gross_sales')} → 총액" if "gross_sales" in mapping else "원본 컬럼 없음: 대사 불가",
        })
    # 오류 또는 검토가 하나라도 있으면 부분 반영을 허용하지 않습니다.
    return {
        "headers": headers,
        "header_signature": context["header_signature"],
        "rows": prepared,
        "status_counts": {status: status_counts.get(status, 0) for status in ("처리", "제외", "오류", "검토")},
        "reconciliation": reconciliation_rows,
        "can_apply": status_counts.get("처리", 0) > 0 and status_counts.get("오류", 0) == 0 and status_counts.get("검토", 0) == 0,
    }
