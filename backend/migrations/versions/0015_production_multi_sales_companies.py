"""Allow production uploads to cover multiple sales companies."""

from alembic import op
import sqlalchemy as sa


revision = "0015_production_multi_sales"
down_revision = "0014_manual_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 판매계획·선적정보는 계속 판매법인을 저장하지만, 생산정보는 파일 전체가 여러
    # 판매법인을 포함할 수 있으므로 NULL을 '여러 판매법인'이라는 업무 의미로 사용합니다.
    op.alter_column("upload_batches", "sales_company_id", existing_type=sa.String(length=40), nullable=True)


def downgrade() -> None:
    # 되돌릴 때 기존 복수법인 생산정보에는 임의 법인을 채울 수 없으므로 명확히 실패시킵니다.
    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT COUNT(*) FROM upload_batches WHERE sales_company_id IS NULL")).scalar_one()
    if count:
        raise RuntimeError("판매법인이 비어 있는 생산정보를 먼저 정리해야 downgrade할 수 있습니다.")
    op.alter_column("upload_batches", "sales_company_id", existing_type=sa.String(length=40), nullable=False)
