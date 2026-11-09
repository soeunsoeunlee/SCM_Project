"""SCM 프로젝트에서 사용하는 데이터베이스 표(테이블)를 정의합니다.

비전공자를 위한 읽는 방법
------------------------
* ``class Order(Base)``: 주문이라는 데이터 표 하나를 정의합니다.
* ``__tablename__``: PostgreSQL 안에서 사용하는 실제 표 이름입니다.
* ``Mapped[str]``: Python에서 이 값이 문자열이라는 뜻입니다.
* ``mapped_column(...)``: 실제 표의 한 칸(컬럼)을 만듭니다.
* ``primary_key=True``: 각 행을 유일하게 구분하는 대표 ID입니다.
* ``nullable=False``: 반드시 값이 있어야 합니다.
* ``nullable=True``: 아직 모르는 값이면 비워 둘 수 있습니다.
* ``ForeignKey("orders.id")``: 다른 표의 ID를 가리켜 두 표를 연결합니다.
* ``relationship(...)``: 연결된 데이터를 Python 코드에서 편하게 읽게 합니다.
* ``UniqueConstraint``: 같은 값의 중복 저장을 DB 차원에서 막습니다.

이 파일은 표의 모양만 정의합니다. 업로드·검증·저장 업무 규칙은
``upload_routes.py``와 ``upload_service.py`` 등에 있습니다.
"""

from __future__ import annotations

# 금액을 float로 저장하면 소수점 오차가 날 수 있어 Decimal을 사용합니다.
from datetime import date
from decimal import Decimal

# DB 컬럼의 자료형과 제약조건을 표현하는 SQLAlchemy 도구입니다.
from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, JSON, LargeBinary, Numeric, String, Text, UniqueConstraint, func
# Mapped/mapped_column은 컬럼을, relationship은 표 사이의 연결을 표현합니다.
from sqlalchemy.orm import Mapped, mapped_column, relationship

# 모든 모델이 공통으로 상속하는 프로젝트 기본 클래스입니다.
from .database import Base


# =============================================================================
# 1. 기본 기준정보: 법인, 사용자, 프로젝트, 로그인
# =============================================================================

class Company(Base):
    """판매법인 또는 생산법인의 기준정보입니다."""

    __tablename__ = "companies"  # 실제 DB 표 이름

    # 판매법인과 생산법인에 같은 지역명(예: 이집트)이 있을 수 있습니다.
    __table_args__ = (UniqueConstraint("project_id", "kind", "name", name="uq_company_project_kind_name"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 내부 법인 ID. 예: sales-a, plant-b
    name: Mapped[str] = mapped_column(String(120), nullable=False)  # 화면에 표시할 법인명
    kind: Mapped[str] = mapped_column(String(24), nullable=False)  # '판매법인' 또는 '생산법인'
    country: Mapped[str] = mapped_column(String(80), nullable=False)  # 법인이 있는 국가
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)  # 비어 있으면 기본 법인, 값이 있으면 해당 프로젝트에서 직접 추가


class User(Base):
    """사이트에 로그인하는 사용자 계정입니다."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 이메일이 바뀌어도 유지되는 사용자 내부 ID
    email: Mapped[str] = mapped_column(String(240), unique=True, nullable=False)  # 로그인 이메일. 계정마다 달라야 함
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)  # 화면에 표시할 사용자 이름
    password_hash: Mapped[str] = mapped_column(String(300), nullable=False)  # 비밀번호 원문이 아닌 scrypt 해시
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())  # 계정 생성 시각


class Project(Base):
    """업무 데이터를 서로 섞이지 않게 나누는 프로젝트(작업 공간)입니다."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 프로젝트 내부 ID
    name: Mapped[str] = mapped_column(String(160), nullable=False)  # 사용자가 보는 프로젝트 이름
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 프로젝트 소유자. 최초 계정 생성 전에는 비어 있을 수 있음
    copied_from_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True)  # 사본이라면 원본 프로젝트 ID
    as_of_date: Mapped[Date] = mapped_column(Date, nullable=False, default=date.today)  # 생산·선적 상태를 판단하는 프로젝트 기준 날짜
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())  # 프로젝트 생성 시각


