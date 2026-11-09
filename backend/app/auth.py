"""비밀번호 해시, 로그인 세션 확인, 현재 프로젝트 확인을 담당합니다."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256, scrypt
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .database import get_session


# 브라우저 쿠키에 사용할 이름입니다. 쿠키에는 비밀번호를 넣지 않습니다.
SESSION_COOKIE = "scm_session"
PROJECT_COOKIE = "scm_project"


@dataclass(frozen=True)
class AuthContext:
    """인증된 사용자와 현재 선택한 프로젝트를 한 묶음으로 전달합니다."""
    user: models.User
    project: models.Project


def hash_password(password: str) -> str:
    """비밀번호 원문을 복구할 수 없는 scrypt 해시로 바꿉니다.

    salt를 매번 새로 만들기 때문에 같은 비밀번호도 저장값은 서로 달라집니다.
    """
    salt = secrets.token_bytes(16)
    digest = scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """입력한 비밀번호가 저장된 해시와 같은지 안전하게 비교합니다."""
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        candidate = scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(candidate.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def token_digest(token: str) -> str:
    """로그인 토큰 원문 대신 저장할 SHA-256 해시를 계산합니다."""
    return sha256(token.encode("utf-8")).hexdigest()


def get_optional_user(request: Request, session: Session) -> models.User | None:
    """유효한 로그인 쿠키가 있으면 사용자를, 없으면 None을 반환합니다."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    login = session.scalar(select(models.LoginSession).where(models.LoginSession.token_hash == token_digest(token)))
    if login is None or login.expires_at <= datetime.now(timezone.utc):
        return None
    return session.get(models.User, login.user_id)


def require_context(request: Request, session: Session = Depends(get_session)) -> AuthContext:
    """로그인이 꼭 필요한 API에서 사용자와 접근 가능한 프로젝트를 확인합니다.

    쿠키의 프로젝트가 사용자 권한 목록에 없으면 접근 가능한 첫 프로젝트를 선택합니다.
    """
    user = get_optional_user(request, session)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="로그인이 필요합니다.")
    memberships = session.scalars(select(models.ProjectMember).where(models.ProjectMember.user_id == user.id)).all()
    allowed_ids = {membership.project_id for membership in memberships}
    project_id = request.cookies.get(PROJECT_COOKIE)
    if project_id not in allowed_ids:
        project_id = next(iter(allowed_ids), None)
    project = session.get(models.Project, project_id) if project_id else None
    if project is None:
        raise HTTPException(status_code=403, detail="접근 가능한 프로젝트가 없습니다.")
    return AuthContext(user=user, project=project)
