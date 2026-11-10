"use client";

import { HistoryView } from "@/components/history-view";
import { PsiView } from "@/components/psi-view";
import { SettingsView } from "@/components/settings-view";

export type SectionSlug = "psi" | "history" | "settings";

/** 빌드 시 생성된 메뉴 주소에 맞는 실제 클라이언트 화면을 표시합니다. */
export function SectionView({ section }: { section: SectionSlug }) {
  if (section === "psi") return <PsiView />;
  if (section === "history") return <HistoryView />;
  return <SettingsView />;
}
