import { NextRequest, NextResponse } from "next/server";
import { baseUrl, body, clear, COOKIE, error, sameOrigin } from "../../../lib/server";

export async function POST(request: NextRequest) {
  if (!sameOrigin(request)) return error("Same-origin sign-in is required.", 403);
  try {
    const value = await body(request);
    const upstream = await fetch(`${baseUrl()}/dashboard/session`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(value), cache: "no-store", signal: AbortSignal.timeout(30_000) });
    if (!upstream.ok) return error(upstream.status === 401 ? "Sign-in link is invalid, expired, or already used. Create a new link." : "Owner session could not be confirmed.", upstream.status);
    const result: unknown = await upstream.json();
    if (!result || typeof result !== "object" || !("session_token" in result) || typeof result.session_token !== "string" || !result.session_token.startsWith("sess_") || !("expires_at" in result) || typeof result.expires_at !== "string" || !(Date.parse(result.expires_at) > Date.now())) return error("Invalid session response.", 502);
    const response = NextResponse.json({ authenticated: true, expires_at: result.expires_at }, { headers: { "Cache-Control": "no-store" } });
    response.cookies.set(COOKIE, result.session_token, { httpOnly: true, sameSite: "strict", secure: request.nextUrl.protocol === "https:", path: "/", maxAge: Math.max(0, Math.min(28_800, Math.floor((Date.parse(result.expires_at) - Date.now()) / 1000))) });
    return response;
  } catch { return error("Sign-in could not be confirmed. Check the backend and create a new link if necessary.", 503); }
}

export async function GET(request: NextRequest) {
  const session = request.cookies.get(COOKIE)?.value;
  if (!session) return NextResponse.json({ authenticated: false }, { headers: { "Cache-Control": "no-store" } });
  try {
    const upstream = await fetch(`${baseUrl()}/dashboard/session`, { headers: { Authorization: `Bearer ${session}` }, cache: "no-store", signal: AbortSignal.timeout(30_000) });
    if (upstream.status === 401) return clear(NextResponse.json({ authenticated: false }));
    if (!upstream.ok) return error("Session verification is unavailable. Check the backend.", 503);
    const value: unknown = await upstream.json();
    if (!value || typeof value !== "object" || !("authenticated" in value) || value.authenticated !== true) return error("Invalid session verification response.", 502);
    return NextResponse.json({ authenticated: true }, { headers: { "Cache-Control": "no-store" } });
  } catch { return error("Session verification is unavailable. Check the backend.", 503); }
}

export async function DELETE(request: NextRequest) {
  if (!sameOrigin(request)) return error("Same-origin sign-out is required.", 403);
  let confirmed = false;
  const session = request.cookies.get(COOKIE)?.value;
  try {
    if (session) confirmed = (await fetch(`${baseUrl()}/dashboard/session`, { method: "DELETE", headers: { Authorization: `Bearer ${session}` }, cache: "no-store", signal: AbortSignal.timeout(30_000) })).ok;
    else confirmed = true;
  } catch { /* The local cookie is cleared even when remote revocation is unavailable. */ }
  return clear(NextResponse.json({ signed_out: true, revocation_confirmed: confirmed }, { headers: { "Cache-Control": "no-store" } }));
}
