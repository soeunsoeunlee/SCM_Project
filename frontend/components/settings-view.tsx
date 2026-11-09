"use client";

// 외부 서비스가 실제로 연결되었는지 과장하지 않고 현재 운영 상태를 보여 줍니다.
import { useEffect, useState } from "react";
import Link from "next/link";
import { getIntegrationStatus, IntegrationStatus } from "@/lib/api";

export function SettingsView() {
  const [data, setData] = useState<IntegrationStatus | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { getIntegrationStatus().then(setData).catch(reason => setError(reason instanceof Error ? reason.message : "연동 상태를 불러오지 못했습니다.")); }, []);
  return <>
    <section className="page-heading"><div><div className="breadcrumb"><Link href="/">업무 현황</Link><span>/</span>연동 및 설정</div><div className="heading-line"><h1>연동 및 설정</h1><span className="status-chip">● 상태 확인</span></div><p className="heading-subtitle">현재 사용할 수 있는 입력 방식과 외부 연결 여부를 한눈에 확인합니다.</p></div></section>
    {error && <div className="error-banner" role="alert">{error}</div>}
    <section className="panel settings-panel"><div className="panel-heading"><div><h2>자료·시스템 연결 상태</h2><p>연결되지 않은 서비스는 데이터를 읽거나 보내지 않습니다.</p></div></div><div className="connector-grid">{data?.connectors.map(connector => <article key={connector.name}><div><strong>{connector.name}</strong><span className={`connector-status ${connector.status === "사용 가능" ? "available" : connector.status === "연결 안 됨" ? "off" : "manual"}`}>{connector.status}</span></div><p>{connector.detail}</p></article>) ?? <p className="loading-cell">상태를 불러오는 중…</p>}</div></section>
    <section className="panel security-panel"><div className="panel-heading"><div><h2>보안·운영 원칙</h2><p>외부 연동을 시작하기 전에 반드시 지키는 기준입니다.</p></div></div><ul>{data?.security.map(item => <li key={item}>✓ {item}</li>)}</ul><div className="panel-foot">OpenAI는 설정된 경우 컬럼과 주문 후보를 추천하며 자동 확정 권한은 갖지 않습니다. Gmail OAuth와 실제 ERP API는 아직 연결되지 않았습니다.</div></section>
  </>;
}
