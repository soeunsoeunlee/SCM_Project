"""생산·선적 업로드와 비정형 원본의 최신 등록자를 보존합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0013_upload_registrants"
down_revision = "0012_psi_business_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_batches", sa.Column("created_by_user_id", sa.String(length=40), nullable=True))
    op.create_foreign_key("fk_upload_batches_created_by_user_id_users", "upload_batches", "users", ["created_by_user_id"], ["id"])
    op.add_column("unstructured_inputs", sa.Column("created_by_user_id", sa.String(length=40), nullable=True))
    op.create_foreign_key("fk_unstructured_inputs_created_by_user_id_users", "unstructured_inputs", "users", ["created_by_user_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_unstructured_inputs_created_by_user_id_users", "unstructured_inputs", type_="foreignkey")
    op.drop_column("unstructured_inputs", "created_by_user_id")
    op.drop_constraint("fk_upload_batches_created_by_user_id_users", "upload_batches", type_="foreignkey")
    op.drop_column("upload_batches", "created_by_user_id")
