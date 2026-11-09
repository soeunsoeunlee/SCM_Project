"""Sales Master 일정 요약, PSI 주차 표, 화면과 같은 Excel 보고서를 만듭니다."""

from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO
from math import ceil

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import models


def order_amount(order: models.Order) -> Decimal:
    """대시보드 주문금액은 Net, Net이 없으면 Gross, 둘 다 없으면 단가×수량입니다."""
    return order.net_sales_amount if order.net_sales_amount is not None else order.gross_sales_amount if order.gross_sales_amount is not None else order.unit_price * order.quantity


def effective_detail_status(item, stage: str, as_of_date: date) -> str:
    """자동 업로드 일정은 프로젝트 기준일을 지나면 실적으로 해석합니다.

    수동 확인값과 데모 확정값은 사용자가 저장한 상태를 그대로 사용하고, 검토 필요
    연결은 날짜가 지나도 자동 확정하지 않습니다.
    """
    if item.status == "검토 필요" or item.is_manual or item.is_demo:
        return item.status
    schedule_date = item.production_date if stage == "production" else item.shipment_date
    if schedule_date is None:
        return item.status
    return ("실제 완료" if stage == "production" else "실제 선적") if schedule_date < as_of_date else "예정"


def schedule_summary(order: models.Order, stage: str, today: date | None = None) -> dict:
    """생산 또는 선적의 예정·실제·잔량·다음 일정·이월 가능 수량을 계산합니다."""
    today = today or date.today()
    if stage == "production":
        details = order.production_details
        actual_status, date_field = "실제 완료", "production_date"
    else:
        details = order.shipment_details
        actual_status, date_field = "실제 선적", "shipment_date"

    statuses = {item.id: effective_detail_status(item, stage, today) for item in details}
    actual = sum(item.quantity for item in details if statuses[item.id] == actual_status)
    planned_items = [item for item in details if statuses[item.id] == "예정"]
    planned = sum(item.quantity for item in planned_items)
    dated_plans = sorted(getattr(item, date_field) for item in planned_items if getattr(item, date_field) is not None)
    future_plans = [value for value in dated_plans if value >= today]
    overdue_plans = [item for item in planned_items if getattr(item, date_field) is not None and getattr(item, date_field) < today]
    scheduled = min(order.quantity, actual + planned)
    remaining = max(order.quantity - actual, 0)
    carryover = max(order.quantity - scheduled, 0)
    if remaining == 0:
        status = "완료"
    elif overdue_plans:
        status = "지연"
    elif future_plans:
        status = "일정 있음"
    else:
        status = "일정 미정"
    return {
        "planned_date": (future_plans[0] if future_plans else dated_plans[0] if dated_plans else None),
        "next_date": future_plans[0] if future_plans else None,
        "actual_quantity": min(actual, order.quantity),
        "planned_quantity": planned,
        "remaining_quantity": remaining,
        "carryover_quantity": carryover,
        "status": status,
    }


def add_months(year: int, month: int, offset: int) -> tuple[int, int]:
    """연도 경계를 넘어가는 월 이동을 계산합니다."""
    index = year * 12 + month - 1 + offset
    return index // 12, index % 12 + 1


def psi_periods(today: date | None = None) -> list[dict]:
    """현재 월의 3개월 전부터 8개월 후까지 12개월의 W1~W5 열을 만듭니다."""
    today = today or date.today()
    periods: list[dict] = []
    for offset in range(-3, 9):
        year, month = add_months(today.year, today.month, offset)
        for week in range(1, ceil(monthrange(year, month)[1] / 7) + 1):
            periods.append({"key": f"{year:04d}-{month:02d}-W{week}", "year": year, "month": month, "week": f"W{week}"})
    return periods


def period_key(value: date) -> str:
    """날짜를 월 안의 1~7일=W1 방식의 주차 키로 바꿉니다."""
    return f"{value.year:04d}-{value.month:02d}-W{(value.day - 1) // 7 + 1}"


