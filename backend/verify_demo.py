"""실행 중인 로컬 합성 데모 API를 확인하는 스크립트.

첫 실행은 테스트 주문 하나를 저장하고 주문 ID를 출력합니다.
프로세스를 재시작한 뒤 --expect-order ID로 실행하면 영속성을 확인합니다.
"""

import argparse
import json
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

BASE_URL = "http://127.0.0.1:8000"


def get_json(path: str) -> object:
    """실행 중인 로컬 API에 GET 요청을 보내 JSON을 읽습니다."""
    with urlopen(f"{BASE_URL}{path}", timeout=5) as response:
        return json.load(response)


def post_json(path: str, payload: dict) -> tuple[object, int]:
    """JSON을 POST하고 응답 본문과 HTTP 상태코드를 반환합니다."""
    body = json.dumps(payload).encode("utf-8")
    request = Request(f"{BASE_URL}{path}", data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=5) as response:
        return json.load(response), response.status


def main() -> None:
    """수량 중복 방지와 서버 재시작 후 주문 보존을 순서대로 확인합니다."""
    parser = argparse.ArgumentParser(description="합성 데모 API와 주문 수량 집계를 확인합니다.")
    parser.add_argument("--expect-order", help="서버 재시작 전에 저장한 주문 ID")
    args = parser.parse_args()

    health = get_json("/health")
    assert health["status"] == "ok", health
    orders = get_json("/api/orders")
    dashboard = get_json("/api/dashboard")
    assert len(orders) >= 9, f"합성 샘플 주문이 부족합니다: {len(orders)}"
    assert dashboard["order_count"] == len(orders), "대시보드 주문 수와 상세 목록이 일치하지 않습니다."
    assert len([company for company in dashboard["companies"] if company["kind"] == "판매법인"]) == 3
    assert len([company for company in dashboard["companies"] if company["kind"] == "생산법인"]) == 3
    assert dashboard["planned_quantity"] == sum(order["quantity"] for order in orders), "주문 요약 수량에 상세 연결이 중복 집계되었습니다."

    split_order = next(order for order in orders if order["quantity"] == 20)
    assert sorted(row["quantity"] for row in split_order["production_details"] if row["status"] == "실제 완료") == [5, 15]
    assert sorted(row["quantity"] for row in split_order["shipment_details"] if row["status"] == "실제 선적") == [10, 10]
    assert split_order["production_quantity"] == 20 and split_order["shipment_quantity"] == 20
    assert split_order["po_number"] == "00047120"
    assert split_order["total_amount"] == 25600
    review = next(order for order in orders if order["id"] == "demo-order-009")
    assert review["review_quantity"] == 3
    assert review["production_quantity"] == review["shipment_quantity"] == 0
    assert any(order["erp_status"] == "미확인" for order in orders), "ERP 매출화 미확인 사례가 없습니다."
    assert any(row["status"] == "검토 필요" for order in orders for row in order["production_details"]), "검토 대기 생산 연결 사례가 없습니다."
    assert any(order["production_quantity"] < order["quantity"] and order["production_quantity"] > 0 for order in orders), "부분 생산 사례가 없습니다."
    assert any(order["shipment_quantity"] < order["quantity"] and order["shipment_quantity"] > 0 for order in orders), "부분 선적 사례가 없습니다."
    assert sum(order["total_amount"] for order in orders if order["currency"] == "USD") == dashboard["planned_value_by_currency"]["USD"]
    print(f"통과: 합성 주문 {len(orders)}건, 판매/생산법인 각 3곳, 20대 주문의 생산 15+5·선적 10+10, 통화별 합계.")

    if args.expect_order:
        saved = next((order for order in orders if order["id"] == args.expect_order), None)
        assert saved is not None, f"재시작 전 저장된 주문이 없습니다: {args.expect_order}"
        assert saved["quantity"] == 3, "재시작 뒤 저장한 데모 주문 수량이 달라졌습니다."
        print(f"통과: 이전 검증 주문 {saved['order_number']}을 재시작 뒤 다시 조회했습니다.")
        return

    order_payload = {
        "sales_company_id": "sales-a",
        "production_company_id": "plant-b",
        "target_month": "2026-12",
        "po_number": f"VERIFY-{uuid4().hex[:8].upper()}",
        "model": "VERIFY-DEMO-1",
        "customer": "가상 검증 고객",
        "quantity": 3,
        "currency": "USD",
        "unit_price": 12.5,
    }
    saved, response_status = post_json("/api/orders", order_payload)
    assert response_status == 201 and saved["quantity"] == 3
    reread = next(order for order in get_json("/api/orders") if order["id"] == saved["id"])
    updated_dashboard = get_json("/api/dashboard")
    assert reread["po_number"] == order_payload["po_number"]
    assert updated_dashboard["planned_quantity"] == dashboard["planned_quantity"] + 3
    assert updated_dashboard["planned_value_by_currency"]["USD"] == dashboard["planned_value_by_currency"]["USD"] + 37.5
    print(f"통과: 주문 저장 후 재조회, 수량·금액 요약 반영. 재시작 검증용 주문 ID: {saved['id']}")
    print("서버를 재시작한 뒤 다음 단계에서 출력된 ID로 다시 실행하면 저장 데이터 보존을 확인할 수 있습니다.")


if __name__ == "__main__":
    try:
        main()
    except (AssertionError, HTTPError, URLError, TimeoutError, KeyError, StopIteration, json.JSONDecodeError) as error:
        raise SystemExit(f"검증 실패: {error}") from error
