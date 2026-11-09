"""화면에 보이는 데모 원본 파일명을 새 판매법인명에 맞춥니다."""

from alembic import op


revision = "0006_rename_demo_sources"
down_revision = "0005_usd_companies_dashboard"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE source_materials SET name = 'gulf-plan-demo.xlsx', description = '걸프 판매계획 합성 예시' WHERE id = 'source-demo-a'")
    op.execute("UPDATE source_materials SET name = 'saudi-plan-demo.xlsx', description = '사우디 판매계획 합성 예시' WHERE id = 'source-demo-b'")
    op.execute("UPDATE source_materials SET name = 'turkiye-plan-demo.xlsx', description = '튀르키예 판매계획 합성 예시' WHERE id = 'source-demo-c'")


def downgrade() -> None:
    pass
