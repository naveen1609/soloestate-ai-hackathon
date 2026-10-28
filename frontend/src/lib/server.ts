import { NextRequest, NextResponse } from "next/server";

export const COOKIE = "soloestate_session";
export const baseUrl = () => (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
export function sameOrigin(request: NextRequest): boolean {
  try {
    const origin = new URL(request.headers.get("origin") ?? "");
    // NextURL normalizes loopback addresses to localhost; compare the real HTTP authority.
    return origin.host === (request.headers.get("host") ?? request.nextUrl.host) &&
      origin.protocol === request.nextUrl.protocol && request.headers.get("sec-fetch-site") !== "cross-site";
  } catch { return false; }
}
export function error(message: string, status: number) { return NextResponse.json({ status: "error", message }, { status, headers: { "Cache-Control": "no-store" } }); }
export async function body(request: NextRequest) {
  const raw = await request.text();
  if (new TextEncoder().encode(raw).length > 32_000) throw new Error("Request too large");
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Expected object");
  return value;
}
export function clear(response: NextResponse) { response.cookies.set(COOKIE, "", { httpOnly: true, sameSite: "strict", path: "/", maxAge: 0 }); return response; }
