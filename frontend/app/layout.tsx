// 모든 페이지가 공통으로 사용하는 최상위 HTML 구조와 로그인 보호 셸입니다.
import type { Metadata } from "next";
import { AuthenticatedShell } from "@/components/authenticated-shell";
import "./globals.css";
import "./psi-overrides.css";

export const metadata: Metadata = {
  // 브라우저 탭 제목과 검색엔진 설명입니다.
  title: "SCM Shipment Control Tower",
  description: "판매·생산·선적 흐름을 한눈에 보는 SCM 데모",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // children 위치에 대시보드·Sales Master·업로드 같은 현재 페이지가 들어갑니다.
  return (
    <html lang="ko">
      <body>
        <AuthenticatedShell>{children}</AuthenticatedShell>
      </body>
    </html>
  );
}
