// 자료 업로드 화면 전용 API 자료형과 요청 함수를 모은 파일입니다.

// 파일 한 번의 업로드·검증·반영 상태입니다.
export type UploadBatch = {
  id: string;
  file_name: string;
  data_type: string;
  sales_company_id: string | null;
  sales_company: string;
  production_company_id: string | null;
  production_company: string | null;
  target_month: string;
  reference_date: string;
  scope_type: string;
  scope_description: string;
  status: string;
  application_mode: string | null;
  result_message: string | null;
  selected_sheet: string | null;
  header_row: number | null;
  download_url: string;
};

export type SheetPreview = {
  name: string;
  row_count: number;
  column_count: number;
  preview: unknown[][];
};
// 원본 보존 직후 서버에서 받는 시트 목록과 미리보기입니다.
export type InspectResult = {
  batch: UploadBatch;
  sheets: SheetPreview[];
  duplicate_file: boolean;
  overwritten?: boolean;
  message: string;
};
export type MappingContext = {
  // 원본 헤더, 별칭 제안, 저장된 법인별 매핑을 한 번에 담습니다.
  headers: string[];
  header_signature: string;
  suggested_mapping: Record<string, number>;
  saved_mapping: Record<string, number> | null;
  saved_pricing: Record<string, string> | null;
  profile_reused: boolean;
  ai_status: "used" | "not_needed" | "not_configured" | "failed";
  ai_suggestions: Record<
    string,
    { source_index: number; confidence: "high" | "medium"; reason: string }
  >;
  message: string;
  standard_fields: { key: string; label: string; required: boolean }[];
};
export type ValidationRow = {
  // 원본 데이터 행 하나의 처리/제외/오류/검토 결과입니다.
  id: number;
  row_number: number;
  status: string;
  issues: string[];
  raw_values: unknown[];
  po_number: string | null;
  po_line_number: string | null;
  model: string | null;
  customer: string | null;
  quantity: number | null;
  net_sales_value: number | null;
  gross_sales_value: number | null;
  net_sales_total: number | null;
  gross_sales_total: number | null;
  currency: string | null;
  production_site: string | null;
  sales_region: string | null;
  applied_order_id: string | null;
};
export type Reconciliation = {
  // 통화별 원본 합계와 실제 반영 예정 합계를 비교합니다.
  currency: string;
  original_quantity: number;
  planned_quantity: number;
  original_net: number;
  planned_net: number;
  original_gross: number;
  planned_gross: number;
  net_available: boolean;
  gross_available: boolean;
  net_basis: string;
  gross_basis: string;
};
export type PrepareResult = {
  // 표준화·검증 API 전체 응답입니다.
  batch: UploadBatch;
  headers: string[];
  status_counts: Record<string, number>;
  reconciliation: Reconciliation[];
  rows: ValidationRow[];
  row_count: number;
  can_apply: boolean;
  application_mode: string;
  apply_label: string;
};
export type SampleFile = {
  id: string;
  file_name: string;
  label: string;
  format: string;
  kind: string;
  download_url: string;
};
export type UnstructuredResult = {
  id: string;
  source_id: string;
  data_type: string;
  input_type: string;
  file_name: string | null;
  extraction_status: string;
  extracted_text: string | null;
  extracted_fields: Record<string, unknown>;
  duplicate: boolean;
  overwritten?: boolean;
  message: string;
};
export type UploadHistoryItem = {
  id: string;
  file_name: string;
  data_type: string;
  sales_company: string | null;
  production_company: string | null;
  target_month: string | null;
  reference_date: string | null;
  scope_type: string | null;
  status: string;
  result_message?: string | null;
  input_type: string;
  record_kind: "batch" | "unstructured";
  deletable: boolean;
  created_by: string | null;
  created_at: string;
  download_url: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 업로드 API 공통 요청 함수입니다. FormData 요청에는 Content-Type을 직접 넣지 않습니다.
  const response = await fetch(`/api/backend${path}`, {
    ...init,
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(
      typeof body?.detail === "string"
        ? body.detail
        : `서버 응답 오류 (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

// JSON POST 요청 설정을 짧게 만드는 보조 함수입니다.
const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

// 업로드 화면의 각 단계가 호출하는 API 함수입니다.
export const getUploadSamples = () =>
  request<SampleFile[]>("/api/upload-samples");
export const getUploadHistory = () =>
  request<UploadHistoryItem[]>("/api/uploads/history");
export const inspectUpload = (form: FormData) =>
  request<InspectResult>("/api/uploads/inspect", {
    method: "POST",
    body: form,
  });
export const reopenUpload = (batchId: string) =>
  request<InspectResult>(`/api/uploads/${batchId}/inspect`);
export const getMapping = (
  batchId: string,
  sheet_name: string,
  header_row: number,
) =>
  request<MappingContext>(
    `/api/uploads/${batchId}/mapping`,
    json({ sheet_name, header_row }),
  );
export const prepareUpload = (
  batchId: string,
  body: {
    sheet_name: string;
    header_row: number;
    mapping: Record<string, number>;
    pricing: Record<string, string>;
  },
) => request<PrepareResult>(`/api/uploads/${batchId}/prepare`, json(body));
export const getUploadValidation = (batchId: string) =>
  request<PrepareResult>(`/api/uploads/${batchId}/validation`);
export const excludeUploadRow = (batchId: string, resultId: number) =>
  request<PrepareResult>(`/api/uploads/${batchId}/rows/${resultId}/exclude`, {
    method: "POST",
  });
export const applyUpload = (batchId: string) =>
  request<{
    batch: UploadBatch;
    created_orders: number;
    duplicate_request: boolean;
    message: string;
  }>(`/api/uploads/${batchId}/apply`, { method: "POST" });
export const inspectUnstructured = (form: FormData) =>
  request<UnstructuredResult>("/api/unstructured/inspect", {
    method: "POST",
    body: form,
  });
export const deleteUploadMaterial = (
  recordKind: UploadHistoryItem["record_kind"],
  id: string,
) =>
  request<{
    deleted: boolean;
    id: string;
    source_id: string;
    deleted_order_count: number;
    message: string;
  }>(`/api/upload-materials/${recordKind}/${id}`, { method: "DELETE" });
