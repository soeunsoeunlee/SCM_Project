interface CloudflareEnv {
  BACKEND_API_URL?: string;
}

interface PagesContext {
  request: Request;
  env: CloudflareEnv;
}

const BODYLESS_METHODS = new Set(["GET", "HEAD"]);

/**
 * 브라우저의 /api/backend/* 요청을 Lightsail FastAPI로 안전하게 중계합니다.
 * 백엔드 주소는 소스코드가 아니라 Cloudflare의 BACKEND_API_URL 환경변수로 받습니다.
 */
export async function onRequest({ request, env }: PagesContext): Promise<Response> {
  const backendBase = env.BACKEND_API_URL?.trim().replace(/\/+$/, "");

  if (!backendBase) {
    return Response.json(
      { detail: "Cloudflare에 BACKEND_API_URL 환경변수가 설정되지 않았습니다." },
      { status: 503 },
    );
  }

  let backendUrl: URL;
  try {
    backendUrl = new URL(backendBase);
  } catch {
    return Response.json({ detail: "BACKEND_API_URL 형식이 올바르지 않습니다." }, { status: 500 });
  }

  if (!["http:", "https:"].includes(backendUrl.protocol)) {
    return Response.json({ detail: "BACKEND_API_URL은 HTTP 또는 HTTPS 주소여야 합니다." }, { status: 500 });
  }

  const incomingUrl = new URL(request.url);
  const backendPath = incomingUrl.pathname.replace(/^\/api\/backend/, "") || "/";
  const targetUrl = new URL(`${backendBase}${backendPath}${incomingUrl.search}`);
  const headers = new Headers(request.headers);

  // 대상 서버가 다시 계산해야 하는 전송 헤더와 Cloudflare 내부 헤더는 제거합니다.
  headers.delete("host");
  headers.delete("content-length");
  headers.delete("cf-connecting-ip");
  headers.delete("cf-ray");
  headers.delete("cf-visitor");

  try {
    const body = BODYLESS_METHODS.has(request.method) ? undefined : await request.arrayBuffer();
    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body,
      redirect: "manual",
    });

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers,
    });
  } catch {
    return Response.json({ detail: "백엔드 서버에 연결하지 못했습니다." }, { status: 502 });
  }
}
