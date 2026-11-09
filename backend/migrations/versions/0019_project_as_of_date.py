"""프로젝트별 생산·선적 상태 기준 날짜를 추가합니다.

Revision ID: 0019
Revises: 0018
"""
from alembic import op
import sqlalchemy as sa


revision = "0019_project_as_of_date"
down_revision = "0018_production_matching"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("as_of_date", sa.Date(), nullable=False, server_default=sa.text("CURRENT_DATE")))


def downgrade() -> None:
    op.drop_column("projects", "as_of_date")
