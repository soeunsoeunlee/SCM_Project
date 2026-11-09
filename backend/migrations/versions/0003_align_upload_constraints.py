"""표준화 결과의 중복 방지 제약조건 이름을 models.py와 일치시킵니다.

Revision ID: 0003_align_upload_constraints
Revises: 0002_sales_uploads
"""
from alembic import op
import sqlalchemy as sa


revision = "0003_align_upload_constraints"
down_revision = "0002_sales_uploads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """기존 제약 이름을 확인하고 새 표준 이름으로 교체합니다."""
    constraints = {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints("standardized_results")}
    if "standardized_results_raw_row_id_key" in constraints:
        op.drop_constraint("standardized_results_raw_row_id_key", "standardized_results", type_="unique")
    if "uq_standardized_raw_row" not in constraints:
        op.create_unique_constraint("uq_standardized_raw_row", "standardized_results", ["raw_row_id"])


def downgrade() -> None:
    """제약조건 이름을 이전 이름으로 되돌립니다."""
    constraints = {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints("standardized_results")}
    if "uq_standardized_raw_row" in constraints:
        op.drop_constraint("uq_standardized_raw_row", "standardized_results", type_="unique")
    if "standardized_results_raw_row_id_key" not in constraints:
        op.create_unique_constraint("standardized_results_raw_row_id_key", "standardized_results", ["raw_row_id"])
