"""Alembic이 현재 모델과 PostgreSQL을 연결해 마이그레이션을 실행하는 설정입니다."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import create_engine

from app import models  # noqa: F401
from app.database import Base, get_database_url

# alembic.ini 설정에 .env에서 읽은 실제 DB 주소를 넣습니다.
config = context.config
config.set_main_option("sqlalchemy.url", get_database_url().replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# models.py에 선언된 모든 표 구조를 자동 비교 기준으로 사용합니다.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """DB에 직접 연결하지 않고 실행할 SQL 스크립트만 만들 때 사용합니다."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """실제 DB에 연결하여 upgrade/downgrade를 적용할 때 사용합니다."""
    connectable = create_engine(
        get_database_url(),
        poolclass=pool.NullPool,
        connect_args={"check_same_thread": False} if get_database_url().startswith("sqlite") else {},
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
