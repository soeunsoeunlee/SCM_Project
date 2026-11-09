"use client";

// 최상위 레이아웃까지 렌더링하지 못한 심각한 오류에 사용하는 마지막 안전 화면입니다.

import type { ErrorInfo } from "react";

export default function GlobalError({ reset }: { error: Error & { digest?: string }; reset: (details: { digest?: string }) => void }) {
  return <html lang="ko"><body style={{ background: "#f4f5f3", fontFamily: "Arial, sans-serif", padding: 40 }}><main><h1>화면 오류</h1><p>다시 시도해 주세요.</p><button onClick={() => reset({})}>다시 시도</button></main></body></html>;
}
