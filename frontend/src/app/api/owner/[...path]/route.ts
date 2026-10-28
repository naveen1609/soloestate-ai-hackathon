import { NextRequest, NextResponse } from "next/server";
import { baseUrl, body, clear, COOKIE, error, sameOrigin } from "../../../../lib/server";

const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export function permitted(method: string, path: string): boolean {
  return method === "GET" ? new RegExp(`^(runs|approvals)(/${uuid})?$`).test(path) :
    method === "POST" && (path === "runs" || new RegExp(`^runs/${uuid}/resume$|^approvals/${uuid}/decision$`).test(path));
}
async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const session = request.cookies.get(COOKIE)?.value;
  if (!session || !session.startsWith("sess_")) return error("Sign in to the owner workspace.", 401);
  if (request.method !== "GET" && !sameOrigin(request)) return error("Same-origin action is required.", 403);
  const path = (await context.params).path.join("/");
  if (!permitted(request.method, path)) return error("Endpoint is not permitted.", 404);
  const query = request.nextUrl.searchParams;
  if ([...query.keys()].some(key => path !== "runs" || key !== "request_key") || (query.get("request_key")?.length ?? 0) > 128) return error("Invalid query.", 400);
  try {
    const payload = request.method === "POST" ? JSON.stringify(await body(request)) : undefined;
    const upstream = await fetch(`${baseUrl()}/owner/${path}${query.size ? `?${query}` : ""}`, { method: request.method, headers: { Authorization: `Bearer ${session}`, "Content-Type": "application/json" }, body: payload, cache: "no-store", signal: AbortSignal.timeout(request.method === "POST" && !path.endsWith("decision") ? 900_000 : 30_000) });
    const value: unknown = await upstream.json();
    const response = NextResponse.json(value, { status: upstream.status, headers: { "Cache-Control": "no-store" } });
    return upstream.status === 401 ? clear(response) : response;
  } catch { return error("Backend response could not be confirmed. Check the saved run before retrying.", 503); }
}
export const GET = proxy;
export const POST = proxy;
