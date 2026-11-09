"""선적 상세에 ETA와 선박명을 추가합니다."""

from alembic import op
import sqlalchemy as sa


revision = "0010_shipment_eta_vessel"
down_revision = "0009_risk_allocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shipment_allocations", sa.Column("eta_date", sa.Date(), nullable=True))
    op.add_column("shipment_allocations", sa.Column("vessel_name", sa.String(length=120), nullable=True))
    # 기존 합성 선적 자료에도 ETD와 구분되는 ETA·선박 예시를 채웁니다.
    op.execute("""
        UPDATE shipment_allocations SET
          eta_date = CASE id
            WHEN 'ship-001-a' THEN DATE '2026-11-08' WHEN 'ship-001-b' THEN DATE '2026-11-12'
            WHEN 'ship-003-a' THEN DATE '2026-11-15' WHEN 'ship-003-b' THEN DATE '2026-11-20'
            WHEN 'ship-006-a' THEN DATE '2026-11-06' WHEN 'ship-007-a' THEN DATE '2026-10-30'
            ELSE eta_date END,
          carrier = CASE id
            WHEN 'ship-001-a' THEN 'Maersk' WHEN 'ship-001-b' THEN 'Maersk'
            WHEN 'ship-003-a' THEN 'Hapag-Lloyd' WHEN 'ship-003-b' THEN 'Hapag-Lloyd'
            WHEN 'ship-006-a' THEN 'MSC' WHEN 'ship-007-a' THEN 'CMA CGM'
            ELSE carrier END,
          vessel_name = CASE id
            WHEN 'ship-001-a' THEN 'MAERSK SANA' WHEN 'ship-001-b' THEN 'MAERSK SANA'
            WHEN 'ship-003-a' THEN 'AL MASHRAB' WHEN 'ship-003-b' THEN 'AL MASHRAB'
            WHEN 'ship-006-a' THEN 'MSC RANIA' WHEN 'ship-007-a' THEN 'CMA CGM TOSCA'
            ELSE vessel_name END
        WHERE id IN ('ship-001-a','ship-001-b','ship-003-a','ship-003-b','ship-006-a','ship-007-a')
    """)


def downgrade() -> None:
    op.drop_column("shipment_allocations", "vessel_name")
    op.drop_column("shipment_allocations", "eta_date")