def build_psi(orders: list[models.Order], today: date | None = None) -> dict:
    """Sales Master 주문을 모델별 DM·SA·SP·Inventory·Warehouse 5개 행으로 변환합니다."""
    periods = psi_periods(today)
    keys = [period["key"] for period in periods]
    groups: dict[tuple[str, str, str], list[models.Order]] = defaultdict(list)
    for order in orders:
        groups[(order.sales_company.name, order.production_company.name if order.production_company else "미매칭", order.model)].append(order)

    rows: list[dict] = []
    for (sales_company, production_company, model), group_orders in sorted(groups.items()):
        metric_values = {metric: {key: 0 for key in keys} for metric in ("DM", "SA", "SP", "Inventory", "Warehouse")}
        for order in group_orders:
            dm_key = f"{order.target_month}-W1"
            if dm_key in metric_values["DM"]:
                metric_values["DM"][dm_key] += order.quantity
            for item in order.production_details:
                if item.production_date:
                    key = period_key(item.production_date)
                    if key in metric_values["SA"] and item.status in {"예정", "실제 완료"}:
                        metric_values["SA"][key] += item.quantity
                    if key in metric_values["Warehouse"] and item.status == "실제 완료":
                        metric_values["Warehouse"][key] += item.quantity
            for item in order.shipment_details:
                if item.shipment_date and item.status in {"예정", "실제 선적"}:
                    key = period_key(item.shipment_date)
                    if key in metric_values["SP"]:
                        metric_values["SP"][key] += item.quantity

        inventory = 0
        for key in keys:
            inventory = max(inventory + metric_values["SA"][key] - metric_values["SP"][key], 0)
            metric_values["Inventory"][key] = inventory
        for metric in ("DM", "SA", "SP", "Inventory", "Warehouse"):
            rows.append({"sales_company": sales_company, "production_company": production_company, "model": model, "metric": metric, "values": metric_values[metric]})
    return {"periods": periods, "rows": rows, "is_demo": True, "inventory_basis": "기초재고 0 + SA - SP의 합성 예시"}


def style_sheet(sheet, widths: dict[int, float] | None = None) -> None:
    """업무용 Excel의 공통 글꼴·헤더·열 너비를 적용합니다."""
    sheet.sheet_view.showGridLines = False
    sheet.freeze_panes = "A3"
    dark = PatternFill("solid", fgColor="263657")
    for row in sheet.iter_rows(min_row=1, max_row=2):
        for cell in row:
            cell.fill = dark
            cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center")
    for row in sheet.iter_rows(min_row=3):
        for cell in row:
            cell.font = Font(name="Arial", size=9)
            cell.alignment = Alignment(vertical="center")
    if widths:
        for column, width in widths.items():
            sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.auto_filter.ref = sheet.dimensions


def sales_master_workbook(orders: list[models.Order], as_of_date: date | None = None) -> bytes:
    """화면의 주문 목록과 일정 요약을 열 단위로 펼친 Excel을 만듭니다."""
    book = Workbook()
    sheet = book.active
    sheet.title = "Sales Master"
    headers = ["SO", "PO", "판매법인", "생산법인", "모델", "고객", "대상 월", "주문 수량(ea)", "Unit Price (USD)", "매칭 검토 필요", "RISK", "PST", "생산 완료(ea)", "생산 미완료(ea)", "생산 다음 일정", "생산 이월 가능(ea)", "ETD", "선사명", "선박명", "선적 완료(ea)", "선적 미완료(ea)", "다음 ETD", "선적 이월 가능(ea)", "Net Sales (USD)", "Gross Sales (USD)", "Status", "Closed", "자료 출처"]
    sheet.append(["Sales Master 주문·생산·선적 현황"] + [None] * (len(headers) - 1))
    sheet.append(headers)
    for order in orders:
        production = schedule_summary(order, "production", as_of_date)
        shipment = schedule_summary(order, "shipment", as_of_date)
        shipment_rows = sorted(order.shipment_details, key=lambda item: (item.shipment_date or date.max, item.id))
        etd_text = " / ".join(str(item.shipment_date) for item in shipment_rows if item.shipment_date) or None
        carriers = " / ".join(dict.fromkeys(item.carrier for item in shipment_rows if item.carrier)) or None
        vessels = " / ".join(dict.fromkeys(item.vessel_name for item in shipment_rows if item.vessel_name)) or None
        sheet.append([
            order.order_number, order.po_number, order.sales_company.name, order.production_company.name if order.production_company else "미매칭", order.model, order.customer,
            order.target_month, order.quantity, float(order.unit_price), "필요" if order.match_status == "검토 필요" else "불필요", order.match_status,
            production["planned_date"], production["actual_quantity"], production["remaining_quantity"], production["next_date"], production["carryover_quantity"],
            etd_text, carriers, vessels, shipment["actual_quantity"], shipment["remaining_quantity"], shipment["next_date"], shipment["carryover_quantity"],
            float(order.net_sales_amount or 0), float(order.gross_sales_amount or 0), order.match_status, order.erp_status, order.source.name,
        ])
    style_sheet(sheet, {1: 20, 2: 16, 3: 14, 4: 14, 5: 18, 6: 20, 7: 11, 9: 18, 11: 15, 12: 13, 15: 13, 17: 22, 18: 22, 19: 18, 20: 22, 23: 13, 25: 18, 26: 18, 29: 24})
    sheet.freeze_panes = "A3"
    for row in sheet.iter_rows(min_row=3):
        for index in (12, 15, 23):
            row[index - 1].number_format = "yyyy-mm-dd"
        for index in (9, 25, 26):
            row[index - 1].number_format = '"$"#,##0.00'
    output = BytesIO(); book.save(output); return output.getvalue()


