"""Store upload-wide common mapping values."""

from alembic import op
import sqlalchemy as sa


revision = "0017_upload_common_values"
down_revision = "0016_project_companies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_batches", sa.Column("common_values_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("upload_batches", "common_values_json")
