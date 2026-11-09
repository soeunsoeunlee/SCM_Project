"use client";

// 아직 구현 중인 PSI·변경 이력·설정 메뉴에 역할과 향후 범위를 안내합니다.

import Link from "next/link";
import { usePathname } from "next/navigation";
import { PsiView } from "@/components/psi-view";
import { HistoryView } from "@/components/history-view";
import { SettingsView } from "@/components/settings-view";

// URL 첫 부분(slug)을 화면 제목과 설명에 연결합니다.
const sections: Record<string, { title: string; lead: string; purpose: string; later: string[] }> = {
  psi: { title: "PSI", lead: "주차별 수요·판매계획과 실제 물량을 비교하는 공간입니다.", purpose: "DM·SA·SP와 실제 실적은 서로 다른 값으로 보존하여 비교합니다.", later: ["주차별 DM·SA·SP 비교", "PSI 버전 변경 추적", "SP 대비 실제 선적 달성률"] },
  history: { title: "변경 이력", lead: "판매계획 버전 및 주문 정보를 수정한 내역을 추적하는 공간입니다.", purpose: "변경 전후 값과 원본 출처·기준일을 함께 관리합니다.", later: ["필드별 변경 전후 기록", "원본 자료 및 처리 시각 확인", "생산·선적과 충돌하는 변경 안내"] },
  settings: { title: "연동 및 설정", lead: "향후 자료 처리와 외부 연동 규칙을 관리하는 공간입니다.", purpose: "실제 연동을 시작하기 전 보안과 운영 기준을 먼저 정의합니다.", later: ["판매·생산 법인별 컬럼 매핑", "승인된 별칭과 위험 기준", "Gmail·실 ERP는 이후 별도 인증 구성"] },
};

export default function ComingSoonPage() {
  // 현재 주소에서 메뉴 키를 읽어 해당 준비 중 설명을 선택합니다.
  const pathname = usePathname();
  const slug = pathname.split("/").filter(Boolean)[0] ?? "";
  if (slug === "psi") return <PsiView />;
  if (slug === "history") return <HistoryView />;
  if (slug === "settings") return <SettingsView />;
  const section = sections[slug];
  if (!section) return <div className="empty-panel"><h1>페이지를 찾지 못했어요</h1><p>메뉴에서 이동할 수 있는 페이지를 선택해 주세요.</p><Link className="primary-button link-button" href="/">대시보드로 이동</Link></div>;

  return <>
    <div className="demo-banner"><span className="demo-banner-icon">✦</span><div><strong>합성 데이터 데모입니다.</strong> 실제 파일 업로드나 외부 서비스 연동은 진행하지 않습니다.</div><span className="demo-banner-tag">SYNTHETIC DATA</span></div>
    <section className="page-heading"><div><div className="breadcrumb"><Link href="/">업무 현황</Link><span>/</span>{section.title}</div><div className="heading-line"><h1>{section.title}</h1><span className="pill amber"><i />준비 중</span></div><p className="heading-subtitle">{section.lead}</p></div></section>
    <div className="comingsoon-layout"><section className="panel purpose-panel"><span className="purpose-mark">{slug === "history" ? "◷" : slug === "settings" ? "⚙" : "✦"}</span><div className="comingsoon-badge"><span /> 다음 구현 단계</div><h2>{section.title}는 아직 동작하지 않아요.</h2><p>{section.purpose}</p><ul>{section.later.map((item) => <li key={item}><span>○</span>{item}</li>)}</ul><Link className="text-link back-link" href="/sales-master">현재 사용 가능한 Sales Master 보기 →</Link></section><aside className="panel scope-panel"><span className="scope-icon">ⓘ</span><h2>이번 단계 범위</h2><p>현재 사용할 수 있는 데이터 기능은 합성 주문 조회와 데모 주문 저장입니다. 버튼을 누르면 아무 일도 하지 않는 화면 대신 구현 범위를 안내하고 있습니다.</p><div className="scope-tag">실제 Gmail 연결 없음</div><div className="scope-tag">실제 ERP 연결 없음</div><div className="scope-tag">자동 매칭 미구현</div></aside></div>
  </>;
}