class ProjectMember(Base):
    """어떤 사용자가 어떤 프로젝트에 참여하는지 연결합니다.

    사용자 한 명이 여러 프로젝트에 참여할 수 있고, 프로젝트 하나도 여러 사용자가
    함께 사용할 수 있어 중간 연결 표가 필요합니다.
    """

    __tablename__ = "project_members"
    # 같은 사용자를 같은 프로젝트에 두 번 등록하지 못하게 합니다.
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_project_member"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)  # DB가 자동으로 올리는 연결 번호
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)  # 참여 사용자 ID
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False)  # 참여 프로젝트 ID
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="owner")  # 프로젝트 안의 역할. 현재 기본값은 owner


class LoginSession(Base):
    """로그인 상태를 일정 기간 유지하기 위한 세션입니다."""

    __tablename__ = "login_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 세션 내부 ID
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)  # 로그인한 사용자 ID
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)  # 쿠키 토큰 원문이 아닌 SHA-256 해시
    expires_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)  # 이 시각 이후에는 다시 로그인해야 함
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())  # 세션 생성 시각


# =============================================================================
# 2. 원본 자료와 Sales Master 주문
# =============================================================================

class SourceMaterial(Base):
    """주문·생산·선적 정보가 어디에서 왔는지 나타내는 원본 자료입니다.

    원본 파일 자체는 UploadBatch 또는 UnstructuredInput에 보존하고, 업무 데이터는
    source_id로 이 표를 가리켜 출처를 추적합니다.
    """

    __tablename__ = "source_materials"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 원본 자료 내부 ID
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # 원본 파일명 또는 '붙여넣기 텍스트'
    description: Mapped[str] = mapped_column(String(300), nullable=False)  # 자료 종류와 적용 범위 설명
    received_at: Mapped[str] = mapped_column(String(40), nullable=False)  # 자료 수신 시각을 ISO 문자열로 저장
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)  # 동일 원본 중복 방지용 해시


