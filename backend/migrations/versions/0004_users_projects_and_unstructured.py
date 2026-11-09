"""로그인·프로젝트 분리·비정형 원본 저장 구조를 추가합니다.

Revision ID: 0004_users_projects
Revises: 0003_align_upload_constraints
"""

from alembic import op
import sqlalchemy as sa


revision = "0004_users_projects"
down_revision = "0003_align_upload_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """새 표와 project_id를 추가하고 기존 13개 주문을 기본 프로젝트에 귀속시킵니다."""
    op.create_table(
        "users",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("email", sa.String(240), nullable=False, unique=True),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("password_hash", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "projects",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("owner_user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("copied_from_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "project_members",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="owner"),
        sa.UniqueConstraint("user_id", "project_id", name="uq_project_member"),
    )
    op.create_table(
        "login_sessions",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("user_id", sa.String(40), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.execute("INSERT INTO projects (id, name) VALUES ('project-demo', '202610 SCM 프로세스 관리프로젝트')")

    with op.batch_alter_table("orders") as batch:
        batch.add_column(sa.Column("project_id", sa.String(40), nullable=False, server_default="project-demo"))
        batch.create_foreign_key("fk_orders_project", "projects", ["project_id"], ["id"])
        batch.create_index("ix_orders_project_id", ["project_id"])
        batch.drop_constraint("orders_order_number_key", type_="unique")
        batch.create_unique_constraint("uq_project_order_number", ["project_id", "order_number"])

    with op.batch_alter_table("upload_batches") as batch:
        batch.add_column(sa.Column("project_id", sa.String(40), nullable=False, server_default="project-demo"))
        batch.create_foreign_key("fk_upload_batches_project", "projects", ["project_id"], ["id"])
        batch.create_index("ix_upload_batches_project_id", ["project_id"])
        batch.drop_constraint("upload_batches_file_hash_key", type_="unique")
        batch.create_unique_constraint("uq_project_upload_hash", ["project_id", "file_hash"])

    with op.batch_alter_table("mapping_profiles") as batch:
        batch.add_column(sa.Column("project_id", sa.String(40), nullable=False, server_default="project-demo"))
        batch.create_foreign_key("fk_mapping_profiles_project", "projects", ["project_id"], ["id"])
        batch.drop_constraint("uq_mapping_company_headers", type_="unique")
        batch.create_unique_constraint("uq_mapping_project_company_headers", ["project_id", "sales_company_id", "header_signature"])

    with op.batch_alter_table("plan_versions") as batch:
        batch.add_column(sa.Column("project_id", sa.String(40), nullable=False, server_default="project-demo"))
        batch.create_foreign_key("fk_plan_versions_project", "projects", ["project_id"], ["id"])
        batch.create_index("ix_plan_versions_project_id", ["project_id"])

    op.create_table(
        "unstructured_inputs",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("project_id", sa.String(40), sa.ForeignKey("projects.id"), nullable=False, index=True),
        sa.Column("source_id", sa.String(40), sa.ForeignKey("source_materials.id"), nullable=False, unique=True),
        sa.Column("data_type", sa.String(30), nullable=False),
        sa.Column("input_type", sa.String(20), nullable=False),
        sa.Column("file_name", sa.String(240), nullable=True),
        sa.Column("content_type", sa.String(120), nullable=False),
        sa.Column("raw_content", sa.LargeBinary(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_fields", sa.JSON(), nullable=False),
        sa.Column("extraction_status", sa.String(40), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.execute("UPDATE companies SET name = 'Northstar' WHERE id = 'sales-a'")
    op.execute("UPDATE companies SET name = 'Bluewave' WHERE id = 'sales-b'")
    op.execute("UPDATE companies SET name = 'Sunrise' WHERE id = 'sales-c'")


def downgrade() -> None:
    """0004 변경을 제거하고 프로젝트 분리 전 구조로 되돌립니다."""
    op.execute("UPDATE companies SET name = name || ' 판매법인' WHERE id IN ('sales-a', 'sales-b', 'sales-c')")
    op.drop_table("unstructured_inputs")
    with op.batch_alter_table("plan_versions") as batch:
        batch.drop_index("ix_plan_versions_project_id")
        batch.drop_constraint("fk_plan_versions_project", type_="foreignkey")
        batch.drop_column("project_id")
    with op.batch_alter_table("mapping_profiles") as batch:
        batch.drop_constraint("uq_mapping_project_company_headers", type_="unique")
        batch.drop_constraint("fk_mapping_profiles_project", type_="foreignkey")
        batch.drop_column("project_id")
        batch.create_unique_constraint("uq_mapping_company_headers", ["sales_company_id", "header_signature"])
    with op.batch_alter_table("upload_batches") as batch:
        batch.drop_constraint("uq_project_upload_hash", type_="unique")
        batch.create_unique_constraint("upload_batches_file_hash_key", ["file_hash"])
        batch.drop_index("ix_upload_batches_project_id")
        batch.drop_constraint("fk_upload_batches_project", type_="foreignkey")
        batch.drop_column("project_id")
    with op.batch_alter_table("orders") as batch:
        batch.drop_constraint("uq_project_order_number", type_="unique")
        batch.create_unique_constraint("orders_order_number_key", ["order_number"])
        batch.drop_index("ix_orders_project_id")
        batch.drop_constraint("fk_orders_project", type_="foreignkey")
        batch.drop_column("project_id")
    op.drop_table("login_sessions")
    op.drop_table("project_members")
    op.drop_table("projects")
    op.drop_table("users")
