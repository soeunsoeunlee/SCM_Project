// Next.js 실행 설정입니다.
import type { NextConfig } from "next";

// 로컬 개발에서는 Next.js가 FastAPI 요청을 중계합니다.
// Cloudflare Pages는 빌드 환경에 CF_PAGES=1을 자동으로 넣으므로 정적 out 폴더를 만듭니다.
const backendUrl = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";
const isCloudflarePages = process.env.CF_PAGES === "1";

const nextConfig: NextConfig = isCloudflarePages
  ? {
      // Cloudflare Pages가 배포할 수 있는 정적 결과물을 frontend/out에 생성합니다.
      output: "export",
      trailingSlash: true,
    }
  : {
      async rewrites() {
        // 로컬 브라우저는 같은 출처만 호출하고 Next.js가 8000번 API로 중계합니다.
        return [{ source: "/api/backend/:path*", destination: `${backendUrl}/:path*` }];
      },
    };

export default nextConfig;
