"""Add project companies and production company to operational uploads."""

from alembic import op
import sqlalchemy as sa


revision = "0016_project_companies"
down_revision = "0015_production_multi_sales"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("project_id", sa.String(length=40), nullable=True))
    op.create_foreign_key("fk_companies_project_id", "companies", "projects", ["project_id"], ["id"])
    op.create_index("ix_companies_project_id", "companies", ["project_id"])
    op.drop_constraint("uq_company_kind_name", "companies", type_="unique")
    op.create_unique_constraint("uq_company_project_kind_name", "companies", ["project_id", "kind", "name"])
    op.add_column("upload_batches", sa.Column("production_company_id", sa.String(length=40), nullable=True))
    op.create_foreign_key("fk_upload_batches_production_company_id", "upload_batches", "companies", ["production_company_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_upload_batches_production_company_id", "upload_batches", type_="foreignkey")
    op.drop_column("upload_batches", "production_company_id")
    op.drop_constraint("uq_company_project_kind_name", "companies", type_="unique")
    op.create_unique_constraint("uq_company_kind_name", "companies", ["kind", "name"])
    op.drop_index("ix_companies_project_id", table_name="companies")
    op.drop_constraint("fk_companies_project_id", "companies", type_="foreignkey")
    op.drop_column("companies", "project_id")