class Order(Base):
    """Sales Master의 중심이 되는 표준 주문입니다.

    주문 수량은 여기에서 한 번만 집계합니다. 생산·선적 상세는 별도 표에 여러 건으로
    저장하므로 상세를 펼쳐도 주문 수량과 금액을 중복 합산하면 안 됩니다.
    """

    __tablename__ = "orders"
    # 한 프로젝트 안에서는 같은 SO 번호를 중복 저장할 수 없습니다.
    # 원본 프로젝트와 사본 프로젝트에서는 같은 SO 번호를 사용할 수 있습니다.
    __table_args__ = (UniqueConstraint("project_id", "order_number", name="uq_project_order_number"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 엑셀 행 번호와 무관한 안정된 주문 내부 ID
    order_number: Mapped[str] = mapped_column(String(40), nullable=False)  # 화면에 표시하는 SO 번호
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, default="project-demo", index=True)  # 주문이 속한 프로젝트
    sales_company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)  # 주문을 전달한 판매법인 ID
    production_company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), nullable=True)  # 생산정보 자동 매칭 전에는 비울 수 있는 생산법인 ID
    target_month: Mapped[str] = mapped_column(String(7), nullable=False)  # 대상 매출 월. 예: 2026-11
    po_number: Mapped[str] = mapped_column(String(80), nullable=False)  # 고객 PO. 앞자리 0 보존을 위해 문자열 사용
    po_line_number: Mapped[str | None] = mapped_column(String(80), nullable=True)  # PO 세부 라인. 원본에 없으면 비움
    model: Mapped[str] = mapped_column(String(100), nullable=False)  # 모델명. .AUS 같은 접미사까지 보존
    customer: Mapped[str] = mapped_column(String(120), nullable=False)  # 고객명
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # 주문 수량(ea). 생산·선적 수량과 별도 보존
    currency: Mapped[str] = mapped_column(String(3), nullable=False)  # ISO 3자리 통화 코드. 예: USD, EUR
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)  # 대표 단가. 소수점 4자리까지 정확히 저장
    net_sales_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # 계산된 Net Sales 총액
    gross_sales_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # 계산된 Gross Sales 총액
    net_sales_type: Mapped[str | None] = mapped_column(String(12), nullable=True)  # 원본 Net 값이 '단가'인지 '총액'인지
    gross_sales_type: Mapped[str | None] = mapped_column(String(12), nullable=True)  # 원본 Gross 값이 '단가'인지 '총액'인지
    production_site_source: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 원본에 적힌 생산사이트 문자열
    sales_region: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 원본에 적힌 판매지역
    upload_batch_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)  # 주문을 만든 엑셀 업로드 배치 ID
    upload_result_id: Mapped[int | None] = mapped_column(Integer, nullable=True, unique=True)  # 주문을 만든 표준화 결과 ID. 중복 반영 방지
    erp_status: Mapped[str] = mapped_column(String(40), nullable=False, default="미확인")  # ERP 매출화 상태. 선적 상태와 별도 관리
    match_status: Mapped[str] = mapped_column(String(40), nullable=False, default="연결 대기")  # 생산·선적 자료와 주문의 연결 상태
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False)  # 주문 값의 근거가 된 원본 ID
    created_at: Mapped[str] = mapped_column(String(40), nullable=False, server_default=func.current_timestamp())  # 주문 생성 시각

    # 아래 relationship은 새 DB 컬럼이 아니라 연결된 객체를 Python에서 편하게 읽는 통로입니다.
    sales_company: Mapped[Company] = relationship(foreign_keys=[sales_company_id])  # order.sales_company로 판매법인 조회
    production_company: Mapped[Company | None] = relationship(foreign_keys=[production_company_id])  # 생산정보가 안전하게 매칭된 뒤 조회되는 생산법인
    source: Mapped[SourceMaterial] = relationship()  # 원본 출처 조회
    production_details: Mapped[list["ProductionAllocation"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="ProductionAllocation.id")  # 여러 생산 상세
    shipment_details: Mapped[list["ShipmentAllocation"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="ShipmentAllocation.id")  # 여러 선적 상세
    erp_confirmations: Mapped[list["ErpConfirmation"]] = relationship(back_populates="order", cascade="all, delete-orphan", order_by="ErpConfirmation.id")  # ERP 매출 완료 수량
    psi_risk_reviews: Mapped[list["PsiRiskReview"]] = relationship(back_populates="order")  # 사용자가 주문에 승인한 PSI RISK 근거
    psi_risk_allocations: Mapped[list["PsiRiskAllocation"]] = relationship(back_populates="order", cascade="all, delete-orphan")  # PSI RISK를 이 주문에 배분한 수량
    manual_override: Mapped["OrderManualOverride | None"] = relationship(back_populates="order", cascade="all, delete-orphan", uselist=False)  # 수동 수정으로 자동 덮어쓰기를 막는 필드 목록
    update_proposals: Mapped[list["OrderUpdateProposal"]] = relationship(back_populates="order", cascade="all, delete-orphan")  # 새 파일과 현재 주문의 변경 비교 후보


class OrderManualOverride(Base):
    """사용자가 직접 저장해 새 파일보다 우선해야 하는 주문 필드를 기록합니다."""

    __tablename__ = "order_manual_overrides"

    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), primary_key=True)  # 보호 대상 주문
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)  # 프로젝트 경계
    protected_fields: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 자동으로 덮어쓰면 안 되는 필드명
    updated_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)  # 마지막 수동 수정자
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())  # 마지막 저장 시각

    order: Mapped[Order] = relationship(back_populates="manual_override")
    updated_by: Mapped[User] = relationship()


