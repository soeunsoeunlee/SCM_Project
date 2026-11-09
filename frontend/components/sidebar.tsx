"use client";

// 모든 업무 화면 왼쪽에 표시되는 메뉴와 프로젝트 선택 영역입니다.

import Link from "next/link";
import { usePathname } from "next/navigation";

// ready=false인 메뉴에는 작동하는 척하는 버튼 대신 '준비 중'을 표시합니다.
const menu = [
  { href: "/", icon: "▦", label: "대시보드", ready: true },
  { href: "/dashboard", icon: "◆", label: "BuildLog", ready: true },
  { href: "/sales-master", icon: "▤", label: "Sales Master", ready: true },
  { href: "/uploads", icon: "⇧", label: "자료 업로드", ready: true },
  { href: "/psi", icon: "⌁", label: "PSI", ready: true },
  { href: "/history", icon: "◷", label: "변경 이력", ready: true },
  { href: "/settings", icon: "⚙", label: "연동 및 설정", ready: true },
];

export function Sidebar({ projects, currentProjectId, busy, onProjectChange, onManage, onLogout }: { projects: { id: string; name: string }[]; currentProjectId: string; busy: boolean; onProjectChange: (id: string) => void; onManage: () => void; onLogout: () => void }) {
  // 현재 URL과 같은 메뉴에 active 스타일을 적용합니다.
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <Link href="/" className="brand">
        <span className="brand-mark lighthouse-mark" aria-hidden="true">
          <svg viewBox="0 0 44 44">
            <path className="lighthouse-beam" d="M16.4 13.2 4.5 9.1v8.2l11.9-1.9m11.2-2.2 11.9-4.1v8.2l-11.9-1.9" />
            <path className="lighthouse-roof" d="m14.8 13.3 7.2-5 7.2 5Z" />
            <rect className="lighthouse-lantern" x="16.4" y="13" width="11.2" height="7.2" rx="2" />
            <circle className="lighthouse-light" cx="22" cy="16.6" r="2" />
            <path className="lighthouse-tower" d="M17.7 20h8.6l2.3 15.2H15.4Z" />
            <path className="lighthouse-stripe" d="M16.8 26.3h10.4l.8 5.1H16Z" />
            <rect className="lighthouse-door" x="20" y="30.6" width="4" height="5.1" rx="1.6" />
            <path className="lighthouse-ground" d="M10.2 36.4c3.7-2 7.4-1.2 11.8 0 4.4 1.2 8.1 2 11.8 0" />
          </svg>
        </span>
        <span className="brand-copy"><strong>SCM FLOW</strong><small>SHIPMENT CONTROL TOWER</small></span>
      </Link>
      <div className="workspace-label">PROJECT <span>{projects.length}</span></div>
      <div className="project-switcher"><select aria-label="프로젝트 선택" value={currentProjectId} disabled={busy} onChange={event => onProjectChange(event.target.value)}>{projects.map(project => <option key={project.id} value={project.id}>{project.name}</option>)}</select><button type="button" disabled={busy} onClick={onManage}>프로젝트 관리</button></div>
      <nav className="side-nav" aria-label="주 메뉴">
        {menu.map((item) => {
          const active = item.href === "/" ? pathname === "/" : pathname === item.href || pathname.startsWith(`${item.href}/`);
          return <Link key={item.href} href={item.href} className={`nav-link${active ? " active" : ""}`} aria-current={active ? "page" : undefined}>
            <span className="nav-icon" aria-hidden="true">{item.icon}</span><span className="nav-label">{item.label}</span>{!item.ready && <span className="nav-soon">준비 중</span>}
          </Link>;
        })}
      </nav>
      <div className="sidebar-bottom"><div className="help-card"><span className="help-mark">i</span><div><strong>프로젝트별 분리</strong><p>빈 프로젝트와 기존 자료 사본을 각각 만들 수 있습니다.</p></div></div><button className="logout-button" type="button" onClick={onLogout}>로그아웃</button></div>
    </aside>
  );
}
