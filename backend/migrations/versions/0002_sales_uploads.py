"""판매자료 업로드·매핑·원본 행·표준화 결과 저장 구조를 추가합니다.

Revision ID: 0002_sales_uploads
Revises: 0001_initial_schema
"""
from alembic import op
import sqlalchemy as sa


revision = "0002_sales_uploads"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    """현재 DB에 이미 있는 표 이름을 확인합니다."""
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    """특정 표에 이미 있는 컬럼 이름을 확인합니다."""
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    """기존 주문 표를 보완하고 업로드 관련 새 표를 안전하게 추가합니다."""
    order_columns = _columns("orders")
    additions = {
        "po_line_number": sa.Column("po_line_number", sa.String(80), nullable=True),
        "net_sales_amount": sa.Column("net_sales_amount", sa.Numeric(18, 4), nullable=True),
        "gross_sales_amount": sa.Column("gross_sales_amount", sa.Numeric(18, 4), nullable=True),
        "net_sales_type": sa.Column("net_sales_type", sa.String(12), nullable=True),
        "gross_sales_type": sa.Column("gross_sales_type", sa.String(12), nullable=True),
        "production_site_source": sa.Column("production_site_source", sa.String(120), nullable=True),
        "sales_region": sa.Column("sales_region", sa.String(120), nullable=True),
        "upload_batch_id": sa.Column("upload_batch_id", sa.String(40), nullable=True),
        "upload_result_id": sa.Column("upload_result_id", sa.Integer(), nullable=True),
    }
    for name, column in additions.items():
        if name not in order_columns:
            op.add_column("orders", column)
    indexes = {item["name"] for item in sa.inspect(op.get_bind()).get_indexes("orders")}
    if "ix_orders_upload_batch_id" not in indexes:
        op.create_index("ix_orders_upload_batch_id", "orders", ["upload_batch_id"])
    uniques = {item["name"] for item in sa.inspect(op.get_bind()).get_unique_constraints("orders")}
    if "uq_orders_upload_result_id" not in uniques:
        op.create_unique_constraint("uq_orders_upload_result_id", "orders", ["upload_result_id"])

    tables = _tables()
    if "upload_batches" not in tables:
        op.create_table(
            "upload_batches",
            sa.Column("id", sa.String(40), primary_key=True),
            sa.Column("source_id", sa.String(40), sa.ForeignKey("source_materials.id"), nullable=False, unique=True),
            sa.Column("file_name", sa.String(240), nullable=False),
            sa.Column("file_extension", sa.String(10), nullable=False),
            sa.Column("file_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("file_content", sa.LargeBinary(), nullable=False),
            sa.Column("sales_company_id", sa.String(40), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("target_month", sa.String(7), nullable=False),
            sa.Column("reference_date", sa.Date(), nullable=False),
            sa.Column("scope_type", sa.String(24), nullable=False),
            sa.Column("scope_description", sa.String(300), nullable=False),
            sa.Column("selected_sheet", sa.String(160), nullable=True),
            sa.Column("header_row", sa.Integer(), nullable=True),
            sa.Column("header_signature", sa.String(64), nullable=True),
            sa.Column("mapping_json", sa.JSON(), nullable=True),
            sa.Column("pricing_json", sa.JSON(), nullable=True),
            sa.Column("status", sa.String(40), nullable=False),
            sa.Column("application_mode", sa.String(40), nullable=True),
            sa.Column("result_message", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        )
    tables = _tables()
    if "mapping_profiles" not in tables:
        op.create_table(
            "mapping_profiles",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("sales_company_id", sa.String(40), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("header_signature", sa.String(64), nullable=False),
            sa.Column("headers_json", sa.JSON(), nullable=False),
            sa.Column("mapping_json", sa.JSON(), nullable=False),
            sa.Column("pricing_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("sales_company_id", "header_signature", name="uq_mapping_company_headers"),
        )
    if "upload_raw_rows" not in tables:
        op.create_table(
            "upload_raw_rows",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("batch_id", sa.String(40), sa.ForeignKey("upload_batches.id"), nullable=False),
            sa.Column("sheet_name", sa.String(160), nullable=False),
            sa.Column("row_number", sa.Integer(), nullable=False),
            sa.Column("raw_values", sa.JSON(), nullable=False),
            sa.Column("formula_missing_columns", sa.JSON(), nullable=False),
            sa.Column("merged_columns", sa.JSON(), nullable=False),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("issues", sa.JSON(), nullable=False),
            sa.UniqueConstraint("batch_id", "sheet_name", "row_number", name="uq_upload_raw_position"),
        )
        op.create_index("ix_upload_raw_rows_batch_id", "upload_raw_rows", ["batch_id"])
    tables = _tables()
    if "standardized_results" not in tables:
        op.create_table(
            "standardized_results",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("batch_id", sa.String(40), sa.ForeignKey("upload_batches.id"), nullable=False),
            sa.Column("raw_row_id", sa.Integer(), sa.ForeignKey("upload_raw_rows.id"), nullable=False, unique=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("issues", sa.JSON(), nullable=False),
            sa.Column("po_number", sa.String(80), nullable=True),
            sa.Column("po_line_number", sa.String(80), nullable=True),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column("customer", sa.String(120), nullable=True),
            sa.Column("quantity", sa.Integer(), nullable=True),
            sa.Column("net_sales_value", sa.Numeric(18, 4), nullable=True),
            sa.Column("gross_sales_value", sa.Numeric(18, 4), nullable=True),
            sa.Column("net_sales_total", sa.Numeric(18, 4), nullable=True),
            sa.Column("gross_sales_total", sa.Numeric(18, 4), nullable=True),
            sa.Column("currency", sa.String(3), nullable=True),
            sa.Column("production_site", sa.String(120), nullable=True),
            sa.Column("production_company_id", sa.String(40), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("sales_region", sa.String(120), nullable=True),
            sa.Column("applied_order_id", sa.String(40), nullable=True, unique=True),
        )
        op.create_index("ix_standardized_results_batch_id", "standardized_results", ["batch_id"])


def downgrade() -> None:
    """0002에서 추가한 업로드 구조를 이전 상태로 되돌립니다."""
    for table in ("standardized_results", "upload_raw_rows", "mapping_profiles", "upload_batches"):
        if table in _tables():
            op.drop_table(table)
    for name in (
        "upload_result_id", "upload_batch_id", "sales_region", "production_site_source",
        "gross_sales_type", "net_sales_type", "gross_sales_amount", "net_sales_amount", "po_line_number",
    ):
        if name in _columns("orders"):
            op.drop_column("orders", name)
