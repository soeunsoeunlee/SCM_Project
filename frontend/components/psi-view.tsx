"use client";

// PSI 원본 업로드, 버전 비교, 검토 승인과 12개월 주차 표를 한 화면에서 제공합니다.
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { approvePsiVersion, decidePsiRisk, deletePsiVersion, getPsi, getPsiVersion, getPsiVersions, PsiData, PsiVersionDetail, PsiVersionSummary, uploadPsiVersion } from "@/lib/api";

const integer = new Intl.NumberFormat("ko-KR");
const metricLabels: Record<string, string> = { DM: "DM 판매계획", SA: "SA 판매할당", SP: "SP 선적계획", Inventory: "예상 재고", Warehouse: "실제 창고" };

export function PsiView() {
  const [fallback, setFallback] = useState<PsiData | null>(null);
  const [versions, setVersions] = useState<PsiVersionSummary[]>([]);
  const [detail, setDetail] = useState<PsiVersionDetail | null>(null);
  const [company, setCompany] = useState("");
  const [model, setModel] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [riskAllocations, setRiskAllocations] = useState<Record<string, Record<string, string>>>({});
  const [riskNotes, setRiskNotes] = useState<Record<string, string>>({});

  // 버전이 있으면 최신 버전을, 없으면 기존 Sales Master 기반 PSI를 보여 줍니다.
  const refresh = useCallback(async (preferredId?: string) => {
    const [base, list] = await Promise.all([getPsi(), getPsiVersions()]);
    setFallback(base); setVersions(list);
    const id = preferredId ?? list[0]?.id;
    setDetail(id ? await getPsiVersion(id) : null);
  }, []);
  useEffect(() => { refresh().catch(reason => setError(reason instanceof Error ? reason.message : "PSI 자료를 불러오지 못했습니다.")); }, [refresh]);

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(""); setMessage("");
    try {
      const result = await uploadPsiVersion(new FormData(event.currentTarget));
      setDetail(result); setMessage(result.message ?? "PSI 버전을 저장했습니다.");
      await refresh(result.version.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "PSI 파일을 업로드하지 못했습니다."); }
    finally { setBusy(false); }
  }

  async function approve(versionId = detail?.version.id) {
    if (!versionId) return;
    setBusy(true); setError("");
    try { const result = await approvePsiVersion(versionId); setMessage(result.message); await refresh(versionId); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "PSI 버전을 승인하지 못했습니다."); }
    finally { setBusy(false); }
  }

  async function decideRisk(reviewId: string, decision: "승인" | "보류") {
    if (!detail) return;
    const review = detail.risk_reviews.find(item => item.id === reviewId);
    if (!review) return;
    const allocations = decision === "승인" ? review.candidates.map(order => ({
      order_id: order.id,
      quantity: Number(riskAllocations[reviewId]?.[order.id] ?? review.allocations.find(item => item.order_id === order.id)?.quantity ?? (review.candidates.length === 1 ? review.quantity : 0)),
    })).filter(item => item.quantity > 0) : [];
    setBusy(true); setError("");
    try {
      const result = await decidePsiRisk(reviewId, decision, allocations, riskNotes[reviewId]);
      setMessage(result.message); setDetail(await getPsiVersion(detail.version.id));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "RISK 검토 결과를 저장하지 못했습니다."); }
    finally { setBusy(false); }
  }

  async function removeVersion(version: PsiVersionSummary) {
    const confirmed = window.confirm(`삭제하면 복구할 수 없습니다.\n\n파일명: ${version.file_name}\n자료 유형: PSI\n기준일: ${version.reference_date}`);
    if (!confirmed) return;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await deletePsiVersion(version.id);
      setMessage(result.message);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "PSI 버전을 삭제하지 못했습니다."); }
    finally { setBusy(false); }
  }

  const data = detail?.psi ?? fallback;
  const rows = useMemo(() => (data?.rows ?? []).filter(row => (!company || row.sales_company === company) && (!model || row.model === model)), [data, company, model]);
  const companies = useMemo(() => [...new Set((data?.rows ?? []).map(row => row.sales_company))].sort(), [data]);
  const models = useMemo(() => [...new Set((data?.rows ?? []).map(row => row.model))].sort(), [data]);
  const activeVersion = useMemo(() => versions.find(version => version.status === "승인") ?? null, [versions]);
  const currentSummary = activeVersion ?? versions[0] ?? null;
  const monthGroups = useMemo(() => {
    const groups: { key: string; label: string; count: number }[] = [];
    for (const period of data?.periods ?? []) {
      const key = `${period.year}-${period.month}`; const last = groups[groups.length - 1];
      if (last?.key === key) last.count += 1; else groups.push({ key, label: `${period.year}년 ${period.month}월`, count: 1 });
    }
    return groups;
  }, [data]);
  const now = new Date(); const currentMonth = `${now.getFullYear()}-${now.getMonth() + 1}`;

  return <>
    <section className="page-heading"><div><div className="breadcrumb"><Link href="/">업무 현황</Link><span>/</span>PSI</div><div className="heading-line"><h1>주차별 PSI</h1><span className="status-chip">● 버전 관리</span></div><p className="heading-subtitle">판매법인·생산법인·모델별 DM, SA, SP, Inventory, Warehouse를 비교합니다.</p></div></section>
    {error && <div className="error-banner" role="alert">{error}</div>}{message && <div className="success-banner">{message}</div>}

    <section className="panel psi-upload-panel">
      <div className="panel-heading"><div><h2>신규 PSI 업로드</h2><p>파일명은 버전 이름으로, 자료 기준일의 연·월은 기준월로 자동 저장됩니다.</p></div></div>
      <form className="psi-upload-form" onSubmit={submitUpload}><label>자료 기준일 · 기준월 자동 산정<input name="reference_date" type="date" required defaultValue={new Date().toISOString().slice(0, 10)} /></label><label className="psi-file-field">PSI 파일 · 파일명이 버전 이름으로 저장됩니다<input name="file" type="file" accept=".xlsx" required /></label><button className="primary-button" disabled={busy}>{busy ? "처리 중…" : "PSI 업로드"}</button></form>
    </section>

    <section className="panel psi-current-panel"><div className="panel-heading"><div><h2>{activeVersion ? "현재 사용 PSI" : "최근 PSI 현황"}</h2><p>{activeVersion ? "가장 최근에 승인된 버전입니다." : currentSummary ? "아직 승인된 버전이 없어 최근 등록본을 표시합니다." : "등록된 PSI 파일이 없습니다."}</p></div>{currentSummary && <span className={`version-status status-${currentSummary.status.replace(" ", "-")}`}>{currentSummary.status}</span>}</div>{currentSummary ? <div className="psi-current-grid"><div><span>버전</span><strong>{currentSummary.label}</strong></div><div><span>기준월</span><strong>{currentSummary.base_month}</strong></div><div><span>자료 기준일</span><strong>{currentSummary.reference_date}</strong></div><div><span>등록자</span><strong>{currentSummary.created_by}</strong></div><div><span>등록일시</span><strong>{currentSummary.created_at ? new Date(currentSummary.created_at).toLocaleString("ko-KR") : "—"}</strong></div><div className="psi-current-actions"><button type="button" className="outline-button" onClick={() => getPsiVersion(currentSummary.id).then(setDetail).catch(reason => setError(String(reason)))}>상세 보기</button><a className="outline-button" href={`/api/backend/api/psi/versions/${currentSummary.id}/download`} download>원본 파일 ↓</a></div></div> : <p className="empty-copy">위에서 첫 PSI 파일을 업로드해 주세요.</p>}</section>

    {detail?.validation_issues.length ? <section className="panel psi-issues"><div className="panel-heading"><div><h2>검증 오류</h2><p>오류가 있는 파일은 비교하거나 승인하지 않습니다.</p></div></div><ul>{detail.validation_issues.map(issue => <li key={issue}>{issue}</li>)}</ul></section> : null}

    {detail && detail.version.status !== "검증 보류" && <section className="psi-review-grid">
      <article className="panel psi-compare-card"><div className="panel-heading"><div><h2>변경 내용</h2><p>{detail.previous_version ? `비교 기준: ${detail.previous_version.reference_date} · ${detail.previous_version.label} (승인본)` : "승인된 이전 PSI가 없어 최초 버전으로 표시합니다."}</p></div><div className="panel-actions"><span className={`version-status status-${detail.version.status.replace(" ", "-")}`}>{detail.version.status}</span><a className="text-link" href={`/api/backend/api/psi/versions/${detail.version.id}/download`} download>원본 파일 ↓</a>{detail.version.status === "검토 대기" && <button type="button" className="primary-button" onClick={() => void approve()} disabled={busy}>현재 버전 승인</button>}</div></div>{detail.comparison ? <><div className="psi-kpis"><span><b>{detail.comparison.changed_cells}</b>변경 셀</span><span><b>{detail.comparison.added_cells}</b>추가</span><span><b>{detail.comparison.removed_cells}</b>삭제</span><span><b>{detail.comparison.quantity_delta > 0 ? "+" : ""}{detail.comparison.quantity_delta}</b>순증감(ea)</span></div><div className="mini-table-wrap"><table><thead><tr><th>모델</th><th>구분</th><th>주차</th><th>이전</th><th>현재</th><th>증감</th></tr></thead><tbody>{detail.comparison.changes.slice(0, 12).map((item, index) => <tr key={`${item.model}-${item.metric}-${item.period_key}-${index}`}><td>{item.model}</td><td>{item.metric}</td><td>{item.period_key}</td><td>{item.before ?? "-"}</td><td>{item.after ?? "-"}</td><td className={item.delta < 0 ? "negative" : "positive"}>{item.delta > 0 ? "+" : ""}{item.delta}</td></tr>)}</tbody></table></div></> : <p className="empty-copy">이 버전을 승인한 뒤 다음 PSI를 업로드하면 승인본 대비 증감이 표시됩니다.</p>}</article>
      <article className="panel psi-risk-card"><div className="panel-heading"><div><h2>RISK 검토 신호</h2><p>기준 주차까지의 누적 DM→SA, SA→SP 부족분입니다.</p></div><strong>{detail.risk_signals.length}건</strong></div><div className="risk-signal-list">{detail.risk_signals.slice(0, 12).map((risk, index) => <div key={`${risk.model}-${risk.period_key}-${risk.risk_type}-${index}`}><span>{risk.risk_type}</span><strong>{risk.model} · {risk.quantity}ea</strong><small>{risk.period_key} · {risk.reason}</small></div>)}{detail.risk_signals.length === 0 && <p className="empty-copy">현재 규칙에서 확인할 부족 신호가 없습니다.</p>}</div><div className="panel-foot">이 신호는 검토 우선순위이며 주문 RISK 확정이나 자동 반영이 아닙니다.</div></article>
    </section>}

    <section className="panel psi-history-panel"><div className="panel-heading"><div><h2>PSI 버전 이력</h2><p>등록한 원본과 처리 상태를 확인하고, 원하는 버전의 표와 변경 내용을 다시 열 수 있습니다.</p></div><span className="history-count">{versions.length}건</span></div><div className="table-wrap"><table><thead><tr><th>등록일시</th><th>버전</th><th>기준월</th><th>자료 기준일</th><th>등록자</th><th>상태</th><th>원본</th><th>보기</th><th>관리</th></tr></thead><tbody>{versions.map(version => <tr key={version.id} className={detail?.version.id === version.id ? "selected-version-row" : ""}><td>{version.created_at ? new Date(version.created_at).toLocaleString("ko-KR") : "—"}</td><td><strong>{version.label}</strong>{activeVersion?.id === version.id && <span className="cell-secondary">현재 사용 중</span>}</td><td>{version.base_month}</td><td>{version.reference_date}</td><td>{version.created_by}</td><td><span className={`version-status status-${version.status.replace(" ", "-")}`}>{version.status}</span></td><td><a className="source-link" href={`/api/backend/api/psi/versions/${version.id}/download`} download>{version.file_name} ↓</a></td><td><button type="button" className="small-button neutral" disabled={busy} onClick={() => getPsiVersion(version.id).then(setDetail).catch(reason => setError(String(reason)))}>상세 보기</button></td><td><button type="button" className="danger-button" disabled={busy} onClick={() => void removeVersion(version)}>삭제</button></td></tr>)}{versions.length === 0 && <tr><td className="loading-cell" colSpan={9}>등록된 PSI 버전이 없습니다.</td></tr>}</tbody></table></div></section>

    {detail && detail.risk_reviews.length > 0 && <section className="panel psi-order-risk-panel"><div className="panel-heading"><div><h2>주문 RISK 수량 배분</h2><p>PSI 영향수량을 후보 주문 한 건 또는 여러 건에 나눕니다. 배분 합계가 영향수량과 같아야 승인됩니다.</p></div><span className="step-mark">검토 대기 {detail.risk_reviews.filter(item => item.status === "검토 대기").length}건</span></div><div className="table-wrap"><table className="psi-risk-review-table"><thead><tr><th>판매·생산법인</th><th>모델·신호</th><th>영향수량</th><th>주문별 배분</th><th>검토 메모</th><th>처리</th></tr></thead><tbody>{detail.risk_reviews.map(review => {
      const allocationValues = review.candidates.map(order => Number(riskAllocations[review.id]?.[order.id] ?? review.allocations.find(item => item.order_id === order.id)?.quantity ?? (review.candidates.length === 1 ? review.quantity : 0)));
      const allocationTotal = allocationValues.reduce((sum, value) => sum + (Number.isFinite(value) ? value : 0), 0);
      return <tr key={review.id}><td><strong>{review.sales_company}</strong><span className="cell-secondary">{review.production_company}</span></td><td><strong>{review.model}</strong><span className="cell-secondary">{review.risk_type} · {review.period_key}</span></td><td><strong>{review.quantity}ea</strong></td><td><div className="risk-allocation-list">{review.candidates.map((order, index) => <label key={order.id}><span>{order.order_number} · PO {order.po_number}<small>{order.target_month} · 주문 {order.quantity}ea</small></span><input type="number" min="0" max={order.quantity} step="1" aria-label={`${order.order_number} RISK 배분수량`} disabled={review.status !== "검토 대기"} value={allocationValues[index] || ""} placeholder="0" onChange={event => setRiskAllocations(current => ({ ...current, [review.id]: { ...current[review.id], [order.id]: event.target.value } }))} /></label>)}{review.candidates.length === 0 && <small>안전하게 연결할 후보 주문이 없습니다.</small>}<strong className={allocationTotal === review.quantity ? "allocation-ok" : "allocation-warning"}>배분 합계 {allocationTotal} / {review.quantity}ea</strong></div></td><td><input value={riskNotes[review.id] ?? review.review_note ?? ""} disabled={review.status !== "검토 대기"} placeholder="배분 근거 또는 보류 사유" onChange={event => setRiskNotes(current => ({ ...current, [review.id]: event.target.value }))} /></td><td>{review.status === "검토 대기" ? <div className="risk-review-actions"><button className="primary-button" disabled={busy || allocationTotal !== review.quantity} onClick={() => decideRisk(review.id, "승인")}>배분 승인</button><button className="small-button neutral" disabled={busy} onClick={() => decideRisk(review.id, "보류")}>보류</button></div> : <><span className={`version-status status-${review.status}`}>{review.status}</span>{review.allocations.length > 0 && <small className="cell-secondary">{review.allocations.length}개 주문에 배분</small>}</>}</td></tr>;
    })}</tbody></table></div></section>}

    <section className="panel psi-panel"><div className="panel-heading dashboard-filter-heading"><div><h2>모델별 PSI 표</h2><p>{detail ? `${detail.version.base_month} · ${detail.version.reference_date} · ${detail.version.label}` : "Sales Master 기반 초기 현황"}</p></div><div className="panel-actions psi-actions"><label>판매법인<select value={company} onChange={event => setCompany(event.target.value)}><option value="">전체</option>{companies.map(item => <option key={item}>{item}</option>)}</select></label><label>모델<select value={model} onChange={event => setModel(event.target.value)}><option value="">전체</option>{models.map(item => <option key={item}>{item}</option>)}</select></label><a className="excel-button" href={detail ? `/api/backend/api/psi/versions/${detail.version.id}/export` : "/api/backend/api/exports/psi.xlsx"} download>Excel 다운로드</a></div></div>
      <div className="psi-legend"><span className="metric-dm">DM 판매계획</span><span className="metric-sa">SA 판매할당</span><span className="metric-sp">SP 선적계획</span><span className="metric-inventory">예상 재고</span><span className="metric-warehouse">실제 창고</span></div>
      <div className="table-wrap psi-table-wrap"><table className="psi-table"><thead><tr><th className="sticky-col col-sales" rowSpan={2}>판매법인</th><th className="sticky-col col-plant" rowSpan={2}>생산법인</th><th className="sticky-col col-model" rowSpan={2}>모델</th><th className="sticky-col col-metric" rowSpan={2}>구분</th>{monthGroups.map(group => <th key={group.key} colSpan={group.count} className={group.key === currentMonth ? "current-period" : ""}>{group.label}</th>)}</tr><tr>{data?.periods.map(period => <th key={period.key} className={`${period.year}-${period.month}` === currentMonth ? "current-period" : ""}>{period.week}</th>)}</tr></thead><tbody>{rows.map((row, index) => <tr key={`${row.sales_company}-${row.production_company}-${row.model}-${row.metric}`} className={`psi-${row.metric.toLowerCase()} ${index % 5 === 4 ? "model-end" : ""}`}><td className="sticky-col col-sales">{row.sales_company}</td><td className="sticky-col col-plant">{row.production_company}</td><td className="sticky-col col-model"><strong>{row.model}</strong></td><td className="sticky-col col-metric"><strong>{row.metric}</strong><span>{metricLabels[row.metric]}</span></td>{data?.periods.map(period => <td key={period.key} className={`${period.year}-${period.month}` === currentMonth ? "current-period" : ""}>{row.values[period.key] ? integer.format(row.values[period.key]) : ""}</td>)}</tr>)}</tbody></table></div>
      <div className="panel-foot"><span>W1=1~7일, W2=8~14일 방식입니다.</span><span>DM·SA·SP는 서로 다른 단계로 합산하지 않습니다.</span></div>
    </section>
  </>;
}
