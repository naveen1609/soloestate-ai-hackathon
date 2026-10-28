import { describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST, permitted } from "./route";

const root = "http://127.0.0.1:3000";
function request(method = "GET", path = "runs", options: { auth?: boolean; origin?: string; query?: string } = {}) {
  const headers: Record<string, string> = { host: "127.0.0.1:3000" };
  if (options.auth !== false) headers.cookie = "soloestate_session=sess_private_browser_session";
  if (method === "POST") { headers.origin = options.origin ?? root; headers["Content-Type"] = "application/json"; }
  return new NextRequest(`${root}/api/owner/${path}${options.query ?? ""}`, { method, headers, body: method === "POST" ? JSON.stringify({ goal: "Read fictional leads", request_key: "stable-key", mode: "read_only" }) : undefined });
}
const context = (path = "runs") => ({ params: Promise.resolve({ path: path.split("/") }) });

describe("owner proxy", () => {
  it("requires a session before contacting the backend", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await GET(request("GET", "runs", { auth: false }), context())).status).toBe(401);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects cross-origin writes", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await POST(request("POST", "runs", { origin: "https://untrusted.invalid" }), context())).status).toBe(403);
    expect(fetch).not.toHaveBeenCalled();
  });
  it.each(["dashboard/tickets", "../runs", "https://untrusted.invalid", "tools/execute_sql", "runs/not-a-uuid"])("forbids arbitrary proxy path %s", path => {
    expect(permitted("POST", path)).toBe(false);
    expect(permitted("GET", path)).toBe(false);
  });
  it("rejects unrecognized query parameters", async () => {
    const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
    expect((await GET(request("GET", "runs", { query: "?upstream=https://untrusted.invalid" }), context())).status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
  it("forwards only the opaque session and preserves a stable request key", async () => {
    const fetch = vi.fn().mockResolvedValue(Response.json({ run_id: "confirmed-run", status: "completed" })); vi.stubGlobal("fetch", fetch);
    const response = await POST(request("POST"), context());
    expect(response.status).toBe(200);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8000/owner/runs");
    expect(init.headers.Authorization).toBe("Bearer sess_private_browser_session");
    expect(JSON.parse(init.body).request_key).toBe("stable-key");
    expect(response.headers.has("Authorization")).toBe(false);
  });
  it("preserves backend conflicts instead of claiming success", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ status: "error", message: "Decision already recorded" }, { status: 409 })));
    expect((await POST(request("POST"), context())).status).toBe(409);
  });
  it("reports uncertain responses on network failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private upstream failure")));
    const response = await POST(request("POST"), context());
    expect(response.status).toBe(503);
    expect(JSON.stringify(await response.json())).not.toContain("private upstream");
  });
});
