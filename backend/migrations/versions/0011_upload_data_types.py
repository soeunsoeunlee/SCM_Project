"""구조화 파일 업로드를 판매계획·생산정보·선적정보로 구분합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0011_upload_data_types"
down_revision = "0010_shipment_eta_vessel"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_batches", sa.Column("data_type", sa.String(length=30), server_default="판매계획", nullable=False))
    op.drop_constraint("uq_project_upload_hash", "upload_batches", type_="unique")
    op.create_unique_constraint("uq_project_type_upload_hash", "upload_batches", ["project_id", "data_type", "file_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_project_type_upload_hash", "upload_batches", type_="unique")
    op.create_unique_constraint("uq_project_upload_hash", "upload_batches", ["project_id", "file_hash"])
    op.drop_column("upload_batches", "data_type")
