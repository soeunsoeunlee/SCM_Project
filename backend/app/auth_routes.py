"""최초 계정 설정, 로그인·로그아웃, 프로젝트 전환·사본 생성 API입니다."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from . import models
from .auth import PROJECT_COOKIE, SESSION_COOKIE, AuthContext, get_optional_user, hash_password, require_context, token_digest, verify_password
from .database import get_session
from .project_service import delete_project_data, project_summary


# 이 파일의 모든 주소 앞에는 /api가 붙습니다.
router = APIRouter(prefix="/api")


class SetupInput(BaseModel):
    """사이트에서 최초 관리자 계정을 만들 때 받는 값입니다."""
    display_name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=240)
    password: str = Field(min_length=12, max_length=128)
    project_name: str = Field(min_length=2, max_length=160)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or "." not in value.rsplit("@", 1)[-1]:
            raise ValueError("이메일 형식을 확인해 주세요.")
        return value


class LoginInput(BaseModel):
    """일반 로그인 요청 값입니다."""
    email: str = Field(min_length=5, max_length=240)
    password: str = Field(min_length=1, max_length=128)


class ProjectInput(BaseModel):
    """프로젝트 사본 이름을 받을 때 사용합니다."""
    name: str = Field(min_length=2, max_length=160)


class ProjectDeleteInput(BaseModel):
    """영구 삭제 전에 사용자가 다시 입력한 프로젝트 이름입니다."""
    confirmation_name: str = Field(min_length=2, max_length=160)


class ProjectAsOfDateInput(BaseModel):
    """현재 프로젝트의 생산·선적 상태 판단 기준일입니다."""
    as_of_date: date


def _project_rows(session: Session, user_id: str) -> list[dict]:
    """사용자가 참여한 프로젝트와 역할을 화면용 목록으로 바꿉니다."""
    rows = session.execute(
        select(models.Project, models.ProjectMember.role)
        .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == user_id)
        .order_by(models.Project.created_at)
    ).all()
    return [
        {
            "id": project.id,
            "name": project.name,
            "role": role,
            "copied_from_id": project.copied_from_id,
            "created_at": project.created_at.isoformat() if project.created_at else None,
            "as_of_date": project.as_of_date.isoformat(),
            **project_summary(session, project.id),
        }
        for project, role in rows
    ]


def _set_login(response: Response, session: Session, user: models.User, project_id: str) -> None:
    """새 로그인 세션을 DB에 저장하고 브라우저에 HttpOnly 쿠키를 설정합니다."""
    raw_token = uuid4().hex + uuid4().hex
    session.add(models.LoginSession(
        id=str(uuid4()), user_id=user.id, token_hash=token_digest(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(days=14),
    ))
    response.set_cookie(SESSION_COOKIE, raw_token, httponly=True, samesite="strict", secure=False, max_age=14 * 24 * 3600, path="/")
    response.set_cookie(PROJECT_COOKIE, project_id, httponly=True, samesite="strict", secure=False, max_age=14 * 24 * 3600, path="/")


@router.get("/auth/status")
def auth_status(request: Request, session: Session = Depends(get_session)) -> dict:
    """첫 설정 필요 여부와 현재 로그인·프로젝트 상태를 반환합니다."""
    needs_setup = (session.scalar(select(func.count()).select_from(models.User)) or 0) == 0
    user = get_optional_user(request, session)
    if user is None:
        return {"authenticated": False, "needs_setup": needs_setup, "user": None, "projects": [], "current_project_id": None}
    projects = _project_rows(session, user.id)
    allowed = {project["id"] for project in projects}
    current = request.cookies.get(PROJECT_COOKIE)
    if current not in allowed:
        current = projects[0]["id"] if projects else None
    return {"authenticated": True, "needs_setup": False, "user": {"id": user.id, "email": user.email, "display_name": user.display_name}, "projects": projects, "current_project_id": current}


@router.post("/auth/setup", status_code=status.HTTP_201_CREATED)
def setup(payload: SetupInput, response: Response, session: Session = Depends(get_session)) -> dict:
    """사용자가 한 명도 없을 때만 첫 관리자와 기본 프로젝트를 만듭니다."""
    if (session.scalar(select(func.count()).select_from(models.User)) or 0) > 0:
        raise HTTPException(status_code=409, detail="초기 관리자 설정이 이미 완료되었습니다.")
    user = models.User(id=str(uuid4()), email=payload.email.lower(), display_name=payload.display_name.strip(), password_hash=hash_password(payload.password))
    session.add(user)
    session.flush()
    project = session.get(models.Project, "project-demo")
    if project is None:
        project = models.Project(id="project-demo", name=payload.project_name.strip(), owner_user_id=user.id)
        session.add(project)
    else:
        project.name = payload.project_name.strip()
        project.owner_user_id = user.id
    session.add(models.ProjectMember(user_id=user.id, project_id=project.id, role="owner"))
    _set_login(response, session, user, project.id)
    session.commit()
    return {"message": "관리자 계정과 첫 프로젝트를 만들었습니다."}


@router.post("/auth/login")
def login(payload: LoginInput, response: Response, session: Session = Depends(get_session)) -> dict:
    """이메일과 비밀번호를 확인하고 14일 로그인 세션을 만듭니다."""
    user = session.scalar(select(models.User).where(models.User.email == payload.email.strip().lower()))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    projects = _project_rows(session, user.id)
    if not projects:
        raise HTTPException(status_code=403, detail="접근 가능한 프로젝트가 없습니다.")
    _set_login(response, session, user, projects[0]["id"])
    session.commit()
    return {"message": "로그인했습니다."}


@router.post("/auth/logout")
def logout(request: Request, response: Response, session: Session = Depends(get_session)) -> dict:
    """현재 세션을 DB와 브라우저 쿠키에서 모두 제거합니다."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        login = session.scalar(select(models.LoginSession).where(models.LoginSession.token_hash == token_digest(token)))
        if login:
            session.delete(login)
            session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(PROJECT_COOKIE, path="/")
    return {"message": "로그아웃했습니다."}


