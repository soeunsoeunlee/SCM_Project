"""PSI XLSX 업로드, 버전 비교, 승인과 원본 다운로드 API입니다."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from . import models
from .auth import AuthContext, require_context
from .database import get_session
from .psi_service import compare_values, parse_psi_workbook, risk_signals, values_to_psi
from .reporting import build_psi, psi_workbook
from .seed import load_orders
from .upload_deletion import UploadMaterialNotFound, UploadMaterialOutOfScope, clear_psi_derivatives, delete_psi_version


router = APIRouter(prefix="/api/psi", tags=["psi versions"])
MAX_PSI_BYTES = 15 * 1024 * 1024


class RiskAllocationInput(BaseModel):
    """사용자가 선택한 주문 한 건과 그 주문에 배분할 PSI 위험수량입니다."""

    order_id: str
    quantity: int = Field(gt=0)


class RiskDecision(BaseModel):
    """사용자가 PSI 신호에 내리는 주문 연결·승인 또는 보류 결정입니다."""
    decision: str
    order_id: str | None = None
    allocations: list[RiskAllocationInput] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=300)


def _load_version(session: Session, version_id: str, project_id: str) -> models.PsiVersion:
    """프로젝트 권한과 값 목록을 함께 확인하여 PSI 버전을 가져옵니다."""
    version = session.scalar(select(models.PsiVersion).options(selectinload(models.PsiVersion.values)).where(models.PsiVersion.id == version_id, models.PsiVersion.project_id == project_id))
    if version is None:
        raise HTTPException(status_code=404, detail="PSI 버전을 찾을 수 없습니다.")
    return version


def _summary(version: models.PsiVersion) -> dict:
    """목록 화면에 필요한 PSI 버전 머리정보입니다."""
    return {
        "id": version.id,
        "label": version.label,
        "base_month": version.base_month,
        "reference_date": version.reference_date.isoformat(),
        "file_name": version.file_name,
        "sheet_name": version.sheet_name,
        "status": version.status,
        "issue_count": len(version.validation_issues or []),
        "value_count": len(version.values),
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "created_by": version.created_by.display_name if version.created_by else "기존 등록",
        "approved_at": version.approved_at.isoformat() if version.approved_at else None,
    }


def _overwrite_psi_version(
    session: Session,
    version: models.PsiVersion,
    *,
    safe_name: str,
    content: bytes,
    parsed_date: date,
    base_month: str,
    parsed: dict,
    user_id: str,
) -> None:
    """같은 해시의 PSI 한 항목을 최신 메타데이터와 파싱 결과로 교체합니다."""
    clear_psi_derivatives(session, version.id, version.source_id)
    now = datetime.now(timezone.utc)
    source = session.get(models.SourceMaterial, version.source_id)
    if source is None:
        raise HTTPException(status_code=409, detail="PSI 원본 기록을 찾지 못했습니다.")
    source.name = safe_name
    source.description = f"PSI 원본 · {safe_name} · 기준월 {base_month} · 기준일 {parsed_date.isoformat()}"
    source.received_at = now.isoformat()
    version.label = safe_name
    version.base_month = base_month
    version.reference_date = parsed_date
    version.file_name = safe_name
    version.file_content = content
    version.sheet_name = parsed["sheet_name"]
    version.status = "검증 보류" if parsed["issues"] else "검토 대기"
    version.validation_issues = parsed["issues"]
    version.created_at = now
    version.created_by_user_id = user_id
    version.approved_at = None
    version.approved_by_user_id = None
    if not parsed["issues"]:
        session.add_all([models.PsiValue(version_id=version.id, **cell) for cell in parsed["cells"]])
    session.flush()
    if not parsed["issues"]:
        _sync_risk_reviews(session, version)


def _previous_version(session: Session, version: models.PsiVersion) -> models.PsiVersion | None:
    """현재 버전보다 먼저 승인된 PSI 중 가장 최근 버전을 비교 기준으로 찾습니다."""
    return session.scalar(select(models.PsiVersion).options(selectinload(models.PsiVersion.values)).where(
        models.PsiVersion.project_id == version.project_id,
        models.PsiVersion.id != version.id,
        models.PsiVersion.status == "승인",
        models.PsiVersion.created_at < version.created_at,
    ).order_by(models.PsiVersion.created_at.desc()).limit(1))


def _risk_candidates(session: Session, review: models.PsiRiskReview) -> list[models.Order]:
    """법인·생산법인·모델이 모두 같은 주문만 후보로 제시합니다."""
    return list(session.scalars(select(models.Order).join(models.Order.sales_company).where(
        models.Order.project_id == review.project_id,
        models.Company.name == review.sales_company,
        models.Order.model == review.model,
    ).order_by(models.Order.target_month, models.Order.order_number)))


def _review_summary(session: Session, review: models.PsiRiskReview) -> dict:
    """RISK 검토 기록과 안전하게 좁힌 주문 후보를 화면용으로 변환합니다."""
    candidates = [order for order in _risk_candidates(session, review) if order.production_company and order.production_company.name == review.production_company]
    return {
        "id": review.id, "version_id": review.version_id, "sales_company": review.sales_company,
        "production_company": review.production_company, "model": review.model,
        "period_key": review.period_key, "risk_type": review.risk_type, "quantity": review.quantity,
        "reason": review.reason, "status": review.status, "order_id": review.order_id,
        "review_note": review.review_note,
        "allocations": [{"id": allocation.id, "order_id": allocation.order_id, "quantity": allocation.quantity} for allocation in review.allocations],
        "candidates": [{"id": order.id, "order_number": order.order_number, "po_number": order.po_number, "target_month": order.target_month, "quantity": order.quantity} for order in candidates],
    }


def _sync_risk_reviews(session: Session, version: models.PsiVersion) -> None:
    """주차별 반복 신호를 모델·유형별 최신 신호 하나로 줄여 검토함에 저장합니다."""
    latest: dict[tuple[str, str, str, str], dict] = {}
    for signal in risk_signals(version.values, version.reference_date):
        key = (signal["sales_company"], signal["production_company"], signal["model"], signal["risk_type"])
        if key not in latest or signal["period_key"] > latest[key]["period_key"]:
            latest[key] = signal
    existing = set(session.scalars(select(models.PsiRiskReview.id).where(models.PsiRiskReview.version_id == version.id)))
    if existing:
        return
    for signal in latest.values():
        session.add(models.PsiRiskReview(id=str(uuid4()), version_id=version.id, project_id=version.project_id, **signal))


@router.get("/versions")
def list_versions(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> list[dict]:
    """현재 프로젝트의 PSI 버전을 최근 순서로 반환합니다."""
    versions = session.scalars(select(models.PsiVersion).options(selectinload(models.PsiVersion.values)).where(models.PsiVersion.project_id == context.project.id).order_by(models.PsiVersion.created_at.desc())).all()
    return [_summary(version) for version in versions]


@router.get("/versions/{version_id}")
def get_version(version_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """선택 버전의 PSI 표, 직전 버전 증감과 RISK 검토 신호를 반환합니다."""
    version = _load_version(session, version_id, context.project.id)
    previous = _previous_version(session, version)
    comparison = compare_values(version.values, previous.values) if previous else None
    reviews = session.scalars(select(models.PsiRiskReview).where(models.PsiRiskReview.version_id == version.id).order_by(models.PsiRiskReview.sales_company, models.PsiRiskReview.model, models.PsiRiskReview.risk_type)).all()
    return {
        "version": _summary(version),
        "validation_issues": version.validation_issues,
        "previous_version": _summary(previous) if previous else None,
        "comparison": comparison,
        "risk_signals": risk_signals(version.values, version.reference_date) if version.status != "검증 보류" else [],
        "risk_reviews": [_review_summary(session, review) for review in reviews],
        "psi": values_to_psi(version.values),
    }


@router.post("/versions", status_code=status.HTTP_201_CREATED)
async def upload_version(
    file: UploadFile = File(...),
    reference_date: str = Form(...),
    context: AuthContext = Depends(require_context),
    session: Session = Depends(get_session),
) -> dict:
    """PSI XLSX 원본과 모든 표준 셀을 저장하되 주문에는 반영하지 않습니다."""
    safe_name = Path(file.filename or "psi.xlsx").name
    if Path(safe_name).suffix.lower() != ".xlsx":
        raise HTTPException(status_code=415, detail="PSI 업로드는 XLSX 파일만 지원합니다.")
    try:
        parsed_date = date.fromisoformat(reference_date)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="자료 기준일을 확인해 주세요.") from error
    # 기준월은 사용자가 따로 입력하지 않고 자료 기준일에서 항상 계산합니다.
    # 이렇게 하면 기준일과 기준월이 서로 다르게 저장되는 일을 막을 수 있습니다.
    base_month = parsed_date.strftime("%Y-%m")
    content = await file.read(MAX_PSI_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일은 업로드할 수 없습니다.")
    if len(content) > MAX_PSI_BYTES:
        raise HTTPException(status_code=413, detail="PSI 파일은 15MB 이하여야 합니다.")
    digest = sha256(content).hexdigest()
    try:
        parsed = parse_psi_workbook(content)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    existing = session.scalar(select(models.PsiVersion).where(models.PsiVersion.project_id == context.project.id, models.PsiVersion.file_hash == digest))
    if existing:
        _overwrite_psi_version(
            session, existing, safe_name=safe_name, content=content,
            parsed_date=parsed_date, base_month=base_month, parsed=parsed,
            user_id=context.user.id,
        )
        session.commit()
        result = get_version(existing.id, context, session)
        result.update({"duplicate_file": False, "overwritten": True, "message": "기존 항목을 최신 정보로 덮어썼습니다."})
        return result
    version_id = str(uuid4())
    source_id = f"psi-{version_id}"
    source = models.SourceMaterial(
        id=source_id,
        name=safe_name,
        description=f"PSI 원본 · {safe_name} · 기준월 {base_month} · 기준일 {parsed_date.isoformat()}",
        received_at=datetime.now(timezone.utc).isoformat(),
        source_hash=sha256(f"{context.project.id}:psi:{digest}".encode()).hexdigest(),
    )
    version = models.PsiVersion(
        id=version_id, project_id=context.project.id, source_id=source_id,
        label=safe_name, base_month=base_month, reference_date=parsed_date, file_name=safe_name,
        file_hash=digest, file_content=content, sheet_name=parsed["sheet_name"],
        status="검증 보류" if parsed["issues"] else "검토 대기",
        validation_issues=parsed["issues"],
        created_by_user_id=context.user.id,
    )
    session.add_all([source, version])
    if not parsed["issues"]:
        session.add_all([models.PsiValue(version_id=version_id, **cell) for cell in parsed["cells"]])
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        duplicate = session.scalar(select(models.PsiVersion).where(models.PsiVersion.project_id == context.project.id, models.PsiVersion.file_hash == digest))
        if duplicate:
            _overwrite_psi_version(
                session, duplicate, safe_name=safe_name, content=content,
                parsed_date=parsed_date, base_month=base_month, parsed=parsed,
                user_id=context.user.id,
            )
            session.commit()
            result = get_version(duplicate.id, context, session)
            result.update({"duplicate_file": False, "overwritten": True, "message": "기존 항목을 최신 정보로 덮어썼습니다."})
            return result
        raise HTTPException(status_code=409, detail="PSI 버전을 저장하지 못했습니다.") from error
    saved = _load_version(session, version_id, context.project.id)
    if saved.status != "검증 보류":
        _sync_risk_reviews(session, saved)
        session.commit()
        saved = _load_version(session, version_id, context.project.id)
    result = get_version(saved.id, context, session)
    result.update({"duplicate_file": False, "overwritten": False, "message": "원본과 주차별 값을 저장했습니다. 비교와 RISK 신호를 확인한 뒤 승인해 주세요." if saved.status == "검토 대기" else "파일은 보존했지만 오류가 있어 비교·승인할 수 없습니다."})
    return result


@router.post("/versions/{version_id}/approve")
def approve_version(version_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """검증 통과 버전을 승인합니다. 주문이나 생산·선적 실적은 변경하지 않습니다."""
    with session.begin_nested():
        version = session.scalar(select(models.PsiVersion).where(models.PsiVersion.id == version_id, models.PsiVersion.project_id == context.project.id).with_for_update())
        if version is None:
            raise HTTPException(status_code=404, detail="PSI 버전을 찾을 수 없습니다.")
        if version.status == "승인":
            return {"version": _summary(_load_version(session, version_id, context.project.id)), "duplicate_request": True, "message": "이미 승인된 버전입니다. 중복 처리는 하지 않았습니다."}
        if version.status != "검토 대기" or version.validation_issues:
            raise HTTPException(status_code=409, detail="검증 오류가 없는 검토 대기 버전만 승인할 수 있습니다.")
        version.status = "승인"
        version.approved_at = datetime.now(timezone.utc)
        version.approved_by_user_id = context.user.id
    session.commit()
    return {"version": _summary(_load_version(session, version_id, context.project.id)), "duplicate_request": False, "message": "PSI 버전을 승인했습니다. Sales Master 주문은 변경하지 않았습니다."}


@router.post("/risk-reviews/{review_id}/decision")
def decide_risk_review(review_id: str, payload: RiskDecision, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """PSI 신호를 후보 주문에 승인하거나 보류합니다.

    모델 단위 신호를 임의 배분하지 않기 위해 승인에는 사용자가 선택한 후보 주문이
    반드시 필요합니다. 보류는 주문을 연결하지 않은 채 사유만 남길 수 있습니다.
    """
    if payload.decision not in {"승인", "보류"}:
        raise HTTPException(status_code=422, detail="승인 또는 보류를 선택해 주세요.")
    review = session.scalar(select(models.PsiRiskReview).where(models.PsiRiskReview.id == review_id, models.PsiRiskReview.project_id == context.project.id).with_for_update())
    if review is None:
        raise HTTPException(status_code=404, detail="RISK 검토 항목을 찾을 수 없습니다.")
    candidates = _risk_candidates(session, review)
    candidates = [order for order in candidates if order.production_company and order.production_company.name == review.production_company]
    candidate_ids = {order.id for order in candidates}
    requested = payload.allocations
    if payload.decision == "승인" and not requested and payload.order_id:
        requested = [RiskAllocationInput(order_id=payload.order_id, quantity=review.quantity)]
    if payload.decision == "승인":
        if not requested:
            raise HTTPException(status_code=409, detail="후보 주문과 배분수량을 한 건 이상 입력해야 승인할 수 있습니다.")
        order_ids = [item.order_id for item in requested]
        if len(order_ids) != len(set(order_ids)):
            raise HTTPException(status_code=409, detail="같은 주문을 두 번 배분할 수 없습니다.")
        if any(order_id not in candidate_ids for order_id in order_ids):
            raise HTTPException(status_code=409, detail="표시된 후보 주문에만 배분할 수 있습니다.")
        if sum(item.quantity for item in requested) != review.quantity:
            raise HTTPException(status_code=409, detail=f"배분수량 합계가 PSI 영향수량 {review.quantity}ea와 같아야 합니다.")
        candidate_map = {order.id: order for order in candidates}
        if any(item.quantity > candidate_map[item.order_id].quantity for item in requested):
            raise HTTPException(status_code=409, detail="배분수량은 해당 주문수량을 초과할 수 없습니다.")
    session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.review_id == review.id))
    if payload.decision == "승인":
        for item in requested:
            session.add(models.PsiRiskAllocation(id=str(uuid4()), review_id=review.id, order_id=item.order_id, quantity=item.quantity, created_by_user_id=context.user.id))
            order = next(order for order in candidates if order.id == item.order_id)
            session.add(models.ChangeEvent(
                id=str(uuid4()), order_id=order.id, source_id=review.version.source_id,
                event_type="PSI RISK 수량 배분", changed_at=datetime.now(timezone.utc),
                after_value=f"{review.risk_type} {item.quantity}ea 배분 · {review.period_key}",
            ))
    review.status = payload.decision
    review.order_id = requested[0].order_id if payload.decision == "승인" and len(requested) == 1 else None
    review.review_note = payload.note.strip() if payload.note and payload.note.strip() else None
    review.reviewed_by_user_id = context.user.id
    review.reviewed_at = datetime.now(timezone.utc)
    session.commit()
    return {"review": _review_summary(session, review), "message": "선택 주문의 Sales Master RISK에 반영했습니다." if payload.decision == "승인" else "신호를 보류했습니다. Sales Master에는 반영하지 않았습니다."}


@router.delete("/versions/{version_id}")
def remove_version(version_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """PSI 버전, 주차 값, RISK 근거와 원본을 한 트랜잭션으로 영구 삭제합니다."""
    try:
        result = delete_psi_version(session, context.project.id, version_id)
        session.commit()
    except UploadMaterialNotFound:
        session.rollback()
        raise HTTPException(status_code=404, detail="삭제할 PSI 버전을 찾을 수 없습니다.")
    except UploadMaterialOutOfScope as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error))
    except Exception:
        session.rollback()
        raise
    return {"deleted": True, "id": result.record_id, "source_id": result.source_id, "message": "PSI 원본과 파생 데이터를 영구 삭제했습니다."}


@router.get("/versions/{version_id}/download")
def download_version(version_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> Response:
    """저장한 원본 XLSX 바이트를 변경 없이 내려줍니다."""
    version = _load_version(session, version_id, context.project.id)
    return Response(content=version.file_content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(version.file_name)}"})


@router.get("/versions/{version_id}/export")
def export_version(version_id: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> Response:
    """선택한 저장 버전을 화면 구조의 XLSX로 다시 만듭니다."""
    version = _load_version(session, version_id, context.project.id)
    content = psi_workbook(values_to_psi(version.values))
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote('psi-version.xlsx')}"})


@router.get("/samples/{kind}")
def download_sample(kind: str, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> Response:
    """현재 프로젝트 합성 주문 기반의 비교용 기준/변경 PSI 샘플을 만듭니다."""
    if kind not in {"base", "revised"}:
        raise HTTPException(status_code=404, detail="PSI 샘플을 찾을 수 없습니다.")
    psi = build_psi(load_orders(session, context.project.id))
    if kind == "revised":
        psi = deepcopy(psi)
        # 첫 모델의 DM과 SP를 명시적으로 바꾸어 버전 비교와 RISK 신호를 확인할 수 있게 합니다.
        first_dm = next((row for row in psi["rows"] if row["metric"] == "DM"), None)
        first_sp = next((row for row in psi["rows"] if row["metric"] == "SP"), None)
        if first_dm and psi["periods"]:
            first_dm["values"][psi["periods"][0]["key"]] += 5
        if first_sp and len(psi["periods"]) > 1:
            first_sp["values"][psi["periods"][1]["key"]] += 2
    content = psi_workbook(psi)
    filename = "psi-sample-base.xlsx" if kind == "base" else "psi-sample-revised.xlsx"
    return Response(content=content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename={filename}"})
