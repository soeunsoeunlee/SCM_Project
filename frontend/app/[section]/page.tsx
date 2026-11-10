import { notFound } from "next/navigation";
import { SectionView, type SectionSlug } from "@/components/section-view";

// Cloudflare 정적 배포에서도 기존 메뉴 주소를 그대로 사용할 수 있도록 빌드할 경로를 명시합니다.
const sections = ["psi", "history", "settings"] as const satisfies readonly SectionSlug[];

export const dynamicParams = false;

export function generateStaticParams() {
  return sections.map((section) => ({ section }));
}

export default async function SectionPage({
  params,
}: {
  params: Promise<{ section: string }>;
}) {
  const { section } = await params;

  if (!sections.includes(section as SectionSlug)) {
    notFound();
  }

  return <SectionView section={section as SectionSlug} />;
}
