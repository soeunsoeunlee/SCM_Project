"""PSI 버전에 기준월과 등록자를 보존합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0012_psi_business_metadata"
down_revision = "0011_upload_data_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("psi_versions", sa.Column("base_month", sa.String(length=7), nullable=True))
    op.add_column("psi_versions", sa.Column("created_by_user_id", sa.String(length=40), nullable=True))
    op.execute("UPDATE psi_versions SET base_month = to_char(reference_date, 'YYYY-MM') WHERE base_month IS NULL")
    op.alter_column("psi_versions", "base_month", nullable=False)
    op.create_foreign_key("fk_psi_versions_created_by_user_id_users", "psi_versions", "users", ["created_by_user_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_psi_versions_created_by_user_id_users", "psi_versions", type_="foreignkey")
    op.drop_column("psi_versions", "created_by_user_id")
    op.drop_column("psi_versions", "base_month")
