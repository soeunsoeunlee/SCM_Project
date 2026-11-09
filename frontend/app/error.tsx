"use client";

// 개별 페이지 렌더링 중 오류가 났을 때 전체 앱 대신 보여 주는 복구 화면입니다.

import { useEffect } from "react";

export default function AppError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  // 개발자가 원인을 확인할 수 있게 콘솔에 남기고, 사용자는 다시 시도할 수 있습니다.
  useEffect(() => { console.error(error); }, [error]);
  return <section className="panel error-page"><span>!</span><h1>화면을 불러오지 못했어요</h1><p>개발 서버가 시작 중인지 확인한 뒤 다시 시도해 주세요.</p><button className="primary-button" onClick={() => reset()}>다시 시도</button></section>;
}
