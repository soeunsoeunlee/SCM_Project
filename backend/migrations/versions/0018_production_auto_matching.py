"""Remove manual plant defaults and allow production-info matching."""

from alembic import op
import sqlalchemy as sa


revision = "0018_production_matching"
down_revision = "0017_upload_common_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("upload_batches", "common_values_json")
    op.alter_column("orders", "production_company_id", existing_type=sa.String(length=40), nullable=True)


def downgrade() -> None:
    op.alter_column("orders", "production_company_id", existing_type=sa.String(length=40), nullable=False)
    op.add_column("upload_batches", sa.Column("common_values_json", sa.JSON(), nullable=True))
