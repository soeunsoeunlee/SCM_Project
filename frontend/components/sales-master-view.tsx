"use client";

// Sales Master 주문, 선택형 필터, 생산·선적 일정 요약과 데모 주문 저장 화면입니다.
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  applyOrderUpdateProposal,
  createDemoOrder,
  createErpConfirmation,
  Dashboard,
  DemoOrderInput,
  editOrder,
  getDashboard,
  getOrders,
  getOrderUpdateProposals,
  Order,
  OrderEditInput,
  OrderUpdateProposal,
} from "@/lib/api";

const integer = new Intl.NumberFormat("ko-KR");
const usd = new Intl.NumberFormat("ko-KR", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});
const emptyForm = {
  sales_company_id: "sales-a",
  production_company_id: "plant-a",
  target_month: "2026-12",
  po_number: "",
  model: "",
  customer: "",
  quantity: "1",
  currency: "USD",
  unit_price: "",
};
const emptyFilters = {
  so: "",
  po: "",
  company: "",
  plant: "",
  model: "",
  customer: "",
  month: "",
  quantity: "",
  unitPrice: "",
  review: "",
  risk: "",
  production: "",
  shipment: "",
  net: "",
  gross: "",
  closed: "",
  status: "",
  source: "",
};

function unique(values: (string | number | null | undefined)[]) {
  return [
    ...new Set(
      values
        .filter((value) => value !== null && value !== undefined)
        .map(String),
    ),
  ].sort((a, b) => a.localeCompare(b, "ko"));
}
function matchesFilter(value: string | number | null | undefined, filter: string) {
  return !filter || String(value ?? "").toLocaleLowerCase("ko").includes(filter.trim().toLocaleLowerCase("ko"));
}
function netAmount(order: Order) {
  return order.net_sales_amount ?? 0;
}
function grossAmount(order: Order) {
  return order.gross_sales_amount ?? 0;
}
function reviewLabel(order: Order) {
  return order.match_status === "검토 필요" || order.review_quantity > 0
    ? "필요"
    : "불필요";
}
function stateLabel(order: Order) {
  if (reviewLabel(order) === "필요")
    return { label: "검토 필요", type: "amber" };
  if (order.shipment_quantity >= order.quantity && order.quantity)
    return { label: "선적 완료", type: "green" };
  if (order.shipment_quantity > 0) return { label: "부분 선적", type: "blue" };
  if (order.production_quantity > 0)
    return { label: "생산 진행", type: "teal" };
  return { label: "미생산", type: "gray" };
}
function scheduleFilter(order: Order, stage: "production" | "shipment") {
  return stage === "production"
    ? order.production_schedule_status
    : order.shipment_schedule_status;
}

function editValues(order: Order): OrderEditInput {
  return {
    order_number: order.order_number,
    sales_company_id: order.sales_company_id,
    production_company_id: order.production_company_id ?? "",
    target_month: order.target_month,
    po_number: order.po_number,
    po_line_number: order.po_line_number,
    model: order.model,
    customer: order.customer,
    quantity: order.quantity,
    net_sales_amount: order.net_sales_amount,
    gross_sales_amount: order.gross_sales_amount,
    production_site_source: order.production_site_source,
    sales_region: order.sales_region,
    match_status: order.match_status,
    production_details: order.production_details.map((row) => ({
      id: row.id,
      quantity: row.quantity,
      status: row.status,
      production_date: row.production_date,
    })),
    shipment_details: order.shipment_details.map((row) => ({
      id: row.id,
      quantity: row.quantity,
      status: row.status,
      shipment_date: row.shipment_date,
      carrier: row.carrier,
      vessel_name: row.vessel_name,
    })),
  };
}

const fieldLabels: Record<string, string> = {
  order_number: "SO",
  sales_company_id: "판매법인",
  production_company_id: "생산법인",
  target_month: "대상 월",
  po_number: "PO",
  po_line_number: "PO 라인",
  model: "모델",
  customer: "고객",
  quantity: "주문 수량",
  net_sales_amount: "Net Sales",
  gross_sales_amount: "Gross Sales",
  production_site_source: "생산사이트",
  sales_region: "판매지역",
  match_status: "Status",
};

function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const listId = `filter-${label.replace(/[^a-zA-Z0-9가-힣]/g, "-")}`;
  return (
    <>
    <input
      aria-label={`${label} 필터`}
      list={listId}
      value={value}
      placeholder="검색 또는 선택"
      onChange={(event) => onChange(event.target.value)}
    />
    <datalist id={listId}>
      {options.map((option) => (
        <option key={option} value={option} />
      ))}
    </datalist>
    </>
  );
}

