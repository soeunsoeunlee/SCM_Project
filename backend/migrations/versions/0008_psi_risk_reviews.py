"""PSI RISK 신호를 주문에 승인 또는 보류하는 검토 표를 추가합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0008_psi_risk_reviews"
down_revision = "0007_psi_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "psi_risk_reviews",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("version_id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=True),
        sa.Column("sales_company", sa.String(length=120), nullable=False),
        sa.Column("production_company", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("period_key", sa.String(length=12), nullable=False),
        sa.Column("risk_type", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("review_note", sa.String(length=300), nullable=True),
        sa.Column("reviewed_by_user_id", sa.String(length=40), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["psi_versions.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "sales_company", "production_company", "model", "risk_type", name="uq_psi_risk_review_signal"),
    )
    op.create_index(op.f("ix_psi_risk_reviews_version_id"), "psi_risk_reviews", ["version_id"], unique=False)
    op.create_index(op.f("ix_psi_risk_reviews_project_id"), "psi_risk_reviews", ["project_id"], unique=False)
    op.create_index(op.f("ix_psi_risk_reviews_order_id"), "psi_risk_reviews", ["order_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_psi_risk_reviews_order_id"), table_name="psi_risk_reviews")
    op.drop_index(op.f("ix_psi_risk_reviews_project_id"), table_name="psi_risk_reviews")
    op.drop_index(op.f("ix_psi_risk_reviews_version_id"), table_name="psi_risk_reviews")
    op.drop_table("psi_risk_reviews")
