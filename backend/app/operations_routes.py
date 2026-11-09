"""Closed(ERP 확인), 변경 이력, 연동 준비 상태를 제공하는 업무 API입니다."""

from datetime import date, datetime, timezone
from io import BytesIO
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import AuthContext, require_context
from .ai_mapping import is_openai_configured
from .database import get_session


router = APIRouter(prefix="/api", tags=["operations"])


class ErpConfirmationInput(BaseModel):
    """사용자가 원본 ERP 화면을 확인한 뒤 입력하는 매출화 확인값입니다."""

    status: str
    confirmed_quantity: int = Field(ge=0)
    confirmation_date: date | None = None
    note: str | None = Field(default=None, max_length=300)


def _project_order(session: Session, project_id: str, order_id: str) -> models.Order:
    order = session.scalar(select(models.Order).where(models.Order.id == order_id, models.Order.project_id == project_id))
    if order is None:
        raise HTTPException(status_code=404, detail="현재 프로젝트에서 주문을 찾을 수 없습니다.")
    return order


@router.post("/orders/{order_id}/erp-confirmations", status_code=status.HTTP_201_CREATED)
def create_erp_confirmation(order_id: str, payload: ErpConfirmationInput, context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> dict:
    """ERP에서 직접 확인한 Closed 수량을 주문에 추가하고 변경 이력을 남깁니다."""
    allowed = {"미확인", "일부 완료", "전체 완료", "취소·정정"}
    if payload.status not in allowed:
        raise HTTPException(status_code=422, detail="ERP 상태는 미확인, 일부 완료, 전체 완료, 취소·정정 중 하나여야 합니다.")
    order = _project_order(session, context.project.id, order_id)
    previous_status = order.erp_status
    current = sum(item.confirmed_quantity for item in order.erp_confirmations if item.status != "취소·정정")
    if payload.status == "취소·정정" and payload.confirmed_quantity != 0:
        raise HTTPException(status_code=409, detail="취소·정정 기록의 확인수량은 0이어야 합니다.")
    next_total = current + (payload.confirmed_quantity if payload.status != "취소·정정" else 0)
    if next_total > order.quantity:
        raise HTTPException(status_code=409, detail=f"누적 Closed 수량은 주문수량 {order.quantity}ea를 초과할 수 없습니다.")
    if payload.status == "전체 완료" and next_total != order.quantity:
        raise HTTPException(status_code=409, detail=f"전체 완료는 누적 Closed 수량이 주문수량 {order.quantity}ea일 때만 선택할 수 있습니다.")

    source_id = str(uuid4())
    confirmation_id = str(uuid4())
    now = datetime.now(timezone.utc)
    session.add(models.SourceMaterial(
        id=source_id, name="ERP 수동 확인", description=payload.note or "사용자가 ERP에서 확인한 Closed 기록",
        received_at=now.isoformat(), source_hash=f"manual-erp-{confirmation_id}",
    ))
    # SourceMaterial과 ChangeEvent 사이에 ORM relationship이 없으므로 먼저 원본 행을 확정해
    # 같은 트랜잭션의 뒤 INSERT가 외래키 순서 때문에 실패하지 않게 합니다.
    session.flush()
    session.add(models.ErpConfirmation(
        id=confirmation_id, order_id=order.id, status=payload.status,
        confirmed_quantity=payload.confirmed_quantity, confirmation_date=payload.confirmation_date, source_id=source_id,
    ))
    order.erp_status = "전체 완료" if next_total == order.quantity else "일부 완료" if next_total > 0 else payload.status
    session.add(models.ChangeEvent(
        id=str(uuid4()), order_id=order.id, source_id=source_id, event_type="ERP Closed 확인",
        changed_at=now, before_value=f"{current}ea · {previous_status}",
        after_value=f"{next_total}ea · {payload.status}" + (f" · {payload.note.strip()}" if payload.note and payload.note.strip() else ""),
    ))
    session.commit()
    return {"id": confirmation_id, "order_id": order.id, "status": order.erp_status, "closed_quantity": next_total, "message": "ERP Closed 확인을 저장했습니다."}


@router.get("/history")
def list_history(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> list[dict]:
    """현재 프로젝트 주문의 변경 이력을 최근 순서로 반환합니다."""
    rows = session.execute(
        select(models.ChangeEvent, models.Order, models.SourceMaterial)
        .join(models.Order, models.ChangeEvent.order_id == models.Order.id)
        .join(models.SourceMaterial, models.ChangeEvent.source_id == models.SourceMaterial.id)
        .where(models.Order.project_id == context.project.id)
        .order_by(models.ChangeEvent.changed_at.desc())
    ).all()
    return [{
        "id": event.id, "changed_at": event.changed_at.isoformat(), "event_type": event.event_type,
        "order_id": order.id, "order_number": order.order_number, "po_number": order.po_number,
        "model": order.model, "before_value": event.before_value, "after_value": event.after_value,
        "source_name": source.name,
    } for event, order, source in rows]


@router.get("/history.xlsx")
def download_history(context: AuthContext = Depends(require_context), session: Session = Depends(get_session)) -> Response:
    """화면의 변경 이력 열과 같은 순서로 Excel 파일을 만듭니다."""
    rows = list_history(context, session)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "변경 이력"
    sheet.append(["처리 시각", "구분", "SO", "PO", "모델", "변경 전", "변경 후", "자료 출처"])
    for row in rows:
        sheet.append([row["changed_at"], row["event_type"], row["order_number"], row["po_number"], row["model"], row["before_value"], row["after_value"], row["source_name"]])
    for width, column in zip((23, 20, 18, 18, 22, 30, 45, 25), sheet.columns):
        sheet.column_dimensions[column[0].column_letter].width = width
    stream = BytesIO()
    workbook.save(stream)
    filename = quote("SCM_변경이력.xlsx")
    return Response(stream.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.get("/integration-status")
def integration_status(context: AuthContext = Depends(require_context)) -> dict:
    """비밀정보를 노출하지 않고 현재 프로젝트의 연동 준비 상태만 알려 줍니다."""
    ai_ready = is_openai_configured()
    return {
        "project_id": context.project.id,
        "connectors": [
            {"name": "판매자료 파일", "status": "사용 가능", "detail": "XLSX, XLS, CSV와 수동 매핑"},
            {
                "name": "OpenAI 매칭 보조",
                "status": "사용 가능" if ai_ready else "연결 안 됨",
                "detail": (
                    "미해결 컬럼 헤더와 최소 표준 식별정보로 판매·생산·선적 후보를 추천합니다. AI 후보는 검토 상태이며 수량·중복 규칙을 서버가 다시 확인합니다."
                    if ai_ready else
                    "프로젝트 루트 .env에 OPENAI_API_KEY를 설정하고 서버를 다시 시작하면 컬럼·주문 후보 추천을 사용합니다."
                ),
            },
            {"name": "메일·이미지·텍스트", "status": "사용 가능" if ai_ready else "AI 연결 안 됨", "detail": "OpenAI로 메일·줄글·이미지를 구조화하고 원본·근거·신뢰도를 검토용으로 저장합니다. Sales Master에는 자동 반영하지 않습니다." if ai_ready else ".env의 OpenAI 키를 연결하면 메일·줄글·이미지 구조화 추출을 사용합니다. 현재는 규칙/OCR 결과만 저장합니다."},
            {"name": "Gmail", "status": "연결 안 됨", "detail": "OAuth 연결 전에는 메일을 읽지 않음"},
            {"name": "ERP", "status": "수동 확인", "detail": "Sales Master에서 Closed 수량을 직접 확인 등록"},
        ],
        "security": ["비밀번호와 API 키를 소스·로그·브라우저에 저장하지 않음", "표 파일 매칭은 헤더와 최소 표준 식별정보만 전송", "사용자가 비정형 입력에 등록한 메일 본문·줄글 또는 이미지는 구조화 추출을 위해 OpenAI로 전송", "AI 추출만으로 주문·수량 배분을 자동 확정하지 않음"],
    }