function ScheduleCell({
  order,
  stage,
}: {
  order: Order;
  stage: "production" | "shipment";
}) {
  const production = stage === "production";
  const plannedDate = production
    ? order.production_planned_date
    : order.shipment_planned_date;
  const nextDate = production
    ? order.production_next_date
    : order.shipment_next_date;
  const planned = production
    ? order.production_planned_quantity
    : order.shipment_planned_quantity;
  const actual = production
    ? order.production_quantity
    : order.shipment_quantity;
  const remaining = production
    ? order.production_remaining_quantity
    : order.shipment_remaining_quantity;
  const carryover = production
    ? order.production_carryover_quantity
    : order.shipment_carryover_quantity;
  const status = production
    ? order.production_schedule_status
    : order.shipment_schedule_status;
  const completionLabel = production ? "생산완료" : "선적완료";
  return (
    <div
      className={`schedule-cell ${status === "지연" || status === "일정 미정" ? "attention" : ""}`}
    >
      <div className="schedule-top">
        <span
          className={`schedule-status ${status === "완료" ? "done" : status === "일정 있음" ? "planned" : "warning"}`}
        >
          {status}
        </span>
        <strong>
          {status === "완료" ? completionLabel : plannedDate ?? "예정일 미정"}
        </strong>
      </div>
      <div className="schedule-progress">
        <span
          style={{
            width: `${order.quantity ? Math.min((actual / order.quantity) * 100, 100) : 0}%`,
          }}
        />
      </div>
      <div className="schedule-numbers">
        <b>
          완료 {integer.format(actual)}/{integer.format(order.quantity)}ea
        </b>
        <span>미완료 {integer.format(remaining)}ea</span>
      </div>
      <p>
        {status === "완료"
          ? `${completionLabel} · 추가 일정 없음`
          : `${planned ? `예정 ${integer.format(planned)}ea` : "추가 예정 물량 없음"} · 다음 일정 ${nextDate ?? "미정"}`}
      </p>
      {carryover > 0 && (
        <p className="carryover">이월 가능 {integer.format(carryover)}ea</p>
      )}
      {production && order.production_details.length > 0 && (
        <div className="production-lines">
          {order.production_details.map((detail) => (
            <div key={detail.id}>
              <span
                className={`schedule-status ${detail.status === "실제 완료" ? "done" : detail.status === "예정" ? "planned" : "warning"}`}
              >
                {detail.status}
              </span>
              <b>
                {detail.production_date ?? "일정 미정"} ·{" "}
                {integer.format(detail.quantity)}ea
                {detail.is_manual ? " · 수동 확인" : ""}
              </b>
            </div>
          ))}
        </div>
      )}
      {!production && order.shipment_details.length > 0 && (
        <div className="shipment-lines">
          {order.shipment_details.map((detail) => (
            <div key={detail.id}>
              <span>
                {detail.shipment_date ?? "ETD 미정"} · {detail.status}{" "}
                {integer.format(detail.quantity)}ea
              </span>
              <b>
                {detail.carrier ?? "선사 미정"} ·{" "}
                {detail.vessel_name ?? "선박 미정"}
              </b>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function SalesMasterView() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [companies, setCompanies] = useState<Dashboard["companies"]>([]);
  const [filters, setFilters] = useState(emptyFilters);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [erpOrderId, setErpOrderId] = useState("");
  const [erpStatus, setErpStatus] = useState("일부 완료");
  const [erpQuantity, setErpQuantity] = useState("1");
  const [erpDate, setErpDate] = useState(new Date().toISOString().slice(0, 10));
  const [erpNote, setErpNote] = useState("");
  const [editingOrder, setEditingOrder] = useState<Order | null>(null);
  const [editForm, setEditForm] = useState<OrderEditInput | null>(null);
  const [proposalOrder, setProposalOrder] = useState<Order | null>(null);
  const [proposals, setProposals] = useState<OrderUpdateProposal[]>([]);
  const [proposalFields, setProposalFields] = useState<Record<string, boolean>>(
    {},
  );

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [rows, dashboard] = await Promise.all([
        getOrders(),
        getDashboard(),
      ]);
      setOrders(Array.isArray(rows) ? rows : []);
      setCompanies(
        Array.isArray(dashboard.companies) ? dashboard.companies : [],
      );
      setError("");
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "서버에 연결할 수 없습니다.",
      );
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    void reload();
  }, [reload]);

  const filtered = useMemo(
    () =>
      orders.filter(
        (order) =>
          matchesFilter(order.order_number, filters.so) &&
          matchesFilter(order.po_number, filters.po) &&
          matchesFilter(order.sales_company, filters.company) &&
          matchesFilter(order.production_company, filters.plant) &&
          matchesFilter(order.model, filters.model) &&
          matchesFilter(order.customer, filters.customer) &&
          matchesFilter(order.target_month, filters.month) &&
          matchesFilter(order.quantity, filters.quantity) &&
          matchesFilter(usd.format(order.unit_price), filters.unitPrice) &&
          matchesFilter(reviewLabel(order), filters.review) &&
          matchesFilter(order.risk_status, filters.risk) &&
          matchesFilter(scheduleFilter(order, "production"), filters.production) &&
          matchesFilter(scheduleFilter(order, "shipment"), filters.shipment) &&
          matchesFilter(usd.format(netAmount(order)), filters.net) &&
          matchesFilter(usd.format(grossAmount(order)), filters.gross) &&
          matchesFilter(order.erp_status, filters.closed) &&
          matchesFilter(stateLabel(order).label, filters.status) &&
          matchesFilter(order.source_name, filters.source),
      ),
    [orders, filters],
  );

  const options = useMemo(
    () => ({
      so: unique(orders.map((order) => order.order_number)),
      po: unique(orders.map((order) => order.po_number)),
      company: unique(orders.map((order) => order.sales_company)),
      plant: unique(orders.map((order) => order.production_company)),
      model: unique(orders.map((order) => order.model)),
      customer: unique(orders.map((order) => order.customer)),
      month: unique(orders.map((order) => order.target_month)),
      quantity: unique(orders.map((order) => order.quantity)),
      unitPrice: unique(orders.map((order) => usd.format(order.unit_price))),
      review: ["필요", "불필요"],
      risk: unique(orders.map((order) => order.risk_status)),
      production: unique(
        orders.map((order) => order.production_schedule_status),
      ),
      shipment: unique(orders.map((order) => order.shipment_schedule_status)),
      net: unique(orders.map((order) => usd.format(netAmount(order)))),
      gross: unique(orders.map((order) => usd.format(grossAmount(order)))),
      closed: unique(orders.map((order) => order.erp_status)),
      status: unique(orders.map((order) => stateLabel(order).label)),
      source: unique(orders.map((order) => order.source_name)),
    }),
    [orders],
  );

  const totals = useMemo(
    () =>
      filtered.reduce(
        (sum, order) => ({
          quantity: sum.quantity + order.quantity,
          review: sum.review + order.review_quantity,
          risk: sum.risk + order.risk_quantity,
          production: sum.production + order.production_quantity,
          shipment: sum.shipment + order.shipment_quantity,
          net: sum.net + netAmount(order),
          gross: sum.gross + grossAmount(order),
        }),
        {
          quantity: 0,
          review: 0,
          risk: 0,
          production: 0,
          shipment: 0,
          net: 0,
          gross: 0,
        },
      ),
    [filtered],
  );
  const setFilter = (key: keyof typeof filters, value: string) =>
    setFilters((current) => ({ ...current, [key]: value }));

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const payload: DemoOrderInput = {
        ...form,
        quantity: Number(form.quantity),
        unit_price: Number(form.unit_price),
      };
      const saved = await createDemoOrder(payload);
      await reload();
      setNotice(`${saved.order_number} 주문을 저장하고 다시 조회했습니다.`);
      setForm(emptyForm);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "데모 주문을 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function submitClosed(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!erpOrderId) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await createErpConfirmation(erpOrderId, {
        status: erpStatus,
        confirmed_quantity: Number(erpQuantity),
        confirmation_date: erpDate || null,
        note: erpNote || null,
      });
      await reload();
      setNotice(
        `${result.message} 누적 Closed ${result.closed_quantity}ea입니다.`,
      );
      setErpOrderId("");
      setErpNote("");
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Closed 확인을 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function submitEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!editingOrder || !editForm) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const result = await editOrder(editingOrder.id, editForm);
      await reload();
      setNotice(result.message);
      setEditingOrder(null);
      setEditForm(null);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "수동 수정값을 저장하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function openProposals(order: Order) {
    setSaving(true);
    setError("");
    try {
      const rows = await getOrderUpdateProposals(order.id);
      setProposalOrder(order);
      setProposals(rows);
      const selected: Record<string, boolean> = {};
      rows.forEach((row) =>
        Object.keys(row.differences).forEach((field) => {
          selected[`${row.id}:${field}`] = false;
        }),
      );
      setProposalFields(selected);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "업데이트 검토 내용을 불러오지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  async function applyProposal(proposal: OrderUpdateProposal) {
    const fields = Object.keys(proposal.differences).filter(
      (field) => proposalFields[`${proposal.id}:${field}`],
    );
    setSaving(true);
    setError("");
    try {
      const result = await applyOrderUpdateProposal(proposal.id, fields);
      await reload();
      if (proposalOrder) await openProposals(proposalOrder);
      setNotice(result.message);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "선택한 업데이트를 반영하지 못했습니다.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <section className="page-heading sales-heading">
        <div>
          <div className="breadcrumb">
            <Link href="/">업무 현황</Link>
            <span>/</span>Sales Master
          </div>
          <div className="heading-line">
            <h1>Sales Master</h1>
            <span className="status-chip">● 서버 데이터</span>
          </div>
          <p className="heading-subtitle">
            생산과 선적의 예정일, 완료수량, 다음 일정과 이월 가능 수량을
            주문별로 확인합니다.
          </p>
        </div>
      </section>
      {error && (
        <div className="error-banner" role="alert">
          <span>서버 요청에 실패했습니다: {error}</span>
          <button className="small-button" onClick={() => void reload()}>
            다시 연결
          </button>
        </div>
      )}
      {notice && (
        <div className="success-banner" role="status">
          ✓ {notice}
        </div>
      )}
      {editingOrder && editForm && (
        <form className="panel order-edit-panel" onSubmit={submitEdit}>
          <div className="panel-heading">
            <div>
              <h2>{editingOrder.order_number} 수동 수정</h2>
              <p>
                저장한 값은 새 파일보다 우선합니다. 예정일이 지났어도 상태를
                `실제 완료`로 직접 확인하기 전에는 생산 실적으로 계산하지
                않습니다.
              </p>
            </div>
            <button
              className="small-button neutral"
              type="button"
              onClick={() => {
                setEditingOrder(null);
                setEditForm(null);
              }}
            >
              닫기
            </button>
          </div>
          <div className="order-edit-grid">
            <label>
              SO
              <input
                required
                value={editForm.order_number}
                onChange={(event) =>
                  setEditForm({ ...editForm, order_number: event.target.value })
                }
              />
            </label>
            <label>
              판매법인
              <select
                value={editForm.sales_company_id}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    sales_company_id: event.target.value,
                  })
                }
              >
                {companies
                  .filter((row) => row.kind === "판매법인")
                  .map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              생산법인
              <select
                value={editForm.production_company_id}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    production_company_id: event.target.value,
                  })
                }
              >
                {companies
                  .filter((row) => row.kind === "생산법인")
                  .map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              대상 월
              <input
                type="month"
                required
                value={editForm.target_month}
                onChange={(event) =>
                  setEditForm({ ...editForm, target_month: event.target.value })
                }
              />
            </label>
            <label>
              PO
              <input
                required
                value={editForm.po_number}
                onChange={(event) =>
                  setEditForm({ ...editForm, po_number: event.target.value })
                }
              />
            </label>
            <label>
              PO 라인
              <input
                value={editForm.po_line_number ?? ""}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    po_line_number: event.target.value || null,
                  })
                }
              />
            </label>
            <label>
              모델
              <input
                required
                value={editForm.model}
                onChange={(event) =>
                  setEditForm({ ...editForm, model: event.target.value })
                }
              />
            </label>
            <label>
              고객
              <input
                required
                value={editForm.customer}
                onChange={(event) =>
                  setEditForm({ ...editForm, customer: event.target.value })
                }
              />
            </label>
            <label>
              주문 수량(ea)
              <input
                type="number"
                min="1"
                required
                value={editForm.quantity}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    quantity: Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              Net Sales (USD)
              <input
                type="number"
                min="0"
                step="0.01"
                value={editForm.net_sales_amount ?? ""}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    net_sales_amount:
                      event.target.value === ""
                        ? null
                        : Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              Gross Sales (USD)
              <input
                type="number"
                min="0"
                step="0.01"
                value={editForm.gross_sales_amount ?? ""}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    gross_sales_amount:
                      event.target.value === ""
                        ? null
                        : Number(event.target.value),
                  })
                }
              />
            </label>
            <label>
              생산사이트
              <input
                value={editForm.production_site_source ?? ""}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    production_site_source: event.target.value || null,
                  })
                }
              />
            </label>
            <label>
              판매지역
              <input
                value={editForm.sales_region ?? ""}
                onChange={(event) =>
                  setEditForm({
                    ...editForm,
                    sales_region: event.target.value || null,
                  })
                }
              />
            </label>
            <label>
              Status
              <select
                value={editForm.match_status}
                onChange={(event) =>
                  setEditForm({ ...editForm, match_status: event.target.value })
                }
              >
                <option>연결 대기</option>
                <option>판매자료 반영</option>
                <option>검토 필요</option>
                <option>연결 완료</option>
              </select>
            </label>
          </div>
          <div className="detail-edit-sections">
            <section>
              <div className="detail-edit-heading">
                <div>
                  <strong>생산 상세</strong>
                  <span>실제 완료는 완료일을 반드시 입력합니다.</span>
                </div>
                <button
                  type="button"
                  className="small-button neutral"
                  onClick={() =>
                    setEditForm({
                      ...editForm,
                      production_details: [
                        ...editForm.production_details,
                        { quantity: 1, status: "예정", production_date: null },
                      ],
                    })
                  }
                >
                  생산 일정 추가
                </button>
              </div>
              {editForm.production_details.map((row, index) => (
                <div
                  className="detail-edit-row production"
                  key={row.id ?? `new-production-${index}`}
                >
                  <select
                    value={row.status}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        production_details: editForm.production_details.map(
                          (item, i) =>
                            i === index
                              ? { ...item, status: event.target.value }
                              : item,
                        ),
                      })
                    }
                  >
                    <option>예정</option>
                    <option>실제 완료</option>
                    <option>검토 필요</option>
                  </select>
                  <input
                    aria-label="생산 수량"
                    type="number"
                    min="1"
                    value={row.quantity}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        production_details: editForm.production_details.map(
                          (item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  quantity: Number(event.target.value),
                                }
                              : item,
                        ),
                      })
                    }
                  />
                  <input
                    aria-label="생산 날짜"
                    type="date"
                    value={row.production_date ?? ""}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        production_details: editForm.production_details.map(
                          (item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  production_date: event.target.value || null,
                                }
                              : item,
                        ),
                      })
                    }
                  />
                  <span>
                    {row.id ? "기존 상세 · 저장 시 수동 우선" : "새 상세"}
                  </span>
                </div>
              ))}
            </section>
            <section>
              <div className="detail-edit-heading">
                <div>
                  <strong>선적 상세</strong>
                  <span>ETD·선사·선박을 Shipment별로 저장합니다.</span>
                </div>
                <button
                  type="button"
                  className="small-button neutral"
                  onClick={() =>
                    setEditForm({
                      ...editForm,
                      shipment_details: [
                        ...editForm.shipment_details,
                        {
                          quantity: 1,
                          status: "예정",
                          shipment_date: null,
                          carrier: null,
                          vessel_name: null,
                        },
                      ],
                    })
                  }
                >
                  Shipment 추가
                </button>
              </div>
              {editForm.shipment_details.map((row, index) => (
                <div
                  className="detail-edit-row shipment"
                  key={row.id ?? `new-shipment-${index}`}
                >
                  <select
                    value={row.status}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        shipment_details: editForm.shipment_details.map(
                          (item, i) =>
                            i === index
                              ? { ...item, status: event.target.value }
                              : item,
                        ),
                      })
                    }
                  >
                    <option>예정</option>
                    <option>실제 선적</option>
                    <option>검토 필요</option>
                  </select>
                  <input
                    aria-label="선적 수량"
                    type="number"
                    min="1"
                    value={row.quantity}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        shipment_details: editForm.shipment_details.map(
                          (item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  quantity: Number(event.target.value),
                                }
                              : item,
                        ),
                      })
                    }
                  />
                  <input
                    aria-label="ETD"
                    type="date"
                    value={row.shipment_date ?? ""}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        shipment_details: editForm.shipment_details.map(
                          (item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  shipment_date: event.target.value || null,
                                }
                              : item,
                        ),
                      })
                    }
                  />
                  <input
                    aria-label="선사"
                    placeholder="선사"
                    value={row.carrier ?? ""}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        shipment_details: editForm.shipment_details.map(
                          (item, i) =>
                            i === index
                              ? { ...item, carrier: event.target.value || null }
                              : item,
                        ),
                      })
                    }
                  />
                  <input
                    aria-label="선박"
                    placeholder="선박"
                    value={row.vessel_name ?? ""}
                    onChange={(event) =>
                      setEditForm({
                        ...editForm,
                        shipment_details: editForm.shipment_details.map(
                          (item, i) =>
                            i === index
                              ? {
                                  ...item,
                                  vessel_name: event.target.value || null,
                                }
                              : item,
                        ),
                      })
                    }
                  />
                </div>
              ))}
            </section>
          </div>
          <div className="order-edit-actions">
            <span>
              기존 원본은 보존하고 수동 수정 이력을 별도로 기록합니다.
            </span>
            <button className="primary-button" disabled={saving}>
              {saving ? "저장 중…" : "수동 수정값 저장"}
            </button>
          </div>
        </form>
      )}
      {proposalOrder && (
        <section className="panel update-proposal-panel">
          <div className="panel-heading">
            <div>
              <h2>{proposalOrder.order_number} 업데이트 검토</h2>
              <p>
                현재값과 새 파일 값을 비교해 바꿀 항목만 선택하세요. 선택하지
                않은 수동값은 유지됩니다.
              </p>
            </div>
            <button
              className="small-button neutral"
              onClick={() => {
                setProposalOrder(null);
                setProposals([]);
              }}
            >
              닫기
            </button>
          </div>
          {proposals.length ? (
            proposals.map((proposal) => (
              <div className="proposal-card" key={proposal.id}>
                <div className="proposal-source">
                  <strong>{proposal.source_name}</strong>
                  <span>
                    {new Date(proposal.created_at).toLocaleString("ko-KR")}
                  </span>
                </div>
                {proposal.match_basis && (
                  <div className="proposal-ai-basis">
                    <strong>AI 동일 물량 후보 · {proposal.match_basis.confidence}</strong>
                    <span>{proposal.match_basis.reason}</span>
                    {proposal.match_basis.signals.length > 0 && (
                      <small>{proposal.match_basis.signals.join(" · ")}</small>
                    )}
                    <em>추천 근거이며 자동 반영되지 않습니다.</em>
                  </div>
                )}
                <div className="proposal-differences">
                  {Object.entries(proposal.differences).map(
                    ([field, difference]) => (
                      <label key={field}>
                        <input
                          type="checkbox"
                          checked={Boolean(
                            proposalFields[`${proposal.id}:${field}`],
                          )}
                          onChange={(event) =>
                            setProposalFields((current) => ({
                              ...current,
                              [`${proposal.id}:${field}`]: event.target.checked,
                            }))
                          }
                        />
                        <span>
                          <b>{fieldLabels[field] ?? field}</b>
                          {difference.manual_priority && <em>수동값 우선</em>}
                        </span>
                        <span>
                          현재: {String(difference.current ?? "비어 있음")}
                        </span>
                        <span>
                          새 파일: {String(difference.incoming ?? "비어 있음")}
                        </span>
                      </label>
                    ),
                  )}
                </div>
                <div className="proposal-actions">
                  <button
                    className="primary-button"
                    disabled={
                      saving ||
                      !Object.keys(proposal.differences).some(
                        (field) => proposalFields[`${proposal.id}:${field}`],
                      )
                    }
                    onClick={() => void applyProposal(proposal)}
                  >
                    선택 항목 업데이트
                  </button>
                </div>
              </div>
            ))
          ) : (
            <p className="empty-copy">검토 대기 중인 업데이트가 없습니다.</p>
          )}
        </section>
      )}
      {erpOrderId &&
        (() => {
          const order = orders.find((item) => item.id === erpOrderId);
          return order ? (
            <form className="panel erp-confirm-panel" onSubmit={submitClosed}>
              <div>
                <strong>
                  {order.order_number} · PO {order.po_number}
                </strong>
                <p>
                  현재 Closed {integer.format(order.closed_quantity)} / 주문{" "}
                  {integer.format(order.quantity)}ea
                </p>
              </div>
              <label>
                처리 상태
                <select
                  value={erpStatus}
                  onChange={(event) => setErpStatus(event.target.value)}
                >
                  <option>일부 완료</option>
                  <option>전체 완료</option>
                  <option>취소·정정</option>
                </select>
              </label>
              <label>
                이번 확인 수량(ea)
                <input
                  type="number"
                  min="0"
                  max={Math.max(order.quantity - order.closed_quantity, 0)}
                  required
                  value={erpQuantity}
                  onChange={(event) => setErpQuantity(event.target.value)}
                />
              </label>
              <label>
                확인일
                <input
                  type="date"
                  value={erpDate}
                  onChange={(event) => setErpDate(event.target.value)}
                />
              </label>
              <label>
                근거 메모
                <input
                  value={erpNote}
                  placeholder="ERP 조회 근거"
                  onChange={(event) => setErpNote(event.target.value)}
                />
              </label>
              <div className="erp-confirm-actions">
                <button
                  className="small-button neutral"
                  type="button"
                  onClick={() => setErpOrderId("")}
                >
                  취소
                </button>
                <button className="primary-button" disabled={saving}>
                  {saving ? "저장 중…" : "Closed 저장"}
                </button>
              </div>
            </form>
          ) : null;
        })()}
      <section className="panel orders-panel">
        <div className="panel-heading sales-panel-heading">
          <div>
            <h2>주문 목록</h2>
            <p>
              필터 결과 {integer.format(filtered.length)}건 · 합계는 목록 맨
              위에서 확인합니다.
            </p>
          </div>
          <div className="panel-actions">
            <button
              className="small-button neutral"
              type="button"
              onClick={() => setFilters(emptyFilters)}
            >
              필터 초기화
            </button>
            <a
              className="excel-button"
              href="/api/backend/api/exports/sales-master.xlsx"
              download
            >
              Excel 다운로드
            </a>
          </div>
        </div>
        <div className="table-wrap sales-table-wrap">
          <table>
            <thead>
              <tr>
                <th>SO</th>
                <th>PO</th>
                <th>판매법인</th>
                <th>모델</th>
                <th>고객</th>
                <th>대상 월</th>
                <th className="numeric">주문 수량(ea)</th>
                <th className="numeric">Unit Price (USD)</th>
                <th>매칭 검토 필요</th>
                <th>RISK</th>
                <th>생산법인</th>
                <th>PST · 생산 진행</th>
                <th>ETD · 선적 진행</th>
                <th className="numeric">Net Sales (USD)</th>
                <th className="numeric">Gross Sales (USD)</th>
                <th>Status</th>
                <th>Closed</th>
                <th>자료 출처</th>
                <th>관리</th>
              </tr>
              <tr className="column-filters">
                <th>
                  <FilterSelect
                    label="SO"
                    value={filters.so}
                    options={options.so}
                    onChange={(value) => setFilter("so", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="PO"
                    value={filters.po}
                    options={options.po}
                    onChange={(value) => setFilter("po", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="판매법인"
                    value={filters.company}
                    options={options.company}
                    onChange={(value) => setFilter("company", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="모델"
                    value={filters.model}
                    options={options.model}
                    onChange={(value) => setFilter("model", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="고객"
                    value={filters.customer}
                    options={options.customer}
                    onChange={(value) => setFilter("customer", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="대상 월"
                    value={filters.month}
                    options={options.month}
                    onChange={(value) => setFilter("month", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="주문 수량"
                    value={filters.quantity}
                    options={options.quantity}
                    onChange={(value) => setFilter("quantity", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="Unit Price"
                    value={filters.unitPrice}
                    options={options.unitPrice}
                    onChange={(value) => setFilter("unitPrice", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="매칭 검토"
                    value={filters.review}
                    options={options.review}
                    onChange={(value) => setFilter("review", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="RISK"
                    value={filters.risk}
                    options={options.risk}
                    onChange={(value) => setFilter("risk", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="생산법인"
                    value={filters.plant}
                    options={options.plant}
                    onChange={(value) => setFilter("plant", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="PST"
                    value={filters.production}
                    options={options.production}
                    onChange={(value) => setFilter("production", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="ETD"
                    value={filters.shipment}
                    options={options.shipment}
                    onChange={(value) => setFilter("shipment", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="Net"
                    value={filters.net}
                    options={options.net}
                    onChange={(value) => setFilter("net", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="Gross"
                    value={filters.gross}
                    options={options.gross}
                    onChange={(value) => setFilter("gross", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="Status"
                    value={filters.status}
                    options={options.status}
                    onChange={(value) => setFilter("status", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="Closed"
                    value={filters.closed}
                    options={options.closed}
                    onChange={(value) => setFilter("closed", value)}
                  />
                </th>
                <th>
                  <FilterSelect
                    label="자료 출처"
                    value={filters.source}
                    options={options.source}
                    onChange={(value) => setFilter("source", value)}
                  />
                </th>
                <th>
                  <span className="filter-static">수정·검토</span>
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="sum-row">
                <td>
                  <strong>합계 ({integer.format(filtered.length)}건)</strong>
                </td>
                <td colSpan={5}></td>
                <td className="numeric">
                  <strong>{integer.format(totals.quantity)}</strong>
                </td>
                <td className="numeric">—</td>
                <td className="numeric">
                  검토 {integer.format(totals.review)}ea
                </td>
                <td className="numeric">
                  위험 {integer.format(totals.risk)}ea
                </td>
                <td className="numeric">
                  <strong>완료 {integer.format(totals.production)}ea</strong>
                </td>
                <td className="numeric">
                  <strong>완료 {integer.format(totals.shipment)}ea</strong>
                </td>
                <td></td>
                <td className="numeric">
                  <strong>{usd.format(totals.net)}</strong>
                </td>
                <td className="numeric">
                  <strong>{usd.format(totals.gross)}</strong>
                </td>
                <td colSpan={4}></td>
              </tr>
              {filtered.map((order) => {
                const state = stateLabel(order);
                const matchingReview = reviewLabel(order) === "필요";
                return (
                  <tr key={order.id}>
                    <td>
                      <strong className="order-ref">
                        {order.order_number}
                      </strong>
                      {order.manual_fields.length > 0 && (
                        <span className="manual-badge">수동 우선</span>
                      )}
                    </td>
                    <td>
                      <strong>{order.po_number}</strong>
                      {order.po_line_number && (
                        <span className="cell-secondary">
                          라인 {order.po_line_number}
                        </span>
                      )}
                    </td>
                    <td>{order.sales_company}</td>
                    <td>
                      <strong>{order.model}</strong>
                    </td>
                    <td>{order.customer}</td>
                    <td>{order.target_month}</td>
                    <td className="numeric">
                      <strong>{integer.format(order.quantity)}</strong>
                    </td>
                    <td className="numeric">
                      <strong>{usd.format(order.unit_price)}</strong>
                    </td>
                    <td>
                      <span
                        className={`pill ${matchingReview ? "amber" : "gray"}`}
                      >
                        <i />
                        {matchingReview
                          ? `필요 ${integer.format(order.review_quantity)}ea`
                          : "불필요"}
                      </span>
                    </td>
                    <td>
                      {order.risk_status === "확인 필요" ? (
                        <details className="risk-detail">
                          <summary>
                            <span className="pill red">
                              <i />
                              확인 필요 {integer.format(order.risk_quantity)}ea
                            </span>
                          </summary>
                          {order.risk_reasons.map((reason) => (
                            <p key={reason}>{reason}</p>
                          ))}
                        </details>
                      ) : (
                        <span className="pill green">
                          <i />
                          현재 위험 없음
                        </span>
                      )}
                    </td>
                    <td>
                      <strong>{order.production_company}</strong>
                    </td>
                    <td>
                      <ScheduleCell order={order} stage="production" />
                    </td>
                    <td>
                      <ScheduleCell order={order} stage="shipment" />
                    </td>
                    <td className="numeric">
                      <strong>
                        {order.net_sales_amount === null
                          ? "—"
                          : usd.format(order.net_sales_amount)}
                      </strong>
                    </td>
                    <td className="numeric">
                      <strong>
                        {order.gross_sales_amount === null
                          ? "—"
                          : usd.format(order.gross_sales_amount)}
                      </strong>
                    </td>
                    <td>
                      <span className={`pill ${state.type}`}>
                        <i />
                        {state.label}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`pill ${order.closed_quantity ? "teal" : "gray"}`}
                      >
                        <i />
                        {order.erp_status}
                      </span>
                      <span className="cell-secondary">
                        {integer.format(order.closed_quantity)}ea
                      </span>
                      <button
                        className="text-link closed-entry"
                        type="button"
                        onClick={() => {
                          setErpOrderId(order.id);
                          setErpStatus(
                            order.closed_quantity ? "일부 완료" : "일부 완료",
                          );
                          setErpQuantity(
                            String(
                              Math.min(
                                Math.max(
                                  order.quantity - order.closed_quantity,
                                  0,
                                ),
                                1,
                              ),
                            ),
                          );
                        }}
                      >
                        Closed 입력
                      </button>
                    </td>
                    <td>
                      {order.source_download_url ? (
                        <a
                          className="source-link"
                          href={`/api/backend${order.source_download_url}`}
                          download
                        >
                          {order.source_name}
                          <span>원본 파일 ↓</span>
                        </a>
                      ) : (
                        <>
                          <span className="source-text">
                            {order.source_name}
                          </span>
                          <span className="cell-secondary">원본 파일 없음</span>
                        </>
                      )}
                    </td>
                    <td>
                      <div className="row-actions">
                        <button
                          className="small-button neutral"
                          type="button"
                          onClick={() => {
                            setEditingOrder(order);
                            setEditForm(editValues(order));
                            setProposalOrder(null);
                          }}
                        >
                          수정
                        </button>
                        {order.pending_update_count > 0 && (
                          <button
                            className="review-update-button"
                            type="button"
                            onClick={() => void openProposals(order)}
                          >
                            업데이트 검토 {order.pending_update_count}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
              {!loading && !error && !filtered.length && (
                <tr>
                  <td className="loading-cell" colSpan={20}>
                    선택한 조건에 맞는 주문이 없습니다.
                  </td>
                </tr>
              )}
              {loading && (
                <tr>
                  <td className="loading-cell" colSpan={20}>
                    서버 데이터 불러오는 중…
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="panel-foot sales-panel-foot">
          <span>
            이월 가능 = 완료수량과 예정수량을 합쳐도 주문수량에 못 미치는 수량
          </span>
          <span>금액·수량은 주문별 한 번만 합산</span>
        </div>
      </section>
      <section className="form-section">
        <div className="section-title">
          <div>
            <h2>테스트 주문 저장</h2>
            <p>합성 주문 한 건을 저장하고 서버에서 다시 조회합니다.</p>
          </div>
          <span className="pill blue">
            <i />
            로컬 데모 저장
          </span>
        </div>
        <form className="panel demo-form" onSubmit={submit}>
          <div className="form-grid">
            <label>
              판매법인
              <select
                required
                value={form.sales_company_id}
                onChange={(e) =>
                  setForm({ ...form, sales_company_id: e.target.value })
                }
              >
                {companies
                  .filter((c) => c.kind === "판매법인")
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              생산법인
              <select
                required
                value={form.production_company_id}
                onChange={(e) =>
                  setForm({ ...form, production_company_id: e.target.value })
                }
              >
                {companies
                  .filter((c) => c.kind === "생산법인")
                  .map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
              </select>
            </label>
            <label>
              대상 월
              <input
                type="month"
                required
                value={form.target_month}
                onChange={(e) =>
                  setForm({ ...form, target_month: e.target.value })
                }
              />
            </label>
            <label>
              PO 번호
              <input
                required
                maxLength={80}
                placeholder="00001234"
                value={form.po_number}
                onChange={(e) =>
                  setForm({ ...form, po_number: e.target.value })
                }
              />
            </label>
            <label>
              모델
              <input
                required
                maxLength={100}
                value={form.model}
                onChange={(e) => setForm({ ...form, model: e.target.value })}
              />
            </label>
            <label>
              고객
              <input
                required
                maxLength={120}
                value={form.customer}
                onChange={(e) => setForm({ ...form, customer: e.target.value })}
              />
            </label>
            <label>
              수량
              <input
                type="number"
                min="1"
                required
                value={form.quantity}
                onChange={(e) => setForm({ ...form, quantity: e.target.value })}
              />
            </label>
            <label>
              통화
              <select value="USD" disabled>
                <option>USD</option>
              </select>
            </label>
            <label>
              Net 단가 (USD)
              <input
                type="number"
                min="0"
                step="0.01"
                required
                value={form.unit_price}
                onChange={(e) =>
                  setForm({ ...form, unit_price: e.target.value })
                }
              />
            </label>
          </div>
          <div className="form-actions">
            <p>생산·선적 일정이 없는 새 주문은 일정 미정으로 표시됩니다.</p>
            <button className="primary-button" type="submit" disabled={saving}>
              {saving ? "저장 중…" : "데모 주문 저장"}
            </button>
          </div>
        </form>
      </section>
    </>
  );
}