def dashboard_workbook(orders: list[models.Order], companies: list[models.Company], as_of_date: date | None = None) -> bytes:
    """화면과 같은 판매법인별 금액표 Excel을 만듭니다."""
    book = Workbook(); sheet = book.active; sheet.title = "Dashboard"
    headers = ["판매법인", "주문금액 (USD)", "배송중 금액 (USD)", "매출완료금액 (USD)", "매출 대기 (USD)", "매출예상금액 (USD)"]
    sheet.append(["판매법인별 매출 현황"] + [None] * 5); sheet.append(headers)
    company_rows = []
    grand = [Decimal("0") for _ in range(5)]
    for company in [item for item in companies if item.kind == "판매법인"]:
        company_orders = [order for order in orders if order.sales_company_id == company.id]
        values = [Decimal("0") for _ in range(5)]
        for order in company_orders:
            amount = order_amount(order); closed = min(order.quantity, sum(item.confirmed_quantity for item in order.erp_confirmations))
            shipped = schedule_summary(order, "shipment", as_of_date)["actual_quantity"]
            completed = amount * Decimal(closed) / Decimal(order.quantity)
            shipping = amount * Decimal(max(shipped - closed, 0)) / Decimal(order.quantity)
            forecast = amount if order.shipment_details else Decimal("0")
            current = [amount, shipping, completed, amount - completed, forecast]
            values = [left + right for left, right in zip(values, current)]
        company_rows.append([company.name] + [float(value) for value in values])
        grand = [left + right for left, right in zip(grand, values)]
    # 수식을 쓰면 일부 미리보기 프로그램이 재계산 전에는 빈칸으로 보입니다.
    # 화면처럼 전체 합계를 맨 위에 두고 실제 숫자를 저장합니다.
    sheet.append(["전체 합계"] + [float(value) for value in grand])
    for row in company_rows:
        sheet.append(row)
    style_sheet(sheet, {1: 22, 2: 20, 3: 20, 4: 20, 5: 20, 6: 20})
    for row in sheet.iter_rows(min_row=3, min_col=2):
        for cell in row: cell.number_format = '"$"#,##0.00'
    output = BytesIO(); book.save(output); return output.getvalue()


def psi_workbook(psi: dict) -> bytes:
    """판매·생산·모델 고정 열과 월/주차 이중 헤더를 가진 PSI Excel을 만듭니다."""
    book = Workbook(); sheet = book.active; sheet.title = "PSI"
    periods = psi["periods"]
    sheet.append(["판매법인", "생산법인", "모델", "구분"] + [f"{item['year']}년 {item['month']}월" for item in periods])
    sheet.append(["판매법인", "생산법인", "모델", "구분"] + [item["week"] for item in periods])
    start = 5
    index = 0
    while index < len(periods):
        end = index
        while end + 1 < len(periods) and periods[end + 1]["year"] == periods[index]["year"] and periods[end + 1]["month"] == periods[index]["month"]: end += 1
        if end > index: sheet.merge_cells(start_row=1, start_column=start + index, end_row=1, end_column=start + end)
        index = end + 1
    for row in psi["rows"]:
        sheet.append([row["sales_company"], row["production_company"], row["model"], row["metric"]] + [row["values"][period["key"]] for period in periods])
    style_sheet(sheet, {1: 16, 2: 16, 3: 20, 4: 12})
    for column in range(5, 5 + len(periods)): sheet.column_dimensions[get_column_letter(column)].width = 7
    sheet.freeze_panes = "E3"
    colors = {"DM": "E9EDFF", "SA": "E8F6F1", "SP": "E8F2FF", "Inventory": "FFF4D9", "Warehouse": "F0F1F4"}
    for row in range(3, sheet.max_row + 1):
        fill = PatternFill("solid", fgColor=colors.get(sheet.cell(row, 4).value, "FFFFFF"))
        for column in range(1, sheet.max_column + 1): sheet.cell(row, column).fill = fill
    output = BytesIO(); book.save(output); return output.getvalue()
