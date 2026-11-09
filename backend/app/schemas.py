"""API가 받을 값과 반환할 JSON 모양을 Pydantic으로 정의합니다.

models.py가 DB 표의 모양이라면, 이 파일은 브라우저와 서버 사이의 데이터 계약입니다.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class CompanyCreate(BaseModel):
    """현재 프로젝트에서 직접 추가할 판매·생산법인입니다."""
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(pattern=r"^(판매법인|생산법인)$")
    country: str | None = Field(default=None, max_length=80)


class DemoOrderCreate(BaseModel):
    """화면에서 데모 주문을 만들 때 허용하는 입력값과 검증 규칙입니다."""
    sales_company_id: str = Field(min_length=1, max_length=40)
    production_company_id: str = Field(min_length=1, max_length=40)
    target_month: str = Field(pattern=r"^20\d{2}-(0[1-9]|1[0-2])$")
    po_number: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=100)
    customer: str = Field(min_length=1, max_length=120)
    quantity: int = Field(gt=0, le=1000000)
    currency: str = Field(pattern=r"^USD$")
    unit_price: Decimal = Field(ge=0, le=1000000000000, max_digits=18, decimal_places=4)

    @field_validator("po_number", "model", "customer")
    @classmethod
    def validate_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("공백만 입력할 수 없습니다.")
        return value


class ProductionDetailOut(BaseModel):
    """주문 응답 안에 포함되는 생산 상세 한 건입니다."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    quantity: int
    status: str
    production_date: date | None
    is_manual: bool = False


class ShipmentDetailOut(BaseModel):
    """주문 응답 안에 포함되는 선적 상세 한 건입니다."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    quantity: int
    status: str
    shipment_date: date | None
    carrier: str | None
    vessel_name: str | None
    is_manual: bool = False


class OrderOut(BaseModel):
    """Sales Master 화면에 전달하는 주문 한 건의 전체 모양입니다."""
    id: str
    order_number: str
    sales_company_id: str
    sales_company: str
    production_company_id: str | None
    production_company: str
    target_month: str
    po_number: str
    po_line_number: str | None = None
    model: str
    customer: str
    quantity: int
    currency: str
    unit_price: float
    total_amount: float
    net_sales_amount: float | None = None
    gross_sales_amount: float | None = None
    net_sales_type: str | None = None
    gross_sales_type: str | None = None
    production_site_source: str | None = None
    sales_region: str | None = None
    upload_batch_id: str | None = None
    erp_status: str
    match_status: str
    source_name: str
    source_download_url: str | None = None
    source_download_label: str | None = None
    production_quantity: int
    shipment_quantity: int
    closed_quantity: int
    production_planned_date: date | None = None
    production_next_date: date | None = None
    production_planned_quantity: int = 0
    production_remaining_quantity: int = 0
    production_carryover_quantity: int = 0
    production_schedule_status: str
    shipment_planned_date: date | None = None
    shipment_next_date: date | None = None
    shipment_planned_quantity: int = 0
    shipment_remaining_quantity: int = 0
    shipment_carryover_quantity: int = 0
    shipment_schedule_status: str
    review_quantity: int
    risk_status: str
    risk_reasons: list[str]
    risk_quantity: int
    production_details: list[ProductionDetailOut]
    shipment_details: list[ShipmentDetailOut]
    manual_fields: list[str] = Field(default_factory=list)
    pending_update_count: int = 0
    is_demo: bool = True


class DashboardOut(BaseModel):
    """대시보드 요약 카드와 최근 주문에 필요한 응답 모양입니다."""
    order_count: int
    planned_quantity: int
    planned_value_by_currency: dict[str, float]
    produced_quantity: int
    shipped_quantity: int
    erp_unconfirmed_orders: int
    review_orders: int
    companies: list[dict[str, str]]
    recent_orders: list[OrderOut]
    is_demo: bool = True