class OrderUpdateProposal(Base):
    """후속 파일 값과 수동 우선 주문 값의 차이를 사용자가 선택하기 전까지 보류합니다."""

    __tablename__ = "order_update_proposals"
    __table_args__ = (UniqueConstraint("batch_id", "upload_result_id", "order_id", name="uq_order_update_proposal_source"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    batch_id: Mapped[str | None] = mapped_column(ForeignKey("upload_batches.id"), nullable=True, index=True)
    upload_result_id: Mapped[int | None] = mapped_column(ForeignKey("standardized_results.id"), nullable=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False)
    incoming_values: Mapped[dict] = mapped_column(JSON, nullable=False)  # 새 파일에서 읽은 값
    differences: Mapped[dict] = mapped_column(JSON, nullable=False)  # 현재값·새값·수동 우선 여부
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="검토 대기")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    order: Mapped[Order] = relationship(back_populates="update_proposals")


# =============================================================================
# 3. 판매자료 업로드, 원본 행, 표준화 결과, 매핑 재사용
# =============================================================================

class UploadBatch(Base):
    """사용자가 한 번 업로드한 엑셀/CSV 파일과 처리 설정을 묶어 보존합니다."""

    __tablename__ = "upload_batches"
    # 같은 프로젝트·자료유형에서 내용이 같은 파일을 중복 등록하지 못하게 합니다.
    __table_args__ = (UniqueConstraint("project_id", "data_type", "file_hash", name="uq_project_type_upload_hash"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 업로드 배치 내부 ID
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False, unique=True)  # 공통 원본 자료 ID
    file_name: Mapped[str] = mapped_column(String(240), nullable=False)  # 사용자가 올린 원래 파일명
    file_extension: Mapped[str] = mapped_column(String(10), nullable=False)  # .xlsx, .xls, .csv 등 확장자
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # 동일 파일 판정용 SHA-256 해시
    data_type: Mapped[str] = mapped_column(String(30), nullable=False, default="판매계획")  # 판매계획, 생산정보, 선적정보
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, default="project-demo", index=True)  # 소속 프로젝트
    file_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 재다운로드 가능한 원본 파일 바이트
    sales_company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), nullable=True)  # 판매계획의 판매법인. 복수 법인이 섞인 생산·선적정보는 비워 둠
    production_company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), nullable=True)  # 생산·선적 파일을 보낸 생산법인
    target_month: Mapped[str] = mapped_column(String(7), nullable=False)  # 자료가 적용될 대상 매출 월
    reference_date: Mapped[Date] = mapped_column(Date, nullable=False)  # 원본 업무 기준일. 업로드 날짜와 다를 수 있음
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)  # '전체 현황' 또는 '변경분'
    scope_description: Mapped[str] = mapped_column(String(300), nullable=False)  # 포함 지역·사이트 같은 적용 범위 설명
    selected_sheet: Mapped[str | None] = mapped_column(String(160), nullable=True)  # 사용자가 가져오기로 선택한 시트
    header_row: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 선택한 헤더 행 번호
    header_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 원본 헤더 구성을 식별하는 해시
    mapping_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # 표준 항목과 원본 컬럼 연결 설정
    pricing_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Net/Gross 단가·총액 선택 설정
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="업로드 완료")  # 업로드·표준화·반영·비교 대기 상태
    application_mode: Mapped[str | None] = mapped_column(String(40), nullable=True)  # 최초 반영 또는 버전 비교 대기
    result_message: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 사용자에게 보여 줄 처리 결과
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())  # 원본 저장 시각
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 이번 파일을 등록하거나 덮어쓴 사용자
    applied_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # 최종 반영 시각

    source: Mapped[SourceMaterial] = relationship()  # 공통 원본 기본정보
    sales_company: Mapped[Company | None] = relationship(foreign_keys=[sales_company_id])  # 생산·선적정보는 여러 판매법인을 포함할 수 있어 연결값이 없을 수 있음
    production_company: Mapped[Company | None] = relationship(foreign_keys=[production_company_id])  # 생산·선적 파일의 생산법인
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])  # 업로드 이력에 표시할 등록자
    raw_rows: Mapped[list["UploadRawRow"]] = relationship(back_populates="batch", cascade="all, delete-orphan")  # 파일에서 읽은 모든 원본 행
    standardized_results: Mapped[list["StandardizedResult"]] = relationship(back_populates="batch", cascade="all, delete-orphan")  # 표준화 결과들


