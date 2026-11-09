// Next.js 실행 설정입니다. 브라우저의 /api/backend 요청을 FastAPI 서버로 전달합니다.
import type { NextConfig } from "next";

// 환경변수가 없으면 로컬 FastAPI 기본 주소를 사용합니다.
const backendUrl = process.env.BACKEND_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    // 브라우저는 같은 출처(3000번 포트)만 호출하고 Next.js가 8000번 API로 중계합니다.
    return [{ source: "/api/backend/:path*", destination: `${backendUrl}/:path*` }];
  },
};

export default nextConfig;
