"use client";

// 판매법인별 매출 흐름을 한 표에서 비교하고 금액을 누르면 주문 상세를 펼칩니다.
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Dashboard, getDashboard, getOrders, Order } from "@/lib/api";

const usd = new Intl.NumberFormat("ko-KR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
type Metric = "order" | "shipping" | "completed" | "pending" | "forecast";
const labels: Record<Metric, string> = { order: "주문금액", shipping: "배송중 금액", completed: "매출완료금액", pending: "매출 대기", forecast: "매출예상금액" };

function amount(order: Order) { return order.net_sales_amount ?? order.total_amount; }
function proportional(order: Order, quantity: number) { return order.quantity ? amount(order) * Math.min(quantity, order.quantity) / order.quantity : 0; }
function hasShipmentSchedule(order: Order) { return order.shipment_details.some(detail => detail.status === "예정" || detail.status === "실제 선적"); }
function values(order: Order) {
  const completedQuantity = Math.min(order.closed_quantity, order.quantity);
  const shippingQuantity = Math.max(Math.min(order.shipment_quantity, order.quantity) - completedQuantity, 0);
  const completed = proportional(order, completedQuantity);
  return { order: amount(order), shipping: proportional(order, shippingQuantity), completed, pending: Math.max(amount(order) - completed, 0), forecast: hasShipmentSchedule(order) ? amount(order) : 0 };
}

export function DashboardView() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [month, setMonth] = useState("");
  const [company, setCompany] = useState("");
  const [active, setActive] = useState<{ company: string; metric: Metric } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { Promise.all([getOrders(), getDashboard()]).then(([rows, data]) => { setOrders(rows); setDashboard(data); }).catch(reason => setError(reason instanceof Error ? reason.message : "서버에 연결할 수 없습니다.")); }, []);
  const months = useMemo(() => [...new Set(orders.map(order => order.target_month))].sort(), [orders]);
  const salesCompanies = useMemo(() => (dashboard?.companies ?? []).filter(item => item.kind === "판매법인"), [dashboard]);
  const scoped = useMemo(() => orders.filter(order => (!month || order.target_month === month) && (!company || order.sales_company_id === company)), [orders, month, company]);
  const rows = useMemo(() => salesCompanies.filter(item => !company || item.id === company).map(item => {
    const companyOrders = scoped.filter(order => order.sales_company_id === item.id);
    const totals = companyOrders.reduce((sum, order) => { const current = values(order); (Object.keys(sum) as Metric[]).forEach(key => sum[key] += current[key]); return sum; }, { order: 0, shipping: 0, completed: 0, pending: 0, forecast: 0 });
    return { ...item, orders: companyOrders, totals };
  }), [salesCompanies, scoped, company]);
  const grand = useMemo(() => rows.reduce((sum, row) => { (Object.keys(sum) as Metric[]).forEach(key => sum[key] += row.totals[key]); return sum; }, { order: 0, shipping: 0, completed: 0, pending: 0, forecast: 0 }), [rows]);
  const detailOrders = useMemo(() => {
    if (!active) return [];
    return scoped.filter(order => order.sales_company_id === active.company).filter(order => {
      const current = values(order);
      return active.metric === "order" || current[active.metric] > 0;
    });
  }, [active, scoped]);

  function toggle(companyId: string, metric: Metric) { setActive(current => current?.company === companyId && current.metric === metric ? null : { company: companyId, metric }); }

  return <>
    <div className="demo-banner"><span className="demo-banner-icon">ⓘ</span><div><strong>합성 데이터 데모입니다.</strong> 금액은 Net Sales USD 기준이며 실제 환율·ERP 연동 결과가 아닙니다.</div><span className="demo-banner-tag">SYNTHETIC DATA</span></div>
    <section className="page-heading"><div><div className="breadcrumb">SCM Control Tower<span>/</span>업무 현황</div><div className="heading-line"><h1>판매법인별 매출 현황</h1><span className="status-chip">● 서버 연결</span></div><p className="heading-subtitle">금액 버튼을 누르면 해당 기준에 포함된 주문만 바로 확인할 수 있습니다.</p></div><Link className="outline-button" href="/sales-master">Sales Master 보기 →</Link></section>
    {error && <div className="error-banner" role="alert">서버 요청에 실패했습니다: {error}</div>}
    <section className="panel dashboard-summary"><div className="panel-heading dashboard-filter-heading"><div><h2>법인별 금액 요약</h2><p>매출 대기 = 주문금액 − 매출완료금액 · 매출예상 = 선적 완료 또는 선적 예정이 있는 주문의 전체 금액</p></div><div className="dashboard-filters"><label>판매법인<select value={company} onChange={event => { setCompany(event.target.value); setActive(null); }}><option value="">전체</option>{salesCompanies.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><label>대상 월<select value={month} onChange={event => { setMonth(event.target.value); setActive(null); }}><option value="">전체</option>{months.map(item => <option key={item}>{item}</option>)}</select></label><a className="excel-button" href="/api/backend/api/exports/dashboard.xlsx" download>Excel 다운로드</a></div></div>
      <div className="table-wrap dashboard-table-wrap"><table><thead><tr><th>판매법인</th>{(Object.keys(labels) as Metric[]).map(metric => <th className="numeric" key={metric}>{labels[metric]} (USD)</th>)}</tr></thead><tbody><tr className="sum-row dashboard-total-row"><td><strong>전체 합계</strong></td>{(Object.keys(labels) as Metric[]).map(metric => <td className="numeric" key={metric}><strong>{usd.format(grand[metric])}</strong></td>)}</tr>{rows.map(row => <tr key={row.id}><td><strong>{row.name}</strong><span className="cell-secondary">{row.orders.length}건</span></td>{(Object.keys(labels) as Metric[]).map(metric => <td className="numeric" key={metric}><button className={`metric-toggle ${active?.company === row.id && active.metric === metric ? "active" : ""}`} onClick={() => toggle(row.id, metric)}>{usd.format(row.totals[metric])}</button></td>)}</tr>)}</tbody></table></div>
      <div className="panel-foot"><span>배송중 금액은 실제 선적 수량 중 아직 Closed되지 않은 수량의 비례 금액입니다.</span><span>주문 금액은 생산·선적 상세 때문에 중복 합산되지 않습니다.</span></div>
    </section>
    {active && <section className="panel dashboard-detail"><div className="panel-heading"><div><h2>{salesCompanies.find(item => item.id === active.company)?.name} · {labels[active.metric]} 상세</h2><p>{detailOrders.length}개 주문이 현재 기준에 포함됩니다.</p></div><button className="small-button neutral" onClick={() => setActive(null)}>상세 닫기</button></div><div className="table-wrap"><table><thead><tr><th>SO</th><th>PO</th><th>모델 · 고객</th><th>대상 월</th><th className="numeric">주문금액 (USD)</th><th className="numeric">선적 / Closed (ea)</th><th>Status</th></tr></thead><tbody>{detailOrders.map(order => <tr key={order.id}><td><strong className="order-ref">{order.order_number}</strong></td><td>{order.po_number}</td><td><strong>{order.model}</strong><span className="cell-secondary">{order.customer}</span></td><td>{order.target_month}</td><td className="numeric"><strong>{usd.format(amount(order))}</strong></td><td className="numeric">{order.shipment_quantity} / {order.closed_quantity}</td><td>{order.match_status}</td></tr>)}</tbody></table></div></section>}
    <div className="bottom-note"><span>ⓘ</span>매출완료는 ERP 확인 수량에 비례해 계산합니다. ERP 확인이 없는 주문은 선적됐더라도 매출 대기에 남습니다.</div>
  </>;
}
