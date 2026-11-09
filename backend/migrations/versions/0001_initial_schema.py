"""최초 SCM 데모 데이터베이스 표 전체를 생성합니다.

Revision ID: 0001_initial_schema
Revises:
"""
from alembic import op

from app.database import Base
from app import models  # noqa: F401

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """models.py에 정의된 모든 표를 처음 생성합니다."""
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    """초기 표 전체를 제거합니다. 일반 실행에서는 사용하지 않습니다."""
    Base.metadata.drop_all(bind=op.get_bind())
