"use client";

// 로그인 전에는 계정 생성/로그인 화면을, 로그인 후에는 공통 메뉴와 현재 페이지를 보여 줍니다.

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Sidebar } from "@/components/sidebar";

type AuthStatus = {
  // /api/auth/status가 반환하는 사용자·프로젝트 상태입니다.
  authenticated: boolean;
  needs_setup: boolean;
  user: { id: string; email: string; display_name: string } | null;
  projects: { id: string; name: string; role: string; copied_from_id: string | null; created_at: string | null; as_of_date: string; order_count: number; upload_count: number; psi_count: number }[];
  current_project_id: string | null;
};

async function request(path: string, body?: unknown, method?: "POST" | "DELETE") {
  // 인증·프로젝트 API를 호출하고 서버 오류 문구를 사용자가 읽을 수 있게 꺼냅니다.
  const response = await fetch(`/api/backend/api${path}`, {
    method: body === undefined ? "GET" : method ?? "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.detail ?? `요청 오류 (${response.status})`);
  return result;
}

export function AuthenticatedShell({ children }: { children: React.ReactNode }) {
  // status가 null이면 최초 로그인 상태를 확인하는 중입니다.
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [mode, setMode] = useState<"setup" | "login">("login");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [projectManagerOpen, setProjectManagerOpen] = useState(false);
  const [newProjectName, setNewProjectName] = useState("");
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null);
  const [deleteConfirmation, setDeleteConfirmation] = useState("");

  const refresh = useCallback(async () => {
    // 쿠키를 바탕으로 현재 사용자와 접근 가능한 프로젝트를 다시 읽습니다.
    try {
      const next = await request("/auth/status") as AuthStatus;
      setStatus(next);
      if (next.needs_setup) setMode("setup");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "로그인 상태를 확인하지 못했습니다."); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    // 최초 설정과 일반 로그인은 입력 항목이 달라 mode에 맞춰 요청 본문을 만듭니다.
    event.preventDefault(); setBusy(true); setError("");
    const data = new FormData(event.currentTarget);
    try {
      const body = mode === "setup"
        ? { display_name: data.get("display_name"), email: data.get("email"), password: data.get("password"), project_name: data.get("project_name") }
        : { email: data.get("email"), password: data.get("password") };
      await request(mode === "setup" ? "/auth/setup" : "/auth/login", body);
      await refresh();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "로그인하지 못했습니다."); }
    finally { setBusy(false); }
  }

  async function changeProject(projectId: string) {
    // 서버 쿠키의 현재 프로젝트를 바꾼 후 새 프로젝트 기준으로 화면을 다시 읽습니다.
    setBusy(true); setError("");
    try { await request("/projects/select", { project_id: projectId }); await refresh(); window.location.reload(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "프로젝트를 바꾸지 못했습니다."); setBusy(false); }
  }

  async function changeAsOfDate(asOfDate: string) {
    if (!asOfDate) return;
    setBusy(true); setError("");
    try {
      await request("/projects/as-of-date", { as_of_date: asOfDate });
      await refresh();
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "기준 날짜를 변경하지 못했습니다.");
      setBusy(false);
    }
  }

  async function copyProject() {
    // 현재 Sales Master를 새 프로젝트로 복사한 뒤 바로 그 사본으로 전환합니다.
    const current = status?.projects.find(project => project.id === status.current_project_id);
    const name = window.prompt("새 프로젝트 사본 이름을 입력하세요.", `${current?.name ?? "SCM 프로젝트"} - 사본`);
    if (!name?.trim()) return;
    setBusy(true); setError("");
    try {
      const result = await request("/projects/copy", { name: name.trim() }) as { project: { id: string }; copied_orders: number };
      await request("/projects/select", { project_id: result.project.id });
      await refresh(); window.location.reload();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "프로젝트를 복사하지 못했습니다."); setBusy(false); }
  }

  async function createBlankProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!newProjectName.trim()) return;
    setBusy(true); setError("");
    try {
      await request("/projects", { name: newProjectName.trim() });
      await refresh();
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "빈 프로젝트를 만들지 못했습니다.");
      setBusy(false);
    }
  }

  async function deleteProject(projectId: string) {
    const target = status?.projects.find(project => project.id === projectId);
    if (!target || deleteConfirmation !== target.name) return;
    setBusy(true); setError("");
    try {
      await request(`/projects/${projectId}`, { confirmation_name: deleteConfirmation }, "DELETE");
      setDeleteTargetId(null); setDeleteConfirmation("");
      await refresh();
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "프로젝트를 삭제하지 못했습니다.");
      setBusy(false);
    }
  }

  async function logout() { await request("/auth/logout", {}); setStatus(null); await refresh(); }

  // 로그인 확인 중, 로그인 전, 로그인 후 화면을 명확히 나누어 렌더링합니다.
  if (!status) return <div className="auth-screen"><div className="auth-card"><strong>SCM FLOW</strong><p>{error || "로그인 상태를 확인하는 중입니다…"}</p></div></div>;
  if (!status.authenticated) return <div className="auth-screen"><form className="auth-card" onSubmit={submit}>
    <span className="brand-mark">S</span><div><strong>SCM FLOW</strong><h1>{mode === "setup" ? "첫 관리자 계정 만들기" : "로그인"}</h1><p>{mode === "setup" ? "비밀번호는 암호화되어 저장됩니다. 실제 업무용 비밀번호는 다른 서비스와 공유하지 마세요." : "내 프로젝트와 Sales Master를 확인합니다."}</p></div>
    {error && <div className="auth-error">{error}</div>}
    {mode === "setup" && <><label>이름<input name="display_name" required minLength={2} autoComplete="name" /></label><label>첫 프로젝트 이름<input name="project_name" required defaultValue="202610 SCM 프로세스 관리프로젝트" /></label></>}
    <label>이메일<input name="email" type="email" required autoComplete="email" /></label>
    <label>비밀번호<input name="password" type="password" required minLength={mode === "setup" ? 12 : 1} autoComplete={mode === "setup" ? "new-password" : "current-password"} /></label>
    <button className="primary-button" disabled={busy}>{busy ? "처리 중…" : mode === "setup" ? "계정과 프로젝트 만들기" : "로그인"}</button>
    {!status.needs_setup && <button type="button" className="auth-switch" onClick={() => setMode(mode === "login" ? "setup" : "login")}>{mode === "login" ? "초기 설정 화면" : "로그인으로 돌아가기"}</button>}
  </form></div>;

  const current = status.projects.find(project => project.id === status.current_project_id);
  return <div className="app-shell">
    <Sidebar projects={status.projects} currentProjectId={status.current_project_id ?? ""} busy={busy} onProjectChange={changeProject} onManage={() => setProjectManagerOpen(true)} onLogout={logout} />
    <div className="workspace"><header className="topbar"><div><span className="topbar-eyebrow">SUPPLY CHAIN · OPERATIONS</span><strong>{current?.name ?? "SCM 프로젝트"}</strong></div><div className="topbar-right"><label className="as-of-date">기준 날짜<input type="date" value={current?.as_of_date ?? ""} disabled={busy} onChange={event => void changeAsOfDate(event.target.value)} /></label><span className="connection-dot" />{status.user?.display_name} <span className="avatar">{status.user?.display_name.slice(0, 2)}</span></div></header><main className="main-content">{error && <div className="error-banner">{error}</div>}{children}</main></div>
    {projectManagerOpen && <div className="project-modal-backdrop" role="presentation" onMouseDown={event => { if (event.currentTarget === event.target && !busy) setProjectManagerOpen(false); }}>
      <section className="project-modal" role="dialog" aria-modal="true" aria-labelledby="project-manager-title">
        <div className="project-modal-heading"><div><h2 id="project-manager-title">프로젝트 관리</h2><p>빈 프로젝트를 시작하거나, 현재 프로젝트의 사본을 만들고, 더 이상 쓰지 않는 프로젝트를 삭제합니다.</p></div><button type="button" className="modal-close" disabled={busy} aria-label="닫기" onClick={() => setProjectManagerOpen(false)}>×</button></div>
        {error && <div className="error-banner">{error}</div>}
        <form className="blank-project-form" onSubmit={createBlankProject}><label><span>새 빈 프로젝트 이름</span><input value={newProjectName} onChange={event => setNewProjectName(event.target.value)} minLength={2} maxLength={160} placeholder="예: 202611 SCM 운영 프로젝트" required /></label><button className="primary-button" disabled={busy || newProjectName.trim().length < 2}>{busy ? "처리 중…" : "빈 프로젝트 시작"}</button></form>
        <div className="project-list-heading"><strong>내 프로젝트</strong><span>{status.projects.length}개</span></div>
        <div className="project-card-list">{status.projects.map(project => {
          const isCurrent = project.id === status.current_project_id;
          const deleting = deleteTargetId === project.id;
          return <article className={`project-card${isCurrent ? " current" : ""}`} key={project.id}>
            <div className="project-card-main"><div><strong>{project.name}</strong>{isCurrent && <span className="current-project-badge">현재 사용 중</span>}</div><small>{project.copied_from_id ? "기존 프로젝트 사본" : "독립 프로젝트"} · {project.created_at ? new Date(project.created_at).toLocaleDateString("ko-KR") : "생성일 미확인"}</small></div>
            <dl><div><dt>주문</dt><dd>{project.order_count}건</dd></div><div><dt>업로드</dt><dd>{project.upload_count}건</dd></div><div><dt>PSI</dt><dd>{project.psi_count}개</dd></div></dl>
            <div className="project-card-actions">{!isCurrent && <button type="button" className="small-button neutral" disabled={busy} onClick={() => void changeProject(project.id)}>전환</button>}<button type="button" className="small-button neutral" disabled={busy || !isCurrent} title={isCurrent ? "현재 프로젝트의 주문 상태를 복사합니다." : "먼저 이 프로젝트로 전환해 주세요."} onClick={() => void copyProject()}>사본 만들기</button><button type="button" className="project-delete-button" disabled={busy || status.projects.length === 1} title={status.projects.length === 1 ? "마지막 프로젝트는 삭제할 수 없습니다." : undefined} onClick={() => { setDeleteTargetId(project.id); setDeleteConfirmation(""); }}>삭제</button></div>
            {deleting && <div className="project-delete-confirm"><strong>삭제하면 복구할 수 없습니다.</strong><p>주문, 업로드, PSI와 프로젝트 안의 모든 업무 데이터가 영구 삭제됩니다. 계속하려면 아래에 프로젝트 이름을 그대로 입력하세요.</p><code>{project.name}</code><input autoFocus value={deleteConfirmation} onChange={event => setDeleteConfirmation(event.target.value)} placeholder="프로젝트 이름 입력" /><div><button type="button" className="small-button neutral" disabled={busy} onClick={() => { setDeleteTargetId(null); setDeleteConfirmation(""); }}>취소</button><button type="button" className="danger-button" disabled={busy || deleteConfirmation !== project.name} onClick={() => void deleteProject(project.id)}>{busy ? "삭제 중…" : "영구 삭제"}</button></div></div>}
          </article>;
        })}</div>
      </section>
    </div>}
  </div>;
}
