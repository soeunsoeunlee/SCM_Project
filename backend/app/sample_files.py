"""업로드 연습용 XLSX·XLS·CSV 합성 샘플을 메모리에서 만듭니다.

이 파일의 함수는 DB를 읽거나 변경하지 않으므로 샘플 다운로드만으로 업무 데이터가 바뀌지 않습니다.
"""
from io import BytesIO, StringIO
import csv

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
import xlwt


# 화면의 샘플 카드에 표시할 이름·형식·정상/오류 구분입니다.
SAMPLES = [
    {"id": "northstar-normal", "file_name": "gulf_sales_normal.xlsx", "label": "걸프 정상 샘플", "format": "XLSX", "kind": "정상"},
    {"id": "bluewave-normal", "file_name": "saudi_sales_normal.xls", "label": "사우디 정상 샘플", "format": "XLS", "kind": "정상"},
    {"id": "sunrise-normal", "file_name": "turkiye_sales_normal.csv", "label": "튀르키예 정상 샘플", "format": "CSV", "kind": "정상"},
    {"id": "validation-errors", "file_name": "sales_validation_errors.xlsx", "label": "검증 오류 샘플", "format": "XLSX", "kind": "오류·검토"},
]


def _northstar() -> bytes:
    """헤더 3행, 여러 시트, PO 앞자리 0, 15+5 분할을 가진 정상 XLSX입니다."""
    book = Workbook()
    info = book.active
    info.title = "안내"
    info.append(["합성 데이터 샘플", "실제 고객 자료가 아닙니다."])
    sheet = book.create_sheet("Sales Plan")
    sheet.append(["걸프 월간 판매계획"])
    sheet.append(["기준: 합성 예시 / 헤더는 3행"])
    headers = ["PO No", "PO Line", "Model Code", "Customer", "Qty", "Net Unit", "Gross Total", "Currency", "Factory", "Region"]
    sheet.append(headers)
    rows = [
        ["00070031", "10", "OLED77-C4.AUS", "Demo Harbor", 15, 1280, 21120, "USD", "폴란드", "Gulf"],
        ["00070031", "10", "OLED77-C4.AUS", "Demo Harbor", 5, 1280, 7040, "USD", "폴란드", "Gulf"],
        ["00070032", "20", "QNED65-X9.US", "Demo Pacific", 8, 840, 7392, "USD", "구미", "Gulf"],
    ]
    for row in rows:
        sheet.append(row)
    for cell in sheet[3]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5961D8")
    sheet.column_dimensions["A"].width = 15
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def _bluewave() -> bytes:
    """서로 다른 컬럼명과 헤더 2행을 가진 정상 구형 XLS 파일입니다."""
    book = xlwt.Workbook(encoding="utf-8")
    info = book.add_sheet("Read me")
    info.write(0, 0, "합성 데이터 샘플")
    sheet = book.add_sheet("Auftrag")
    sheet.write(0, 0, "Saudi Verkaufsplan (synthetic)")
    headers = ["Bestellnummer", "Pos", "Modell", "Kunde", "Menge", "Netto Gesamt", "Brutto Stück", "Währung", "Werk", "Gebiet"]
    for column, header in enumerate(headers):
        sheet.write(1, column, header)
    rows = [
        ["00008110", "1", "FRIDGE-G8.DE", "Demo Morgen", 6, 6120, 1122, "USD", "이집트", "Saudi"],
        ["00008111", "1", "TV55-U7.DE", "Demo Nordlicht", 4, 2560, 704, "USD", "구미", "Saudi"],
    ]
    for row_index, row in enumerate(rows, start=2):
        for column, value in enumerate(row):
            sheet.write(row_index, column, value)
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def _sunrise() -> bytes:
    """USD 통화와 또 다른 컬럼명을 가진 UTF-8 CSV 파일입니다."""
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["PO번호", "라인", "모델명", "거래처", "판매수량", "Net Sales", "Gross Sales", "통화코드", "생산공장", "판매지역"])
    writer.writerow(["00099001", "01", "WASH-A9.TR", "Demo Lotus", "7", "410", "3157", "USD", "인도네시아", "Türkiye"])
    writer.writerow(["00099002", "01", "FRIDGE-M6.TR", "Demo Green Leaf", "5", "500", "2750", "USD", "구미", "Türkiye"])
    return output.getvalue().encode("utf-8-sig")


def _errors() -> bytes:
    """누락값·반품·중복·수식·병합셀·합계행 검증용 오류 XLSX입니다."""
    book = Workbook()
    info = book.active
    info.title = "안내"
    info.append(["오류·검토 샘플", "이 파일은 반영 차단 검증용 합성 자료입니다."])
    sheet = book.create_sheet("Error Cases")
    sheet.append(["오류 유형을 의도적으로 포함한 합성 자료"])
    sheet.append(["헤더는 3행입니다"])
    headers = ["PO No", "Line", "Model", "Customer", "Qty", "Net Unit", "Gross Total", "Currency", "Plant", "Region"]
    sheet.append(headers)
    sheet.append([None, "1", "MODEL-MISSING-PO", "Demo Missing", 3, 100, 330, "USD", "구미", "Gulf"])
    sheet.append(["00080002", "1", "MODEL-RETURN.R", "Demo Return", -2, 90, -198, "USD", "구미", "Gulf"])
    sheet.append(["00080003", "1", "MODEL-DUP.A", "Demo Duplicate", 4, 70, 308, "USD", "인도네시아", "Türkiye"])
    sheet.append(["00080003", "1", "MODEL-DUP.A", "Demo Duplicate", 4, 70, 308, "USD", "인도네시아", "Türkiye"])
    sheet.append(["00080004", "1", "MODEL-FORMULA.X", "Demo Formula", "=SUM(2,3)", 50, 275, "USD", "구미", "Gulf"])
    sheet.append(["00080005", "1", "MODEL-MERGED.A", "Demo Merged", 2, 40, 88, "USD", "구미", "Gulf"])
    sheet.append(["00080006", "1", "MODEL-MERGED.B", None, 2, 40, 88, "USD", "구미", "Gulf"])
    sheet.merge_cells("D9:D10")
    sheet.append(["합계", None, None, None, 13, None, 1199, "USD", None, None])
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def get_sample(sample_id: str) -> tuple[dict, bytes]:
    """샘플 ID에 맞는 설명과 실제 파일 바이트를 생성해 반환합니다."""
    sample = next((item for item in SAMPLES if item["id"] == sample_id), None)
    if sample is None:
        raise KeyError(sample_id)
    builders = {
        "northstar-normal": _northstar,
        "bluewave-normal": _bluewave,
        "sunrise-normal": _sunrise,
        "validation-errors": _errors,
    }
    return sample, builders[sample_id]()
