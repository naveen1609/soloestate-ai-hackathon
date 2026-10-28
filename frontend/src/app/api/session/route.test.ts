import { expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { DELETE, GET, POST } from "./route";

const root = "http://127.0.0.1:3000";
function request(method: string, origin = root) {
  return new NextRequest(`${root}/api/session`, { method, headers: { origin, host: "127.0.0.1:3000", cookie: "soloestate_session=sess_cookie", "Content-Type": "application/json" }, body: method === "POST" ? JSON.stringify({ ticket: "login_one_time_test_ticket" }) : undefined });
}
it("keeps the session token out of the browser JSON and uses an HttpOnly cookie", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ session_token: "sess_private_opaque_value", expires_at: new Date(Date.now() + 3600_000).toISOString() })));
  const response = await POST(request("POST"));
  expect(response.status).toBe(200);
  expect(response.headers.get("set-cookie")).toContain("HttpOnly");
  expect(response.headers.get("set-cookie")).toContain("SameSite=strict");
  expect(JSON.stringify(await response.json())).not.toContain("sess_private");
});
it("rejects a replayed or expired link", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "Expired" }, { status: 401 })));
  const response = await POST(request("POST"));
  expect(response.status).toBe(401);
  expect(response.headers.has("set-cookie")).toBe(false);
});
it("does not contact the backend for cross-origin login or logout", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  expect((await POST(request("POST", "https://untrusted.invalid"))).status).toBe(403);
  expect((await DELETE(request("DELETE", "https://untrusted.invalid"))).status).toBe(403);
  expect(fetch).not.toHaveBeenCalled();
});
it("clears an expired browser session", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ detail: "Expired" }, { status: 401 })));
  const response = await GET(request("GET"));
  expect(await response.json()).toEqual({ authenticated: false });
  expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
});
it("clears the local cookie but does not claim remote revocation after a network error", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  const response = await DELETE(request("DELETE"));
  expect(await response.json()).toEqual({ signed_out: true, revocation_confirmed: false });
  expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
});