class MappingProfile(Base):
    """법인별 컬럼 매핑을 다음 업로드에서 재사용하기 위한 표입니다."""

    __tablename__ = "mapping_profiles"
    # 같은 프로젝트·법인·헤더 구성에는 하나의 매핑만 존재합니다.
    __table_args__ = (UniqueConstraint("project_id", "sales_company_id", "header_signature", name="uq_mapping_project_company_headers"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)  # 자동 증가 매핑 번호
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, default="project-demo")  # 매핑 소속 프로젝트
    sales_company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)  # 매핑 대상 판매법인
    header_signature: Mapped[str] = mapped_column(String(64), nullable=False)  # 헤더 구성을 식별하는 해시
    headers_json: Mapped[list] = mapped_column(JSON, nullable=False)  # 실제 원본 헤더 목록
    mapping_json: Mapped[dict] = mapped_column(JSON, nullable=False)  # 표준 항목과 컬럼 번호 연결
    pricing_json: Mapped[dict] = mapped_column(JSON, nullable=False)  # Net/Gross 단가·총액 선택
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())  # 마지막 수정 시각


class UploadRawRow(Base):
    """업로드 파일에서 읽은 원본 데이터 행을 가공 전 상태로 보존합니다."""

    __tablename__ = "upload_raw_rows"
    # 같은 배치의 같은 시트·행을 중복 저장하지 못하게 합니다.
    __table_args__ = (UniqueConstraint("batch_id", "sheet_name", "row_number", name="uq_upload_raw_position"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)  # 원본 행 자동 증가 ID
    batch_id: Mapped[str] = mapped_column(ForeignKey("upload_batches.id"), nullable=False, index=True)  # 소속 업로드 배치
    sheet_name: Mapped[str] = mapped_column(String(160), nullable=False)  # 원본 시트명
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)  # 원본 행 번호. 추적용이며 주문 ID가 아님
    raw_values: Mapped[list] = mapped_column(JSON, nullable=False)  # 원본 셀 값을 순서대로 보존한 목록
    formula_missing_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 수식 계산값이 빠진 컬럼 목록
    merged_columns: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 병합셀 범위에 포함된 컬럼 목록
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # 처리, 제외, 오류, 검토 중 하나
    issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 발견한 문제와 검토 근거

    batch: Mapped[UploadBatch] = relationship(back_populates="raw_rows")  # 소속 업로드 배치 객체
    standardized_result: Mapped["StandardizedResult | None"] = relationship(back_populates="raw_row", uselist=False, cascade="all, delete-orphan")  # 원본 행당 최대 1개 표준화 결과


class StandardizedResult(Base):
    """원본 행을 PO·모델·수량 등 표준 컬럼으로 변환한 결과입니다.

    오류나 검토 행도 삭제하지 않고 상태와 문제 사유를 저장합니다.
    """

    __tablename__ = "standardized_results"
    __table_args__ = (UniqueConstraint("raw_row_id", name="uq_standardized_raw_row"),)  # 원본 행당 결과 1개

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)  # 표준화 결과 자동 증가 ID
    batch_id: Mapped[str] = mapped_column(ForeignKey("upload_batches.id"), nullable=False, index=True)  # 소속 배치
    raw_row_id: Mapped[int] = mapped_column(ForeignKey("upload_raw_rows.id"), nullable=False)  # 변환 전 원본 행 ID
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # 처리, 제외, 오류, 검토
    issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 검증 문제와 근거
    po_number: Mapped[str | None] = mapped_column(String(80), nullable=True)  # 표준 PO. 앞자리 0 보존
    po_line_number: Mapped[str | None] = mapped_column(String(80), nullable=True)  # 표준 PO 라인
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 표준 모델명
    customer: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 표준 고객명
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 표준 주문 수량. 오류 행은 비어 있을 수 있음
    net_sales_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # 원본 Net 셀 값
    gross_sales_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # 원본 Gross 셀 값
    net_sales_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # 단가/총액 설정 적용 후 Net 총액
    gross_sales_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)  # 설정 적용 후 Gross 총액
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)  # 표준 통화 코드
    production_site: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 원본 생산사이트 문자열
    production_company_id: Mapped[str | None] = mapped_column(ForeignKey("companies.id"), nullable=True)  # 연결된 생산법인. 모호하면 비움
    sales_region: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 표준 판매지역
    applied_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True, unique=True)  # 최종 반영으로 생성된 주문 ID

    batch: Mapped[UploadBatch] = relationship(back_populates="standardized_results")  # 소속 업로드 배치
    raw_row: Mapped[UploadRawRow] = relationship(back_populates="standardized_result")  # 변환 전 원본 행


