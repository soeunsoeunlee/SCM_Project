"""데이터베이스 연결을 만들고 API 요청마다 안전한 DB 세션을 제공합니다."""

import os
from collections.abc import Generator
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """모든 SQLAlchemy 모델이 상속하는 공통 부모 클래스입니다."""
    pass


# 프로젝트 루트의 .env를 읽습니다. 실제 비밀번호는 Git에 포함하지 않습니다.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


def get_database_url() -> str:
    """환경변수에서 DB 주소를 읽고, 없으면 로컬 PostgreSQL 기본 주소를 사용합니다.

    과거 SQLite 데모 경로도 읽을 수 있게 남겨 두었지만 현재 기본 실행 DB는 PostgreSQL입니다.
    """
    database_url = os.getenv("DATABASE_URL", "postgresql+psycopg://scm@127.0.0.1:5433/scm_demo")
    if database_url.startswith("sqlite:///"):
        raw_path = database_url.removeprefix("sqlite:///")
        if raw_path.startswith("./"):
            raw_path = raw_path[2:]
        if raw_path and raw_path != ":memory:":
            sqlite_path = Path(raw_path)
            if not sqlite_path.is_absolute():
                sqlite_path = Path(__file__).resolve().parents[1] / sqlite_path
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{sqlite_path.as_posix()}"
    return database_url


def make_engine(url: str | None = None):
    """SQLAlchemy가 실제 DB와 통신할 엔진을 만듭니다.

    pool_pre_ping은 오래된 연결을 사용하기 전에 살아 있는지 확인하는 안전장치입니다.
    """
    database_url = url or get_database_url()
    options = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=options, pool_pre_ping=True)


# 애플리케이션 전체가 공유하는 DB 엔진입니다.
engine = make_engine()
# 실제 조회·저장 작업 단위인 Session을 만드는 공장입니다.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Generator[Session, None, None]:
    """FastAPI 요청 하나에 DB 세션 하나를 빌려주고 요청이 끝나면 닫습니다."""
    with SessionLocal() as session:
        yield session
