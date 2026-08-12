export const ALLOWED_WEB_ORIGINS = new Set([
  "https://jy2834.github.io",
  "http://localhost:5173",
  "http://127.0.0.1:5173",
]);

export function isAllowedOrigin(origin: string | null): boolean {
  return origin === null || ALLOWED_WEB_ORIGINS.has(origin);
}

export function corsHeaders(origin: string | null): Headers {
  const headers = new Headers({
    "access-control-allow-headers": "authorization, apikey, content-type, x-client-info",
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-max-age": "600",
    "vary": "Origin",
  });
  if (origin !== null && ALLOWED_WEB_ORIGINS.has(origin)) {
    headers.set("access-control-allow-origin", origin);
  }
  return headers;
}
