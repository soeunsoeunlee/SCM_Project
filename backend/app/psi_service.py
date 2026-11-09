"""PSI 엑셀 파싱, 버전 비교, 검토용 RISK 신호 계산을 담당합니다.

이 모듈은 PSI를 주문으로 자동 변환하지 않습니다. 원본 값과 비교 결과를 별도로
보존하고 사용자가 확인할 근거만 만듭니다.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from io import BytesIO
import re

from openpyxl import load_workbook

from . import models


METRICS = ("DM", "SA", "SP", "Inventory", "Warehouse")
# 10~12월이 1월로 잘못 잘리지 않도록 두 자리 월을 먼저 비교합니다.
MONTH_PATTERN = re.compile(r"(20\d{2})\D+(1[0-2]|0?[1-9])(?:\D|$)")
WEEK_PATTERN = re.compile(r"W([1-5])", re.IGNORECASE)


def _text(value) -> str:
    """None과 숫자를 포함한 셀 값을 비교 가능한 짧은 문자열로 바꿉니다."""
    return "" if value is None else str(value).strip()


def parse_psi_workbook(content: bytes) -> dict:
    """정해진 PSI 이중 헤더를 읽고 모든 유효 셀과 오류를 반환합니다.

    1행은 연·월, 2행은 W1~W5, A:D는 판매법인·생산법인·모델·구분입니다.
    병합된 연·월 헤더만 명시적으로 이어받습니다. 일반 데이터 셀은 아래로 복사하지
    않습니다. 왼쪽 고정 열은 모델별 5행 묶음 안에서만 빈 값을 이어받습니다.
    """
    try:
        values_book = load_workbook(BytesIO(content), data_only=True, read_only=False)
        formula_book = load_workbook(BytesIO(content), data_only=False, read_only=False)
    except Exception as error:
        raise ValueError(f"XLSX 파일을 읽지 못했습니다: {error}") from error
    sheet_name = "PSI" if "PSI" in values_book.sheetnames else values_book.sheetnames[0]
    sheet = values_book[sheet_name]
    formula_sheet = formula_book[sheet_name]
    issues: list[str] = []
    if sheet.max_row < 3 or sheet.max_column < 5:
        return {"sheet_name": sheet_name, "periods": [], "cells": [], "issues": ["PSI 표는 2개 헤더 행과 E열 이후 주차 값이 필요합니다."]}
    expected = ["판매법인", "생산법인", "모델", "구분"]
    actual = [_text(sheet.cell(2, index).value or sheet.cell(1, index).value) for index in range(1, 5)]
    if actual != expected:
        issues.append("A:D 고정 열은 판매법인, 생산법인, 모델, 구분 순서여야 합니다.")

    periods: list[dict] = []
    last_month = ""
    seen_periods: set[str] = set()
    for column in range(5, sheet.max_column + 1):
        month_text = _text(sheet.cell(1, column).value)
        if month_text:
            last_month = month_text
        month_match = MONTH_PATTERN.search(last_month)
        week_match = WEEK_PATTERN.fullmatch(_text(sheet.cell(2, column).value))
        if not month_match or not week_match:
            issues.append(f"{column}열의 연·월 또는 주차 헤더를 확인해 주세요.")
            continue
        key = f"{int(month_match.group(1)):04d}-{int(month_match.group(2)):02d}-W{int(week_match.group(1))}"
        if key in seen_periods:
            issues.append(f"중복 주차 열이 있습니다: {key}")
            continue
        seen_periods.add(key)
        periods.append({"column": column, "key": key, "year": int(month_match.group(1)), "month": int(month_match.group(2)), "week": f"W{int(week_match.group(1))}"})

    cells: list[dict] = []
    current_group = {"sales_company": "", "production_company": "", "model": ""}
    metrics_in_group: set[str] = set()
    for row in range(3, sheet.max_row + 1):
        metric = _text(sheet.cell(row, 4).value)
        fixed = [_text(sheet.cell(row, column).value) for column in range(1, 4)]
        if not metric and not any(fixed) and not any(sheet.cell(row, item["column"]).value is not None for item in periods):
            continue
        if metric not in METRICS:
            issues.append(f"{row}행 구분은 DM, SA, SP, Inventory, Warehouse 중 하나여야 합니다.")
            continue
        # 새 모델 묶음은 하나 이상의 고정 열 값이 있을 때 시작합니다.
        if any(fixed):
            if not all(fixed):
                issues.append(f"{row}행의 판매법인·생산법인·모델을 모두 입력해 주세요.")
                continue
            current_group = {"sales_company": fixed[0], "production_company": fixed[1], "model": fixed[2]}
            metrics_in_group = set()
        if not all(current_group.values()):
            issues.append(f"{row}행은 연결할 모델 고정 열이 없습니다.")
            continue
        if metric in metrics_in_group:
            issues.append(f"{row}행의 {metric}은 같은 모델 묶음에서 중복되었습니다.")
        metrics_in_group.add(metric)
        for period in periods:
            value = sheet.cell(row, period["column"]).value
            formula_value = formula_sheet.cell(row, period["column"]).value
            if isinstance(formula_value, str) and formula_value.startswith("=") and value is None:
                issues.append(f"{row}행 {period['key']} 수식의 계산값이 없습니다. Excel에서 다시 계산 후 저장해 주세요.")
                continue
            if value in (None, ""):
                quantity = 0
            else:
                try:
                    numeric = float(value)
                except (TypeError, ValueError):
                    issues.append(f"{row}행 {period['key']} 값은 숫자여야 합니다.")
                    continue
                if numeric < 0 or not numeric.is_integer():
                    issues.append(f"{row}행 {period['key']} 값은 0 이상의 정수여야 합니다.")
                    continue
                quantity = int(numeric)
            cells.append({**current_group, "metric": metric, "period_key": period["key"], "quantity": quantity, "source_row": row, "source_column": period["column"]})
    return {"sheet_name": sheet_name, "periods": [{key: value for key, value in item.items() if key != "column"} for item in periods], "cells": cells, "issues": sorted(set(issues))}


def values_to_psi(values: list[models.PsiValue]) -> dict:
    """DB 셀 목록을 기존 PSI 대시보드가 읽는 행 구조로 바꿉니다."""
    period_keys = sorted({item.period_key for item in values})
    periods = [{"key": key, "year": int(key[:4]), "month": int(key[5:7]), "week": key[8:]} for key in period_keys]
    grouped: dict[tuple[str, str, str, str], dict[str, int]] = defaultdict(dict)
    for item in values:
        grouped[(item.sales_company, item.production_company, item.model, item.metric)][item.period_key] = item.quantity
    rows = []
    for sales, plant, model, metric in sorted(grouped, key=lambda item: (item[0], item[1], item[2], METRICS.index(item[3]))):
        rows.append({"sales_company": sales, "production_company": plant, "model": model, "metric": metric, "values": {key: grouped[(sales, plant, model, metric)].get(key, 0) for key in period_keys}})
    return {"periods": periods, "rows": rows, "is_demo": False, "inventory_basis": "업로드한 PSI 원본 값"}


def compare_values(current: list[models.PsiValue], previous: list[models.PsiValue]) -> dict:
    """두 버전의 같은 모델·지표·주차 셀을 안정된 키로 비교합니다."""
    def index(rows):
        return {(row.sales_company, row.production_company, row.model, row.metric, row.period_key): row.quantity for row in rows}
    now, before = index(current), index(previous)
    changes = []
    for key in sorted(set(now) | set(before)):
        old, new = before.get(key), now.get(key)
        if old == new:
            continue
        change_type = "추가" if old is None else "삭제" if new is None else "변경"
        changes.append({"sales_company": key[0], "production_company": key[1], "model": key[2], "metric": key[3], "period_key": key[4], "before": old, "after": new, "delta": (new or 0) - (old or 0), "change_type": change_type})
    return {
        "changed_cells": len(changes),
        "added_cells": sum(item["change_type"] == "추가" for item in changes),
        "removed_cells": sum(item["change_type"] == "삭제" for item in changes),
        "quantity_delta": sum(item["delta"] for item in changes),
        "changes": changes[:500],
    }


def risk_signals(values: list[models.PsiValue], reference_date: date) -> list[dict]:
    """기준 주차까지의 누적 부족분을 투명한 검토 신호로 계산합니다.

    신호는 주문 RISK 확정이나 자동 조치가 아닙니다. DM→SA와 SA→SP의 누적 차이만
    사용하며 서로 다른 단계의 수량을 더하지 않습니다.
    """
    reference_key = f"{reference_date.year:04d}-{reference_date.month:02d}-W{(reference_date.day - 1) // 7 + 1}"
    groups: dict[tuple[str, str, str], dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    for item in values:
        groups[(item.sales_company, item.production_company, item.model)][item.metric][item.period_key] = item.quantity
    signals = []
    for (sales, plant, model), metrics in sorted(groups.items()):
        dm = sa = sp = 0
        for period in sorted({key for metric in metrics.values() for key in metric}):
            dm += metrics["DM"].get(period, 0)
            sa += metrics["SA"].get(period, 0)
            sp += metrics["SP"].get(period, 0)
            if period > reference_key:
                continue
            if dm > sa:
                signals.append({"sales_company": sales, "production_company": plant, "model": model, "period_key": period, "risk_type": "DM 대비 SA 부족", "quantity": dm - sa, "reason": f"기준 주차까지 누적 DM {dm}ea, SA {sa}ea"})
            if sa > sp:
                signals.append({"sales_company": sales, "production_company": plant, "model": model, "period_key": period, "risk_type": "SA 대비 SP 지연", "quantity": sa - sp, "reason": f"기준 주차까지 누적 SA {sa}ea, SP {sp}ea"})
    return signals
