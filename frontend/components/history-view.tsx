"use client";

// 주문과 PSI 검토 과정에서 실제로 저장된 변경 기록을 조회합니다.
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getHistory, HistoryItem } from "@/lib/api";

export function HistoryView() {
  const [rows, setRows] = useState<HistoryItem[]>([]);
  const [eventType, setEventType] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => { getHistory().then(setRows).catch(reason => setError(reason instanceof Error ? reason.message : "변경 이력을 불러오지 못했습니다.")).finally(() => setLoading(false)); }, []);
  const types = useMemo(() => [...new Set(rows.map(row => row.event_type))].sort(), [rows]);
  const filtered = useMemo(() => rows.filter(row => !eventType || row.event_type === eventType), [rows, eventType]);
  return <>
    <section className="page-heading"><div><div className="breadcrumb"><Link href="/">업무 현황</Link><span>/</span>변경 이력</div><div className="heading-line"><h1>변경 이력</h1><span className="status-chip">● 감사 기록</span></div><p className="heading-subtitle">Closed 확인과 PSI RISK 배분의 변경 전후 값, 처리 시각, 근거 출처를 확인합니다.</p></div></section>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="panel history-panel"><div className="panel-heading"><div><h2>저장된 업무 변경</h2><p>현재 프로젝트 기준 {filtered.length}건</p></div><div className="panel-actions"><label className="compact-filter">구분<select value={eventType} onChange={event => setEventType(event.target.value)}><option value="">전체</option>{types.map(type => <option key={type}>{type}</option>)}</select></label><a className="excel-button" href="/api/backend/api/history.xlsx" download>Excel 다운로드</a></div></div><div className="table-wrap"><table><thead><tr><th>처리 시각</th><th>구분</th><th>SO / PO</th><th>모델</th><th>변경 전</th><th>변경 후</th><th>자료 출처</th></tr></thead><tbody>{filtered.map(row => <tr key={row.id}><td>{new Date(row.changed_at).toLocaleString("ko-KR")}</td><td><strong>{row.event_type}</strong></td><td><strong>{row.order_number}</strong><span className="cell-secondary">PO {row.po_number}</span></td><td>{row.model}</td><td>{row.before_value ?? "—"}</td><td>{row.after_value ?? "—"}</td><td>{row.source_name}</td></tr>)}{loading && <tr><td className="loading-cell" colSpan={7}>변경 이력을 불러오는 중…</td></tr>}{!loading && !filtered.length && <tr><td className="loading-cell" colSpan={7}>아직 저장된 변경 이력이 없습니다.</td></tr>}</tbody></table></div></section>
  </>;
}
