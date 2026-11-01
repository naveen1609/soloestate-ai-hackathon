import { NextRequest, NextResponse } from "next/server";
import { baseUrl, body, error, sameOrigin } from "../../../../lib/server";

export const VISITOR_COOKIE = "soloestate_visitor";
const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export function permitted(method: string, path: string): boolean {
  if (method === 'GET') return new RegExp(`^chat/conversations/${uuid}$|^chat/conversations/${uuid}/matches$|^chat/conversations/${uuid}/slots$`).test(path);
  return method === 'POST' && new RegExp(`^chat/start$|^chat/conversations/${uuid}/(send|requirements|context|handoff)$`).test(path);
}
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const path = (await context.params).path.join("/");
  if (!permitted(request.method, path)) return error("Endpoint is not permitted.", 404);
  if (request.method !== "GET" && !sameOrigin(request)) return error("Same-origin action is required.", 403);
  const query = request.nextUrl.searchParams;
  const allowed = new RegExp(`^chat/conversations/${uuid}/matches$`).test(path) ? ['limit'] : new RegExp(`^chat/conversations/${uuid}/slots$`).test(path) ? ['since','until'] : [];
  if ([...query.keys()].some(key => !allowed.includes(key) || query.getAll(key).length !== 1) || query.toString().length > 4000) return error("Invalid query.", 400);
  const token = request.cookies.get(VISITOR_COOKIE)?.value;
  if (path !== 'chat/start' && (!token || !token.startsWith('v1.'))) return error("Start or resume the chat first.", 401);
  try {
    const payload = request.method !== "GET" ? JSON.stringify(await body(request)) : undefined;
    const headers: Record<string,string> = { "Content-Type": "application/json" };
    if (token) headers.Authorization = `Bearer ${token}`;
    const upstream = await fetch(`${baseUrl()}/${path}${query.size ? `?${query}` : ""}`, { method: request.method, headers, body: payload, cache: "no-store", signal: AbortSignal.timeout(60_000) });
    const value: unknown = await upstream.json();
    if (path === 'chat/start' && upstream.ok) {
      if (!value || typeof value !== "object" || !("token" in value) || typeof value.token !== "string" || !value.token.startsWith("v1.")) return error("Invalid chat response.", 502);
      const rest = { ...(value as Record<string, unknown>) };
      delete rest.token;
      const response = NextResponse.json(rest, { status: upstream.status, headers: { "Cache-Control": "no-store" } });
      response.cookies.set(VISITOR_COOKIE, value.token, { httpOnly: true, sameSite: "strict", secure: request.nextUrl.protocol === "https:", path: "/api/chat", maxAge: 86400 });
      return response;
    }
    return NextResponse.json(value, { status: upstream.status, headers: { "Cache-Control": "no-store" } });
  } catch { return error("Backend response could not be confirmed. Check the saved chat before retrying.", 503); }
}
export const GET = proxy;
export const POST = proxy;