# =============================================================================
# 4. 판매계획 버전: 어느 자료 버전에 어떤 주문·수량이 있었는지 보존
# =============================================================================

class PlanVersion(Base):
    """특정 기준일에 받은 판매계획 파일 한 버전의 머리정보입니다."""

    __tablename__ = "plan_versions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 판매계획 버전 내부 ID
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, default="project-demo", index=True)  # 소속 프로젝트
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False)  # 버전의 근거 원본
    sales_company_id: Mapped[str] = mapped_column(ForeignKey("companies.id"), nullable=False)  # 자료를 보낸 판매법인
    reference_date: Mapped[str] = mapped_column(String(10), nullable=False)  # 원본 기준일. 예: 2026-10-29
    scope_type: Mapped[str] = mapped_column(String(24), nullable=False)  # 전체 현황 또는 변경분


class PlanVersionLine(Base):
    """판매계획 버전에 포함된 주문과 당시 수량을 연결합니다."""

    __tablename__ = "plan_version_lines"
    __table_args__ = (UniqueConstraint("plan_version_id", "order_id", name="uq_version_order"),)  # 버전 내 주문 중복 방지

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)  # 버전 상세 자동 증가 ID
    plan_version_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"), nullable=False)  # 상위 버전 ID
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False)  # 버전에 포함된 주문 ID
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # 이 버전에서 확인된 주문 수량


# =============================================================================
# 4-1. PSI 원본 버전: 업로드 파일과 주차별 값을 주문과 분리하여 보존
# =============================================================================

class PsiVersion(Base):
    """사용자가 업로드한 PSI 엑셀 한 파일을 하나의 버전으로 보존합니다.

    PSI는 주문을 직접 수정하는 자료가 아닙니다. 업로드 직후에는 항상 검토 대기 또는
    검증 보류 상태이며, 사용자가 승인해도 이 표의 상태만 바뀌고 Sales Master 주문은
    자동으로 덮어쓰지 않습니다.
    """

    __tablename__ = "psi_versions"
    __table_args__ = (UniqueConstraint("project_id", "file_hash", name="uq_project_psi_file_hash"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # PSI 버전 내부 ID
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)  # 소속 프로젝트
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False, unique=True)  # 원본 출처
    label: Mapped[str] = mapped_column(String(160), nullable=False)  # 사용자가 구분하기 위한 버전 이름
    base_month: Mapped[str] = mapped_column(String(7), nullable=False)  # 사용자가 지정한 PSI 기준월. 예: 2026-10
    reference_date: Mapped[Date] = mapped_column(Date, nullable=False)  # PSI 업무 기준일
    file_name: Mapped[str] = mapped_column(String(240), nullable=False)  # 원래 파일명
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # 동일 파일 중복 방지 해시
    file_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 원본 XLSX 바이트
    sheet_name: Mapped[str] = mapped_column(String(160), nullable=False)  # 실제로 읽은 시트명
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # 검증 보류, 검토 대기, 승인
    validation_issues: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # 파일 구조·값 오류
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    approved_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 파일을 등록한 사용자
    approved_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    source: Mapped[SourceMaterial] = relationship()  # 원본 자료 기본정보
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])  # 화면에 표시할 등록자
    values: Mapped[list["PsiValue"]] = relationship(back_populates="version", cascade="all, delete-orphan")  # 주차별 값


