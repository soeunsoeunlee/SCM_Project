// 대시보드와 Sales Master가 사용하는 API 자료형과 공통 요청 함수를 모은 파일입니다.

// FastAPI가 반환하는 주문 한 건의 모양입니다. null은 아직 확인되지 않은 선택값입니다.
export type Order = {
  id: string;
  order_number: string;
  sales_company_id: string;
  sales_company: string;
  production_company_id: string | null;
  production_company: string;
  target_month: string;
  po_number: string;
  po_line_number: string | null;
  model: string;
  customer: string;
  quantity: number;
  currency: string;
  unit_price: number;
  total_amount: number;
  net_sales_amount: number | null;
  gross_sales_amount: number | null;
  net_sales_type: string | null;
  gross_sales_type: string | null;
  production_site_source: string | null;
  sales_region: string | null;
  upload_batch_id: string | null;
  erp_status: string;
  match_status: string;
  source_name: string;
  source_download_url: string | null;
  source_download_label: string | null;
  production_quantity: number;
  shipment_quantity: number;
  closed_quantity: number;
  production_planned_date: string | null;
  production_next_date: string | null;
  production_planned_quantity: number;
  production_remaining_quantity: number;
  production_carryover_quantity: number;
  production_schedule_status: string;
  shipment_planned_date: string | null;
  shipment_next_date: string | null;
  shipment_planned_quantity: number;
  shipment_remaining_quantity: number;
  shipment_carryover_quantity: number;
  shipment_schedule_status: string;
  review_quantity: number;
  risk_status: string;
  risk_reasons: string[];
  risk_quantity: number;
  production_details: { id: string; quantity: number; status: string; production_date: string | null; is_manual: boolean }[];
  shipment_details: { id: string; quantity: number; status: string; shipment_date: string | null; carrier: string | null; vessel_name: string | null; is_manual: boolean }[];
  manual_fields: string[];
  pending_update_count: number;
  is_demo: boolean;
};

export type Dashboard = {
  // 대시보드 요약 카드와 법인·최근 주문에 필요한 값입니다.
  order_count: number;
  planned_quantity: number;
  planned_value_by_currency: Record<string, number>;
  produced_quantity: number;
  shipped_quantity: number;
  erp_unconfirmed_orders: number;
  review_orders: number;
  companies: { id: string; name: string; kind: string; country: string }[];
  recent_orders: Order[];
  is_demo: boolean;
};
export type Company = Dashboard["companies"][number];

export type DemoOrderInput = {
  // 화면의 테스트 주문 저장 폼에서 서버로 보내는 값입니다.
  sales_company_id: string;
  production_company_id: string;
  target_month: string;
  po_number: string;
  model: string;
  customer: string;
  quantity: number;
  currency: string;
  unit_price: number;
};

export type OrderEditInput = {
  order_number: string; sales_company_id: string; production_company_id: string; target_month: string;
  po_number: string; po_line_number: string | null; model: string; customer: string;
  quantity: number; net_sales_amount: number | null; gross_sales_amount: number | null;
  production_site_source: string | null; sales_region: string | null; match_status: string;
  production_details: { id?: string; quantity: number; status: string; production_date: string | null }[];
  shipment_details: { id?: string; quantity: number; status: string; shipment_date: string | null; carrier: string | null; vessel_name: string | null }[];
};
export type OrderUpdateProposal = {
  id: string; order_id: string; status: string; source_name: string; created_at: string;
  differences: Record<string, { current: string | number | null; incoming: string | number | null; manual_priority: boolean }>;
  match_basis: { confidence: "high" | "medium" | "low"; reason: string; signals: string[] } | null;
};

export type PsiData = {
  periods: { key: string; year: number; month: number; week: string }[];
  rows: { sales_company: string; production_company: string; model: string; metric: "DM" | "SA" | "SP" | "Inventory" | "Warehouse"; values: Record<string, number> }[];
  is_demo: boolean;
  inventory_basis: string;
};

export type PsiVersionSummary = {
  id: string; label: string; base_month: string; reference_date: string; file_name: string; sheet_name: string;
  status: "검증 보류" | "검토 대기" | "승인"; issue_count: number; value_count: number;
  created_at: string | null; created_by: string; approved_at: string | null;
};

