import { NextRequest, NextResponse } from "next/server";
import { baseUrl, body, clear, COOKIE, error, sameOrigin } from "../../../../lib/server";

const uuid = "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}";
export function permitted(method: string, path: string): boolean {
  if (path.startsWith('crm/')) {
    if (method === 'GET') return new RegExp(`^crm/(summary|leads|properties|appointments|visit-slots)$|^crm/(leads|properties|appointments|appointment-requests)/${uuid}$|^crm/operations/[A-Za-z0-9:_-]{1,128}$|^crm/leads/${uuid}/(activities|matches)$`).test(path);
    if (method === 'PATCH') return new RegExp(`^crm/(leads|properties)/${uuid}$`).test(path);
    return method === 'POST' && new RegExp(`^crm/(leads|properties|visit-slots|appointment-requests)$|^crm/leads/${uuid}/(archive|journey|qualify)$|^crm/appointments/${uuid}/cancel$|^crm/appointment-requests/${uuid}/(commit|reject)$`).test(path);
  }
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
  const crmQueries: Record<string,string[]> = {
    'crm/leads':['q','sort','page','page_size','status','archived'],
    'crm/properties':['q','sort','page','page_size','active','locality','bedrooms','min_price','max_price'],
    'crm/appointments':['q','sort','page','page_size','status','period','lead_id','property_id','since','until'],
    'crm/visit-slots':['since','until'],
  };
  const allowed = path === 'runs' ? ['request_key'] : crmQueries[path] ?? (new RegExp(`^crm/leads/${uuid}/activities$`).test(path) ? ['page','page_size'] : new RegExp(`^crm/leads/${uuid}/matches$`).test(path) ? ['limit'] : []);
  if ([...query.keys()].some(key => !allowed.includes(key) || query.getAll(key).length !== 1) || (query.get("request_key")?.length ?? 0) > 128 || query.toString().length > 4000) return error("Invalid query.", 400);
  try {
    const payload = request.method !== "GET" ? JSON.stringify(await body(request)) : undefined;
    const upstream = await fetch(`${baseUrl()}/owner/${path}${query.size ? `?${query}` : ""}`, { method: request.method, headers: { Authorization: `Bearer ${session}`, "Content-Type": "application/json" }, body: payload, cache: "no-store", signal: AbortSignal.timeout(path.startsWith('crm/') ? 60_000 : request.method === "POST" && !path.endsWith("decision") ? 900_000 : 30_000) });
    const value: unknown = await upstream.json();
    const response = NextResponse.json(value, { status: upstream.status, headers: { "Cache-Control": "no-store" } });
    return upstream.status === 401 ? clear(response) : response;
  } catch { return error("Backend response could not be confirmed. Check the saved run before retrying.", 503); }
}
export const GET = proxy;
export const POST = proxy;
export const PATCH = proxy;
