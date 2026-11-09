"""Sales Master 수동 우선값과 후속 파일 변경 제안을 보존합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0014_manual_overrides"
down_revision = "0013_upload_registrants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "order_manual_overrides",
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("protected_fields", sa.JSON(), nullable=False),
        sa.Column("updated_by_user_id", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["updated_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("order_id"),
    )
    op.create_index("ix_order_manual_overrides_project_id", "order_manual_overrides", ["project_id"])
    op.create_table(
        "order_update_proposals",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("batch_id", sa.String(length=40), nullable=True),
        sa.Column("upload_result_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.String(length=40), nullable=False),
        sa.Column("incoming_values", sa.JSON(), nullable=False),
        sa.Column("differences", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), server_default="검토 대기", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_by_user_id", sa.String(length=40), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["batch_id"], ["upload_batches.id"]),
        sa.ForeignKeyConstraint(["upload_result_id"], ["standardized_results.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["source_materials.id"]),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "upload_result_id", "order_id", name="uq_order_update_proposal_source"),
    )
    op.create_index("ix_order_update_proposals_project_id", "order_update_proposals", ["project_id"])
    op.create_index("ix_order_update_proposals_order_id", "order_update_proposals", ["order_id"])
    op.create_index("ix_order_update_proposals_batch_id", "order_update_proposals", ["batch_id"])
    op.add_column("production_allocations", sa.Column("is_manual", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.add_column("shipment_allocations", sa.Column("is_manual", sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    op.drop_column("shipment_allocations", "is_manual")
    op.drop_column("production_allocations", "is_manual")
    op.drop_index("ix_order_update_proposals_batch_id", table_name="order_update_proposals")
    op.drop_index("ix_order_update_proposals_order_id", table_name="order_update_proposals")
    op.drop_index("ix_order_update_proposals_project_id", table_name="order_update_proposals")
    op.drop_table("order_update_proposals")
    op.drop_index("ix_order_manual_overrides_project_id", table_name="order_manual_overrides")
    op.drop_table("order_manual_overrides")
