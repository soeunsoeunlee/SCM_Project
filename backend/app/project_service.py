"""프로젝트 전체 삭제 범위를 한 곳에서 관리합니다.

프로젝트 삭제는 복구할 수 없는 작업이므로 이 함수는 반드시 API의 권한·이름 확인을
통과한 뒤 호출합니다. 모든 삭제는 호출한 데이터베이스 트랜잭션 안에서 실행되며,
중간에 하나라도 실패하면 라우터가 commit하지 않아 전체가 rollback됩니다.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from . import models


def project_summary(session: Session, project_id: str) -> dict[str, int]:
    """프로젝트 관리 목록에 보여 줄 주요 데이터 건수를 계산합니다."""
    return {
        "order_count": session.scalar(select(func.count()).select_from(models.Order).where(models.Order.project_id == project_id)) or 0,
        "upload_count": (
            (session.scalar(select(func.count()).select_from(models.UploadBatch).where(models.UploadBatch.project_id == project_id)) or 0)
            + (session.scalar(select(func.count()).select_from(models.UnstructuredInput).where(models.UnstructuredInput.project_id == project_id)) or 0)
        ),
        "psi_count": session.scalar(select(func.count()).select_from(models.PsiVersion).where(models.PsiVersion.project_id == project_id)) or 0,
    }


def _source_is_referenced(session: Session, source_id: str) -> bool:
    """다른 프로젝트가 같은 원본을 사용 중인지 확인합니다.

    프로젝트 사본의 주문은 원본 프로젝트와 같은 SourceMaterial을 가리킬 수 있으므로,
    참조가 하나라도 남아 있으면 원본 행을 삭제하지 않습니다.
    """
    source_models = (
        models.Order,
        models.OrderUpdateProposal,
        models.UploadBatch,
        models.PlanVersion,
        models.PsiVersion,
        models.ProductionAllocation,
        models.ShipmentAllocation,
        models.ErpConfirmation,
        models.ChangeEvent,
        models.UnstructuredInput,
    )
    return any(
        session.scalar(select(model).where(model.source_id == source_id).limit(1)) is not None
        for model in source_models
    )


def delete_project_data(session: Session, project_id: str) -> dict[str, int]:
    """프로젝트와 그 프로젝트에만 속한 업무 데이터를 영구 삭제합니다.

    삭제 순서는 외래키 관계의 자식에서 부모 방향입니다. 다른 프로젝트가 공유하는
    법인·사용자·SourceMaterial은 삭제하지 않습니다.
    """
    summary = project_summary(session, project_id)
    order_ids = list(session.scalars(select(models.Order.id).where(models.Order.project_id == project_id)))
    batch_ids = list(session.scalars(select(models.UploadBatch.id).where(models.UploadBatch.project_id == project_id)))
    raw_row_ids = list(session.scalars(select(models.UploadRawRow.id).where(models.UploadRawRow.batch_id.in_(batch_ids)))) if batch_ids else []
    psi_ids = list(session.scalars(select(models.PsiVersion.id).where(models.PsiVersion.project_id == project_id)))
    review_ids = list(session.scalars(select(models.PsiRiskReview.id).where(models.PsiRiskReview.project_id == project_id)))
    plan_ids = list(session.scalars(select(models.PlanVersion.id).where(models.PlanVersion.project_id == project_id)))

    source_ids = set(session.scalars(select(models.Order.source_id).where(models.Order.project_id == project_id)))
    source_ids.update(session.scalars(select(models.UploadBatch.source_id).where(models.UploadBatch.project_id == project_id)))
    source_ids.update(session.scalars(select(models.PlanVersion.source_id).where(models.PlanVersion.project_id == project_id)))
    source_ids.update(session.scalars(select(models.PsiVersion.source_id).where(models.PsiVersion.project_id == project_id)))
    source_ids.update(session.scalars(select(models.UnstructuredInput.source_id).where(models.UnstructuredInput.project_id == project_id)))
    source_ids.update(session.scalars(select(models.OrderUpdateProposal.source_id).where(models.OrderUpdateProposal.project_id == project_id)))
    if order_ids:
        source_ids.update(session.scalars(select(models.ProductionAllocation.source_id).where(models.ProductionAllocation.order_id.in_(order_ids))))
        source_ids.update(session.scalars(select(models.ShipmentAllocation.source_id).where(models.ShipmentAllocation.order_id.in_(order_ids))))
        source_ids.update(session.scalars(select(models.ErpConfirmation.source_id).where(models.ErpConfirmation.order_id.in_(order_ids), models.ErpConfirmation.source_id.is_not(None))))
        source_ids.update(session.scalars(select(models.ChangeEvent.source_id).where(models.ChangeEvent.order_id.in_(order_ids))))

    if review_ids:
        session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.review_id.in_(review_ids)))
    session.execute(delete(models.PsiRiskReview).where(models.PsiRiskReview.project_id == project_id))
    if psi_ids:
        session.execute(delete(models.PsiValue).where(models.PsiValue.version_id.in_(psi_ids)))
    session.execute(delete(models.PsiVersion).where(models.PsiVersion.project_id == project_id))

    if plan_ids:
        session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.plan_version_id.in_(plan_ids)))
    session.execute(delete(models.PlanVersion).where(models.PlanVersion.project_id == project_id))

    session.execute(delete(models.OrderUpdateProposal).where(models.OrderUpdateProposal.project_id == project_id))
    if order_ids:
        session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.order_id.in_(order_ids)))
        session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.order_id.in_(order_ids)))
        session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.order_id.in_(order_ids)))
        session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.order_id.in_(order_ids)))
        session.execute(delete(models.ProductionAllocation).where(models.ProductionAllocation.order_id.in_(order_ids)))
        session.execute(delete(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id.in_(order_ids)))
        session.execute(delete(models.OrderManualOverride).where(models.OrderManualOverride.order_id.in_(order_ids)))
        session.execute(delete(models.Order).where(models.Order.id.in_(order_ids)))

    if batch_ids:
        session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id.in_(batch_ids)))
        session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id.in_(batch_ids)))
        session.execute(delete(models.UploadBatch).where(models.UploadBatch.id.in_(batch_ids)))
    elif raw_row_ids:
        # 현재 구조에서는 도달하지 않지만, 삭제 범위가 바뀌어도 고아 행을 남기지 않습니다.
        session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.id.in_(raw_row_ids)))

    session.execute(delete(models.MappingProfile).where(models.MappingProfile.project_id == project_id))
    session.execute(delete(models.UnstructuredInput).where(models.UnstructuredInput.project_id == project_id))
    # 주문·업로드가 먼저 제거된 뒤 프로젝트 전용 법인을 정리합니다. 기본 법인은 project_id가 없어 보존됩니다.
    session.execute(delete(models.Company).where(models.Company.project_id == project_id))
    session.execute(delete(models.ProjectMember).where(models.ProjectMember.project_id == project_id))

    # 사본 프로젝트는 삭제하지 않고, 삭제된 원본 프로젝트를 가리키는 표지만 해제합니다.
    session.execute(update(models.Project).where(models.Project.copied_from_id == project_id).values(copied_from_id=None))
    session.execute(delete(models.Project).where(models.Project.id == project_id))
    session.flush()

    removed_sources = 0
    for source_id in source_ids:
        if source_id is None:
            continue
        if not _source_is_referenced(session, source_id):
            source = session.get(models.SourceMaterial, source_id)
            if source is not None:
                session.delete(source)
                removed_sources += 1
    session.flush()
    return {**summary, "source_count": removed_sources}
