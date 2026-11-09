"""PSI RISK 수량을 여러 주문에 안전하게 배분하는 표를 추가합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0009_risk_allocations"
down_revision = "0008_psi_risk_reviews"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "psi_risk_allocations",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("review_id", sa.String(length=40), nullable=False),
        sa.Column("order_id", sa.String(length=40), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.String(length=40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("quantity > 0", name="ck_psi_risk_allocation_positive"),
        sa.ForeignKeyConstraint(["review_id"], ["psi_risk_reviews.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"]),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("review_id", "order_id", name="uq_psi_risk_allocation_order"),
    )
    op.create_index(op.f("ix_psi_risk_allocations_review_id"), "psi_risk_allocations", ["review_id"], unique=False)
    op.create_index(op.f("ix_psi_risk_allocations_order_id"), "psi_risk_allocations", ["order_id"], unique=False)
    # 0008에서 한 주문에 승인된 기존 기록도 새 배분 구조에서 그대로 보이게 옮깁니다.
    op.execute("""
        INSERT INTO psi_risk_allocations (id, review_id, order_id, quantity, created_by_user_id, created_at)
        SELECT md5(id || order_id), id, order_id, quantity, reviewed_by_user_id, COALESCE(reviewed_at, created_at)
        FROM psi_risk_reviews
        WHERE status = '승인' AND order_id IS NOT NULL AND reviewed_by_user_id IS NOT NULL
    """)


def downgrade() -> None:
    op.drop_index(op.f("ix_psi_risk_allocations_order_id"), table_name="psi_risk_allocations")
    op.drop_index(op.f("ix_psi_risk_allocations_review_id"), table_name="psi_risk_allocations")
    op.drop_table("psi_risk_allocations")
