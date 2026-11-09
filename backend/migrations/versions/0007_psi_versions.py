"""PSI 원본 버전과 모델·주차별 값을 보존하는 표를 추가합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0007_psi_versions"
down_revision = "0006_rename_demo_sources"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "psi_versions",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("project_id", sa.String(length=40), nullable=False),
        sa.Column("source_id", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("reference_date", sa.Date(), nullable=False),
        sa.Column("file_name", sa.String(length=240), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_content", sa.LargeBinary(), nullable=False),
        sa.Column("sheet_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("validation_issues", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.String(length=40), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["source_materials.id"]),
        sa.ForeignKeyConstraint(["approved_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_id"),
        sa.UniqueConstraint("project_id", "file_hash", name="uq_project_psi_file_hash"),
    )
    op.create_index(op.f("ix_psi_versions_project_id"), "psi_versions", ["project_id"], unique=False)
    op.create_table(
        "psi_values",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("version_id", sa.String(length=40), nullable=False),
        sa.Column("sales_company", sa.String(length=120), nullable=False),
        sa.Column("production_company", sa.String(length=120), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=20), nullable=False),
        sa.Column("period_key", sa.String(length=12), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("source_column", sa.Integer(), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_psi_quantity_nonnegative"),
        sa.ForeignKeyConstraint(["version_id"], ["psi_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("version_id", "sales_company", "production_company", "model", "metric", "period_key", name="uq_psi_version_cell"),
    )
    op.create_index(op.f("ix_psi_values_version_id"), "psi_values", ["version_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_psi_values_version_id"), table_name="psi_values")
    op.drop_table("psi_values")
    op.drop_index(op.f("ix_psi_versions_project_id"), table_name="psi_versions")
    op.drop_table("psi_versions")
