import { afterEach, describe, expect, it, vi } from "vitest";
import { GET } from "./route";

afterEach(() => { vi.unstubAllGlobals(); vi.unstubAllEnvs(); });

describe("backend health proxy", () => {
  it("reports a confirmed backend response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json({ status: "ok", service: "soloestate-backend" })));
    const response = await GET();
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok", backend: { status: "ok", service: "soloestate-backend" } });
  });
  it.each([
    () => Promise.reject(new Error("offline")),
    () => Promise.resolve(Response.json({ status: "ok" })),
    () => Promise.resolve(Response.json({ status: "ok", service: "soloestate-backend" }, { status: 500 })),
  ])("reports unavailable when confirmation fails", async (result) => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(result));
    expect((await GET()).status).toBe(503);
  });
});