@router.post("/projects/select")
def select_project(payload: dict, response: Response, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """사용자가 실제로 참여한 프로젝트인지 확인한 뒤 현재 프로젝트를 바꿉니다."""
    project_id = str(payload.get("project_id", ""))
    membership = session.scalar(select(models.ProjectMember).where(models.ProjectMember.user_id == context.user.id, models.ProjectMember.project_id == project_id))
    if membership is None:
        raise HTTPException(status_code=403, detail="이 프로젝트에 접근할 수 없습니다.")
    response.set_cookie(PROJECT_COOKIE, project_id, httponly=True, samesite="strict", secure=False, max_age=14 * 24 * 3600, path="/")
    return {"message": "프로젝트를 전환했습니다.", "project_id": project_id}


@router.post("/projects/as-of-date")
def update_as_of_date(payload: ProjectAsOfDateInput, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """현재 프로젝트의 모든 생산·선적 상태가 바라볼 기준 날짜를 저장합니다."""
    context.project.as_of_date = payload.as_of_date
    session.commit()
    return {"message": "기준 날짜를 변경했습니다.", "as_of_date": payload.as_of_date.isoformat()}


@router.post("/projects/copy", status_code=status.HTTP_201_CREATED)
def copy_project(payload: ProjectInput, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """현재 Sales Master와 생산·선적 상세를 독립된 새 프로젝트로 복사합니다.

    새 내부 ID를 발급하므로 사본에서 수정해도 원본 주문에는 영향을 주지 않습니다.
    원본 자료는 변경되지 않는 출처이므로 같은 source_id를 참조할 수 있습니다.
    """
    name = payload.name.strip()
    duplicate = session.scalar(
        select(models.Project.id)
        .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == context.user.id, func.lower(models.Project.name) == name.lower())
        .limit(1)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="같은 이름의 프로젝트가 이미 있습니다.")
    new_project = models.Project(id=str(uuid4()), name=name, owner_user_id=context.user.id, copied_from_id=context.project.id, as_of_date=context.project.as_of_date)
    session.add(new_project)
    session.flush()
    session.add(models.ProjectMember(user_id=context.user.id, project_id=new_project.id, role="owner"))
    # 현재 프로젝트에서 직접 추가한 법인은 사본에서도 독립적으로 수정할 수 있게 새 ID로 복제합니다.
    company_id_map: dict[str, str] = {}
    for company in session.scalars(select(models.Company).where(models.Company.project_id == context.project.id)):
        new_company_id = str(uuid4())
        company_id_map[company.id] = new_company_id
        session.add(models.Company(id=new_company_id, name=company.name, kind=company.kind, country=company.country, project_id=new_project.id))
    session.flush()
    source_orders = session.scalars(
        select(models.Order).where(models.Order.project_id == context.project.id).options(
            selectinload(models.Order.production_details), selectinload(models.Order.shipment_details), selectinload(models.Order.manual_override)
        )
    ).all()
    copied = 0
    # 주문마다 새 ID를 만들고, 생산·선적 상세도 새 행으로 복제합니다.
    for source_order in source_orders:
        new_order_id = str(uuid4())
        clone = models.Order(
            id=new_order_id, project_id=new_project.id, order_number=source_order.order_number,
            sales_company_id=company_id_map.get(source_order.sales_company_id, source_order.sales_company_id), production_company_id=company_id_map.get(source_order.production_company_id, source_order.production_company_id),
            target_month=source_order.target_month, po_number=source_order.po_number, po_line_number=source_order.po_line_number,
            model=source_order.model, customer=source_order.customer, quantity=source_order.quantity, currency=source_order.currency,
            unit_price=source_order.unit_price, net_sales_amount=source_order.net_sales_amount, gross_sales_amount=source_order.gross_sales_amount,
            net_sales_type=source_order.net_sales_type, gross_sales_type=source_order.gross_sales_type,
            production_site_source=source_order.production_site_source, sales_region=source_order.sales_region,
            erp_status=source_order.erp_status, match_status=source_order.match_status, source_id=source_order.source_id,
        )
        session.add(clone)
        session.flush()
        for row in source_order.production_details:
            session.add(models.ProductionAllocation(id=str(uuid4()), order_id=new_order_id, source_id=row.source_id, quantity=row.quantity, status=row.status, production_date=row.production_date, is_demo=row.is_demo, is_manual=row.is_manual))
        for row in source_order.shipment_details:
            session.add(models.ShipmentAllocation(id=str(uuid4()), order_id=new_order_id, source_id=row.source_id, quantity=row.quantity, status=row.status, shipment_date=row.shipment_date, eta_date=row.eta_date, carrier=row.carrier, vessel_name=row.vessel_name, is_demo=row.is_demo, is_manual=row.is_manual))
        if source_order.manual_override:
            session.add(models.OrderManualOverride(order_id=new_order_id, project_id=new_project.id, protected_fields=list(source_order.manual_override.protected_fields), updated_by_user_id=context.user.id))
        session.add(models.ChangeEvent(id=str(uuid4()), order_id=new_order_id, source_id=source_order.source_id, event_type="프로젝트 사본 생성", changed_at=datetime.now(timezone.utc), after_value=f"{context.project.name}에서 현재 주문 상태 복사"))
        copied += 1
    session.commit()
    return {"project": {"id": new_project.id, "name": new_project.name, "copied_from_id": context.project.id}, "copied_orders": copied, "message": "현재 Sales Master 상태를 독립된 프로젝트 사본으로 만들었습니다."}


@router.post("/projects", status_code=status.HTTP_201_CREATED)
def create_blank_project(payload: ProjectInput, response: Response, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """주문·업로드·PSI가 하나도 없는 새 프로젝트를 만듭니다."""
    name = payload.name.strip()
    duplicate = session.scalar(
        select(models.Project.id)
        .join(models.ProjectMember, models.ProjectMember.project_id == models.Project.id)
        .where(models.ProjectMember.user_id == context.user.id, func.lower(models.Project.name) == name.lower())
        .limit(1)
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="같은 이름의 프로젝트가 이미 있습니다.")
    project = models.Project(id=str(uuid4()), name=name, owner_user_id=context.user.id, copied_from_id=None)
    session.add(project)
    session.flush()
    session.add(models.ProjectMember(user_id=context.user.id, project_id=project.id, role="owner"))
    response.set_cookie(PROJECT_COOKIE, project.id, httponly=True, samesite="strict", secure=False, max_age=14 * 24 * 3600, path="/")
    session.commit()
    return {
        "project": {"id": project.id, "name": project.name, "copied_from_id": None},
        "message": "빈 프로젝트를 만들었습니다. 주문·업로드·PSI 데이터는 0건입니다.",
    }


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, payload: ProjectDeleteInput, request: Request, response: Response, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """소유자가 이름을 확인한 프로젝트와 그 종속 데이터를 영구 삭제합니다."""
    membership = session.scalar(
        select(models.ProjectMember).where(
            models.ProjectMember.user_id == context.user.id,
            models.ProjectMember.project_id == project_id,
        )
    )
    project = session.get(models.Project, project_id)
    if project is None or membership is None:
        raise HTTPException(status_code=404, detail="삭제할 프로젝트를 찾을 수 없습니다.")
    if membership.role != "owner" or project.owner_user_id != context.user.id:
        raise HTTPException(status_code=403, detail="프로젝트 소유자만 삭제할 수 있습니다.")
    if payload.confirmation_name.strip() != project.name:
        raise HTTPException(status_code=400, detail="확인을 위해 입력한 프로젝트 이름이 일치하지 않습니다.")
    remaining = _project_rows(session, context.user.id)
    remaining = [item for item in remaining if item["id"] != project_id]
    if not remaining:
        raise HTTPException(status_code=409, detail="마지막 프로젝트는 삭제할 수 없습니다. 먼저 빈 프로젝트를 하나 만들어 주세요.")

    deleted = delete_project_data(session, project_id)
    current_project_id = request.cookies.get(PROJECT_COOKIE)
    next_project_id = remaining[0]["id"] if current_project_id == project_id else current_project_id
    if next_project_id:
        response.set_cookie(PROJECT_COOKIE, next_project_id, httponly=True, samesite="strict", secure=False, max_age=14 * 24 * 3600, path="/")
    session.commit()
    return {
        "message": "프로젝트와 프로젝트에만 속한 데이터를 영구 삭제했습니다.",
        "deleted": deleted,
        "next_project_id": next_project_id,
    }