export type PsiVersionDetail = {
  version: PsiVersionSummary;
  validation_issues: string[];
  previous_version: PsiVersionSummary | null;
  comparison: null | {
    changed_cells: number; added_cells: number; removed_cells: number; quantity_delta: number;
    changes: { sales_company: string; production_company: string; model: string; metric: string; period_key: string; before: number | null; after: number | null; delta: number; change_type: string }[];
  };
  risk_signals: { sales_company: string; production_company: string; model: string; period_key: string; risk_type: string; quantity: number; reason: string }[];
  risk_reviews: { id: string; version_id: string; sales_company: string; production_company: string; model: string; period_key: string; risk_type: string; quantity: number; reason: string; status: string; order_id: string | null; review_note: string | null; allocations: { id: string; order_id: string; quantity: number }[]; candidates: { id: string; order_number: string; po_number: string; target_month: string; quantity: number }[] }[];
  psi: PsiData;
  duplicate_file?: boolean;
  overwritten?: boolean;
  message?: string;
};

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  // 모든 요청을 Next.js 프록시로 보내고, 실패하면 FastAPI의 쉬운 오류 메시지를 꺼냅니다.
  const response = await fetch(`/api/backend${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `서버 응답 오류 (${response.status})`);
  }
  return response.json() as Promise<T>;
}

// 각 화면에서 사용할 수 있도록 목적이 분명한 함수 이름으로 공통 요청을 감쌉니다.
export const getDashboard = () => apiFetch<Dashboard>("/api/dashboard");
export const createCompany = (input: { name: string; kind: "판매법인" | "생산법인"; country?: string | null }) => apiFetch<Company>("/api/companies", { method: "POST", body: JSON.stringify(input) });
export const getOrders = () => apiFetch<Order[]>("/api/orders");
export const getPsi = () => apiFetch<PsiData>("/api/psi");
export const getPsiVersions = () => apiFetch<PsiVersionSummary[]>("/api/psi/versions");
export const getPsiVersion = (id: string) => apiFetch<PsiVersionDetail>(`/api/psi/versions/${id}`);
export async function uploadPsiVersion(form: FormData): Promise<PsiVersionDetail> {
  const response = await fetch("/api/backend/api/psi/versions", { method: "POST", body: form });
  const body = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof body?.detail === "string" ? body.detail : `서버 응답 오류 (${response.status})`);
  return body as PsiVersionDetail;
}
export const approvePsiVersion = (id: string) => apiFetch<{ version: PsiVersionSummary; duplicate_request: boolean; message: string }>(`/api/psi/versions/${id}/approve`, { method: "POST" });
export const deletePsiVersion = (id: string) => apiFetch<{ deleted: boolean; id: string; source_id: string; message: string }>(`/api/psi/versions/${id}`, { method: "DELETE" });
export const decidePsiRisk = (id: string, decision: "승인" | "보류", allocations: { order_id: string; quantity: number }[], note?: string) => apiFetch<{ review: PsiVersionDetail["risk_reviews"][number]; message: string }>(`/api/psi/risk-reviews/${id}/decision`, { method: "POST", body: JSON.stringify({ decision, allocations, note: note || null }) });
export type HistoryItem = { id: string; changed_at: string; event_type: string; order_id: string; order_number: string; po_number: string; model: string; before_value: string | null; after_value: string | null; source_name: string };
export const getHistory = () => apiFetch<HistoryItem[]>("/api/history");
export type IntegrationStatus = { project_id: string; connectors: { name: string; status: string; detail: string }[]; security: string[] };
export const getIntegrationStatus = () => apiFetch<IntegrationStatus>("/api/integration-status");
export const createErpConfirmation = (orderId: string, input: { status: string; confirmed_quantity: number; confirmation_date: string | null; note: string | null }) => apiFetch<{ id: string; order_id: string; status: string; closed_quantity: number; message: string }>(`/api/orders/${orderId}/erp-confirmations`, { method: "POST", body: JSON.stringify(input) });
export const editOrder = (orderId: string, input: OrderEditInput) => apiFetch<{ message: string; order_id: string; protected_fields: string[] }>(`/api/orders/${orderId}`, { method: "PATCH", body: JSON.stringify(input) });
export const getOrderUpdateProposals = (orderId: string) => apiFetch<OrderUpdateProposal[]>(`/api/orders/${orderId}/update-proposals`);
export const applyOrderUpdateProposal = (proposalId: string, fields: string[]) => apiFetch<{ message: string; applied_fields: string[] }>(`/api/order-update-proposals/${proposalId}/apply`, { method: "POST", body: JSON.stringify({ fields }) });
export const createDemoOrder = (order: DemoOrderInput) => apiFetch<Order>("/api/orders", {
  method: "POST",
  body: JSON.stringify(order),
});
