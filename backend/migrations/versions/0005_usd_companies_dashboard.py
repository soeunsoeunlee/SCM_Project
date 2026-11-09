"""법인 기준정보를 확장하고 기존 합성 금액을 USD 기준으로 정리합니다.

Revision ID: 0005_usd_companies_dashboard
Revises: 0004_users_projects
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_usd_companies_dashboard"
down_revision = "0004_users_projects"
branch_labels = None
depends_on = None


COMPANIES = [
    ("sales-a", "걸프", "판매법인", "중동"),
    ("sales-b", "사우디", "판매법인", "사우디아라비아"),
    ("sales-c", "튀르키예", "판매법인", "튀르키예"),
    ("sales-d", "레반트", "판매법인", "레반트"),
    ("sales-e", "이집트", "판매법인", "이집트"),
    ("sales-f", "이스라엘", "판매법인", "이스라엘"),
    ("sales-g", "남아프리카공화국", "판매법인", "남아프리카공화국"),
    ("sales-h", "모로코", "판매법인", "모로코"),
    ("sales-i", "인도", "판매법인", "인도"),
    ("plant-a", "구미", "생산법인", "대한민국"),
    ("plant-b", "이집트", "생산법인", "이집트"),
    ("plant-c", "인도네시아", "생산법인", "인도네시아"),
    ("plant-d", "베트남", "생산법인", "베트남"),
    ("plant-e", "중국", "생산법인", "중국"),
    ("plant-f", "폴란드", "생산법인", "폴란드"),
]


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)
    unique_constraints = inspector.get_unique_constraints("companies")

    # 이전에는 이름 하나만 유일해야 했지만 이제 판매/생산에 '이집트'가 각각 존재할 수 있습니다.
    for constraint in unique_constraints:
        if constraint.get("column_names") == ["name"] and constraint.get("name"):
            op.drop_constraint(constraint["name"], "companies", type_="unique")

    statement = sa.text("""
        INSERT INTO companies (id, name, kind, country)
        VALUES (:id, :name, :kind, :country)
        ON CONFLICT (id) DO UPDATE
        SET name = EXCLUDED.name, kind = EXCLUDED.kind, country = EXCLUDED.country
    """)
    for company_id, name, kind, country in COMPANIES:
        connection.execute(statement, {"id": company_id, "name": name, "kind": kind, "country": country})

    op.execute("UPDATE source_materials SET name = 'gulf-plan-demo.xlsx', description = '걸프 판매계획 합성 예시' WHERE id = 'source-demo-a'")
    op.execute("UPDATE source_materials SET name = 'saudi-plan-demo.xlsx', description = '사우디 판매계획 합성 예시' WHERE id = 'source-demo-b'")
    op.execute("UPDATE source_materials SET name = 'turkiye-plan-demo.xlsx', description = '튀르키예 판매계획 합성 예시' WHERE id = 'source-demo-c'")

    inspector = sa.inspect(connection)
    if not any(item.get("name") == "uq_company_kind_name" for item in inspector.get_unique_constraints("companies")):
        op.create_unique_constraint("uq_company_kind_name", "companies", ["kind", "name"])

    # 이 프로젝트의 기존 자료는 모두 합성 데모입니다. EUR은 1.08, VND는 1/24,000의
    # 고정 데모 기준으로 한 번만 USD화합니다. 실제 환율 엔진으로 사용하지 않습니다.
    op.execute("""
        UPDATE orders
        SET unit_price = unit_price * 1.08,
            net_sales_amount = CASE WHEN net_sales_amount IS NULL THEN NULL ELSE net_sales_amount * 1.08 END,
            gross_sales_amount = CASE WHEN gross_sales_amount IS NULL THEN NULL ELSE gross_sales_amount * 1.08 END,
            currency = 'USD'
        WHERE currency = 'EUR'
    """)
    op.execute("""
        UPDATE orders
        SET unit_price = unit_price / 24000,
            net_sales_amount = CASE WHEN net_sales_amount IS NULL THEN NULL ELSE net_sales_amount / 24000 END,
            gross_sales_amount = CASE WHEN gross_sales_amount IS NULL THEN NULL ELSE gross_sales_amount / 24000 END,
            currency = 'USD'
        WHERE currency = 'VND'
    """)
    op.execute("UPDATE orders SET net_sales_amount = quantity * unit_price WHERE net_sales_amount IS NULL")
    op.execute("UPDATE orders SET gross_sales_amount = net_sales_amount * 1.10 WHERE gross_sales_amount IS NULL")
    op.execute("UPDATE orders SET net_sales_type = '총액' WHERE net_sales_type IS NULL")
    op.execute("UPDATE orders SET gross_sales_type = '총액' WHERE gross_sales_type IS NULL")


def downgrade() -> None:
    # 통화 환산은 원본 값을 역으로 추정하면 오차가 생기므로 자동 복구하지 않습니다.
    pass