class PsiValue(Base):
    """PSI 한 버전의 모델·지표·주차별 수량 한 칸입니다."""

    __tablename__ = "psi_values"
    __table_args__ = (
        UniqueConstraint("version_id", "sales_company", "production_company", "model", "metric", "period_key", name="uq_psi_version_cell"),
        CheckConstraint("quantity >= 0", name="ck_psi_quantity_nonnegative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("psi_versions.id"), nullable=False, index=True)
    sales_company: Mapped[str] = mapped_column(String(120), nullable=False)  # 원본 판매법인 표시값
    production_company: Mapped[str] = mapped_column(String(120), nullable=False)  # 원본 생산법인 표시값
    model: Mapped[str] = mapped_column(String(120), nullable=False)  # 모델 접미사까지 보존
    metric: Mapped[str] = mapped_column(String(20), nullable=False)  # DM, SA, SP, Inventory, Warehouse
    period_key: Mapped[str] = mapped_column(String(12), nullable=False)  # 예: 2026-10-W3
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)  # 원본 엑셀 행 추적용
    source_column: Mapped[int] = mapped_column(Integer, nullable=False)  # 원본 엑셀 열 추적용

    version: Mapped[PsiVersion] = relationship(back_populates="values")


class PsiRiskReview(Base):
    """PSI 모델 단위 RISK 신호를 특정 주문에 연결해 승인·보류한 기록입니다."""

    __tablename__ = "psi_risk_reviews"
    __table_args__ = (UniqueConstraint("version_id", "sales_company", "production_company", "model", "risk_type", name="uq_psi_risk_review_signal"),)

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("psi_versions.id"), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    order_id: Mapped[str | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)  # 승인 시 선택한 주문
    sales_company: Mapped[str] = mapped_column(String(120), nullable=False)
    production_company: Mapped[str] = mapped_column(String(120), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    period_key: Mapped[str] = mapped_column(String(12), nullable=False)
    risk_type: Mapped[str] = mapped_column(String(40), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="검토 대기")  # 검토 대기, 승인, 보류
    review_note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    reviewed_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    version: Mapped[PsiVersion] = relationship()
    order: Mapped[Order | None] = relationship(back_populates="psi_risk_reviews")
    allocations: Mapped[list["PsiRiskAllocation"]] = relationship(back_populates="review", cascade="all, delete-orphan", order_by="PsiRiskAllocation.created_at")


class PsiRiskAllocation(Base):
    """PSI의 모델 단위 위험수량을 사용자가 확인한 주문들에 나눈 기록입니다.

    한 검토 신호의 배분수량 합계는 반드시 그 신호의 영향수량과 같아야 합니다.
    서버 API가 이 규칙과 주문별 수량 초과를 검사합니다.
    """

    __tablename__ = "psi_risk_allocations"
    __table_args__ = (
        UniqueConstraint("review_id", "order_id", name="uq_psi_risk_allocation_order"),
        CheckConstraint("quantity > 0", name="ck_psi_risk_allocation_positive"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    review_id: Mapped[str] = mapped_column(ForeignKey("psi_risk_reviews.id"), nullable=False, index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    review: Mapped[PsiRiskReview] = relationship(back_populates="allocations")
    order: Mapped[Order] = relationship(back_populates="psi_risk_allocations")


# =============================================================================
# 5. 주문에 연결되는 생산·선적·ERP 실적
# =============================================================================

class ProductionAllocation(Base):
    """주문에 배분된 생산 물량 한 건입니다. 20대를 15+5로 생산하면 두 행입니다."""

    __tablename__ = "production_allocations"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_production_quantity_positive"),)  # 0 이하 수량 금지

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 생산 상세 내부 ID
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)  # 연결된 주문 ID
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False)  # 생산정보 원본 ID
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # 이 상세 행의 생산 물량
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # 예정, 실제 완료, 검토 필요 등
    production_date: Mapped[Date | None] = mapped_column(Date, nullable=True)  # 생산 예정일 또는 실제 완료일
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # 합성 데모 자료 여부
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 사용자가 직접 확인·수정한 생산 상세
    order: Mapped[Order] = relationship(back_populates="production_details")  # 상위 주문 객체
    source: Mapped[SourceMaterial] = relationship()  # 생산정보 원본 객체


class ShipmentAllocation(Base):
    """주문에 배분된 선적 물량 한 건입니다. 20대를 10+10으로 선적하면 두 행입니다."""

    __tablename__ = "shipment_allocations"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_shipment_quantity_positive"),)  # 0 이하 수량 금지

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 선적 상세 내부 ID
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)  # 연결된 주문 ID
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False)  # 선적정보 원본 ID
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)  # 이 상세 행의 선적 물량
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # 예정, 실제 선적, 검토 필요 등
    shipment_date: Mapped[Date | None] = mapped_column(Date, nullable=True)  # 선적 예정일 또는 실제 선적일
    eta_date: Mapped[Date | None] = mapped_column(Date, nullable=True)  # 목적지 도착 예정일(ETA). ETD와 별도 보존
    carrier: Mapped[str | None] = mapped_column(String(100), nullable=True)  # 선사명. 없으면 비움
    vessel_name: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 선박명. 확인되지 않으면 비움
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)  # 합성 데모 자료 여부
    is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)  # 사용자가 직접 확인·수정한 선적 상세
    order: Mapped[Order] = relationship(back_populates="shipment_details")  # 상위 주문 객체
    source: Mapped[SourceMaterial] = relationship()  # 선적정보 원본 객체


