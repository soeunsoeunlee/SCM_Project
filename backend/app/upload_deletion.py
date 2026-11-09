"""업로드 자료의 영구 삭제와 원본 연결 정리를 한 곳에서 관리합니다.

판매계획은 그 파일이 만든 Sales Master 주문과 주문 종속 자료까지 함께 영구 삭제합니다.
생산 자동 매칭은 기존 ``ProductionAllocation``을 사용합니다. 이후 선적 자동 매칭처럼
새 연결 모델을 만들 때는 ``SOURCE_DERIVED_DELETE_MODELS``에 추가합니다.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from . import models


DELETABLE_DATA_TYPES = {"판매계획", "생산정보", "선적정보"}

# source_id 하나에 전적으로 종속되어 원본 삭제 시 함께 없어져야 하는 연결입니다.
# 이후 선적 등 새 자동 매칭 연결 모델도 source_id를 갖게 하고 이 목록에 추가합니다.
SOURCE_DERIVED_DELETE_MODELS = (
    models.ProductionAllocation,
    models.ShipmentAllocation,
    models.OrderUpdateProposal,
    models.ChangeEvent,
)


class UploadMaterialNotFound(Exception):
    """현재 프로젝트에서 삭제 대상을 찾지 못했습니다."""


class UploadMaterialOutOfScope(Exception):
    """현재 삭제 규칙으로 안전하게 처리할 수 없는 자료입니다."""


@dataclass(frozen=True)
class DeleteResult:
    """API가 사용자에게 알려 줄 삭제 결과입니다."""

    record_id: str
    file_name: str
    data_type: str
    source_id: str
    detached_erp_references: int
    deleted_source_links: int
    deleted_order_count: int = 0


def _delete_source_if_unreferenced(session: Session, source_id: str) -> None:
    """사본 등 다른 업무 데이터가 같은 출처 메타데이터를 사용하지 않을 때만 지웁니다."""
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
    referenced = any(
        session.scalar(select(model).where(model.source_id == source_id).limit(1)) is not None
        for model in source_models
    )
    if not referenced:
        session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == source_id))


def clear_sales_plan_batch(session: Session, batch: models.UploadBatch) -> tuple[int, int, int]:
    """판매계획 파일이 만든 주문과 그 주문의 모든 종속 업무 데이터를 삭제합니다."""
    result_ids = select(models.StandardizedResult.id).where(models.StandardizedResult.batch_id == batch.id)
    order_ids = list(session.scalars(
        select(models.Order.id).where(
            models.Order.project_id == batch.project_id,
            (models.Order.upload_batch_id == batch.id) | (models.Order.upload_result_id.in_(result_ids)),
        )
    ))
    deleted_orders = len(order_ids)
    dependent_source_ids: set[str] = {batch.source_id}
    if order_ids:
        dependent_source_ids.update(session.scalars(select(models.Order.source_id).where(models.Order.id.in_(order_ids))))
        for model in (models.ProductionAllocation, models.ShipmentAllocation, models.ChangeEvent):
            dependent_source_ids.update(session.scalars(select(model.source_id).where(model.order_id.in_(order_ids))))
        dependent_source_ids.update(session.scalars(
            select(models.ErpConfirmation.source_id).where(
                models.ErpConfirmation.order_id.in_(order_ids),
                models.ErpConfirmation.source_id.is_not(None),
            )
        ))

        # PSI 신호 자체는 PSI 원본에 속하므로 남기되, 삭제될 주문과의 승인·배분 연결은 초기화합니다.
        affected_review_ids = list(session.scalars(select(models.PsiRiskReview.id).where(models.PsiRiskReview.order_id.in_(order_ids))))
        if affected_review_ids:
            session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.review_id.in_(affected_review_ids)))
            session.execute(
                update(models.PsiRiskReview)
                .where(models.PsiRiskReview.id.in_(affected_review_ids))
                .values(order_id=None, status="검토 대기", review_note=None, reviewed_by_user_id=None, reviewed_at=None)
            )
        session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.order_id.in_(order_ids)))
        session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.order_id.in_(order_ids)))
        session.execute(delete(models.OrderUpdateProposal).where(models.OrderUpdateProposal.order_id.in_(order_ids)))
        session.execute(delete(models.OrderManualOverride).where(models.OrderManualOverride.order_id.in_(order_ids)))
        session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.order_id.in_(order_ids)))
        session.execute(delete(models.ProductionAllocation).where(models.ProductionAllocation.order_id.in_(order_ids)))
        session.execute(delete(models.ShipmentAllocation).where(models.ShipmentAllocation.order_id.in_(order_ids)))
        session.execute(delete(models.ErpConfirmation).where(models.ErpConfirmation.order_id.in_(order_ids)))
        session.execute(delete(models.Order).where(models.Order.id.in_(order_ids)))

    # 아직 주문에 반영되지 않은 후속 파일 변경 제안은 해당 파일의 파생물이므로 제거합니다.
    proposal_result = session.execute(
        delete(models.OrderUpdateProposal).where(
            models.OrderUpdateProposal.project_id == batch.project_id,
            (models.OrderUpdateProposal.batch_id == batch.id) | (models.OrderUpdateProposal.source_id == batch.source_id),
        )
    )
    plan_ids = list(session.scalars(
        select(models.PlanVersion.id).where(
            models.PlanVersion.project_id == batch.project_id,
            models.PlanVersion.source_id == batch.source_id,
        )
    ))
    if plan_ids:
        session.execute(delete(models.PlanVersionLine).where(models.PlanVersionLine.plan_version_id.in_(plan_ids)))
        session.execute(delete(models.PlanVersion).where(models.PlanVersion.id.in_(plan_ids)))
    session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id == batch.id))
    session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id == batch.id))
    session.flush()
    # ERP 수동 확인 등 주문과 함께 사라진 별도 원본도 다른 참조가 없을 때 정리합니다.
    for source_id in dependent_source_ids - {batch.source_id}:
        if source_id:
            _delete_source_if_unreferenced(session, source_id)
    return proposal_result.rowcount or 0, 0, deleted_orders


def delete_source_dependents(session: Session, source_id: str) -> tuple[int, int]:
    """원본에 종속된 연결은 삭제하고 ERP 확정 기록은 출처만 끊습니다.

    ERP Closed 자체는 보존해야 하므로 ErpConfirmation 행을 삭제하지 않습니다.
    주문이나 판매계획의 직접 원본이면 안전을 위해 삭제를 거부합니다.
    """
    if session.scalar(select(models.Order.id).where(models.Order.source_id == source_id).limit(1)) is not None:
        raise UploadMaterialOutOfScope("Sales Master 주문의 직접 원본은 이번 범위에서 삭제할 수 없습니다.")
    if session.scalar(select(models.PlanVersion.id).where(models.PlanVersion.source_id == source_id).limit(1)) is not None:
        raise UploadMaterialOutOfScope("판매계획 버전의 원본은 이번 범위에서 삭제할 수 없습니다.")

    production_order_ids = list(session.scalars(select(models.ProductionAllocation.order_id).where(
        models.ProductionAllocation.source_id == source_id,
        models.ProductionAllocation.is_manual.is_(False),
    )))
    deleted_links = 0
    for model in SOURCE_DERIVED_DELETE_MODELS:
        result = session.execute(delete(model).where(model.source_id == source_id))
        deleted_links += result.rowcount or 0
    session.flush()
    # 생산법인이 이 원본의 자동 매칭으로만 정해졌다면 출처 삭제 후에도 근거 없는 값이
    # 남지 않게 비웁니다. 사용자가 수동 저장한 값과 다른 생산 원본의 연결은 보존합니다.
    for order_id in set(production_order_ids):
        order = session.get(models.Order, order_id)
        if order is None:
            continue
        remaining = session.scalar(select(models.ProductionAllocation.id).where(
            models.ProductionAllocation.order_id == order_id,
        ).limit(1))
        protected = set(order.manual_override.protected_fields if order.manual_override else [])
        if remaining is None and "production_company_id" not in protected:
            order.production_company_id = None
            order.production_site_source = None

    # ERP Closed 값과 수량은 그대로 두고 삭제된 원본을 가리키는 연결만 NULL로 바꿉니다.
    detached = session.execute(
        update(models.ErpConfirmation)
        .where(models.ErpConfirmation.source_id == source_id)
        .values(source_id=None)
    ).rowcount or 0
    return deleted_links, detached


def clear_psi_derivatives(session: Session, version_id: str, source_id: str) -> None:
    """PSI 값·RISK 검토·배분·Sales Master RISK 근거를 모두 제거합니다."""
    review_ids = select(models.PsiRiskReview.id).where(models.PsiRiskReview.version_id == version_id)
    session.execute(delete(models.PsiRiskAllocation).where(models.PsiRiskAllocation.review_id.in_(review_ids)))
    session.execute(delete(models.ChangeEvent).where(models.ChangeEvent.source_id == source_id))
    session.execute(delete(models.PsiRiskReview).where(models.PsiRiskReview.version_id == version_id))
    session.execute(delete(models.PsiValue).where(models.PsiValue.version_id == version_id))


def clear_upload_batch_derivatives(session: Session, batch: models.UploadBatch) -> tuple[int, int]:
    """배치의 원본 행·표준화 결과와 생산·선적 연결을 제거합니다."""
    deleted_links, detached = delete_source_dependents(session, batch.source_id)
    session.execute(delete(models.StandardizedResult).where(models.StandardizedResult.batch_id == batch.id))
    session.execute(delete(models.UploadRawRow).where(models.UploadRawRow.batch_id == batch.id))
    return deleted_links, detached


def delete_psi_version(session: Session, project_id: str, version_id: str) -> DeleteResult:
    """PSI 한 버전과 파생값 및 원본을 영구 삭제합니다."""
    version = session.scalar(
        select(models.PsiVersion)
        .where(models.PsiVersion.id == version_id, models.PsiVersion.project_id == project_id)
        .with_for_update()
    )
    if version is None:
        raise UploadMaterialNotFound
    clear_psi_derivatives(session, version.id, version.source_id)
    deleted_links, detached = delete_source_dependents(session, version.source_id)
    result = DeleteResult(version.id, version.file_name, "PSI", version.source_id, detached, deleted_links)
    session.execute(delete(models.PsiVersion).where(models.PsiVersion.id == version.id))
    session.execute(delete(models.SourceMaterial).where(models.SourceMaterial.id == version.source_id))
    return result


def delete_upload_material(session: Session, project_id: str, record_kind: str, record_id: str) -> DeleteResult:
    """판매계획·생산·선적의 구조화 배치 또는 비정형 입력을 영구 삭제합니다."""
    if record_kind == "batch":
        record = session.scalar(
            select(models.UploadBatch)
            .where(models.UploadBatch.id == record_id, models.UploadBatch.project_id == project_id)
            .with_for_update()
        )
        if record is None:
            raise UploadMaterialNotFound
        if record.data_type not in DELETABLE_DATA_TYPES:
            raise UploadMaterialOutOfScope("삭제할 수 없는 자료 유형입니다.")
        if record.data_type == "판매계획":
            deleted_links, detached, deleted_orders = clear_sales_plan_batch(session, record)
        else:
            deleted_links, detached = clear_upload_batch_derivatives(session, record)
            deleted_orders = 0
        result = DeleteResult(record.id, record.file_name, record.data_type, record.source_id, detached, deleted_links, deleted_orders)
        session.execute(delete(models.UploadBatch).where(models.UploadBatch.id == record.id))
    elif record_kind == "unstructured":
        record = session.scalar(
            select(models.UnstructuredInput)
            .where(models.UnstructuredInput.id == record_id, models.UnstructuredInput.project_id == project_id)
            .with_for_update()
        )
        if record is None:
            raise UploadMaterialNotFound
        if record.data_type not in DELETABLE_DATA_TYPES:
            raise UploadMaterialOutOfScope("삭제할 수 없는 자료 유형입니다.")
        deleted_links, detached = delete_source_dependents(session, record.source_id)
        result = DeleteResult(record.id, record.file_name or "붙여넣기 텍스트", record.data_type, record.source_id, detached, deleted_links)
        session.execute(delete(models.UnstructuredInput).where(models.UnstructuredInput.id == record.id))
    else:
        raise UploadMaterialNotFound

    _delete_source_if_unreferenced(session, result.source_id)
    return result