class ErpConfirmation(Base):
    """실제 선적 후 ERP에서 매출화되었는지 확인한 기록입니다."""

    __tablename__ = "erp_confirmations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # ERP 확인 기록 ID
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)  # 확인 대상 주문
    status: Mapped[str] = mapped_column(String(32), nullable=False)  # 미확인, 일부 완료, 전체 완료 등
    confirmed_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # 매출화 확인 수량
    confirmation_date: Mapped[Date | None] = mapped_column(Date, nullable=True)  # ERP 확인일
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source_materials.id"), nullable=True)  # 확인 근거 원본
    order: Mapped[Order] = relationship(back_populates="erp_confirmations")  # 이 확인이 속한 주문


# =============================================================================
# 6. 변경 이력과 비정형(메일·텍스트·이미지) 원본
# =============================================================================

class ChangeEvent(Base):
    """주문에 일어난 변경과 그 근거를 남기는 감사 이력입니다."""

    __tablename__ = "change_events"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 변경 이력 ID
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)  # 변경된 주문 ID
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False)  # 변경 근거 원본 ID
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)  # 주문 등록, 최초 반영, 사본 생성 등
    changed_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)  # 변경 시각과 시간대
    before_value: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 변경 전 값. 최초 생성이면 비움
    after_value: Mapped[str | None] = mapped_column(String(500), nullable=True)  # 변경 후 값 또는 변경 설명


class UnstructuredInput(Base):
    """메일 본문, HTML, 텍스트, 캡처이미지처럼 표 형태가 아닌 원본입니다.

    추출에 실패해도 원본을 버리지 않습니다. 추출 결과는 검토용으로 저장하고,
    사용자가 확인하기 전에는 Sales Master에 자동 반영하지 않습니다.
    """

    __tablename__ = "unstructured_inputs"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # 비정형 입력 내부 ID
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)  # 소속 프로젝트
    source_id: Mapped[str] = mapped_column(ForeignKey("source_materials.id"), nullable=False, unique=True)  # 공통 원본 자료 ID
    data_type: Mapped[str] = mapped_column(String(30), nullable=False)  # 판매계획, 생산정보, 선적정보, 기타
    input_type: Mapped[str] = mapped_column(String(20), nullable=False)  # 메일, HTML, 텍스트 파일, 이미지, 붙여넣기
    file_name: Mapped[str | None] = mapped_column(String(240), nullable=True)  # 원래 파일명. 붙여넣기는 없음
    content_type: Mapped[str] = mapped_column(String(120), nullable=False)  # image/png, text/plain 같은 파일 형식
    raw_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # 재다운로드 가능한 원본 바이트
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)  # 메일/HTML 본문 또는 OCR 결과
    extracted_fields: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)  # 'PO: 값'처럼 찾은 필드와 값
    extraction_status: Mapped[str] = mapped_column(String(40), nullable=False)  # 추출 완료, OCR 도구 필요, 실행 실패 등
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())  # 원본 저장 시각
    created_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # 이번 원본을 등록하거나 덮어쓴 사용자

    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])  # 업로드 이력에 표시할 등록자
