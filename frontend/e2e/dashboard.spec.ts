import { test as base, expect, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";

type Scenario = { run: string; lead: string; property: string; approval: string; lead_name: string; property_title: string };
const backend = resolve(process.cwd(), "../backend");
const origin=process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3000';
function runFixture(...args: string[]) {
  return execFileSync(resolve(backend, ".venv/Scripts/python.exe"), ["-c", "import runpy; runpy.run_path('../frontend/e2e/approval_fixture.py',run_name='__main__')", ...args], { cwd: backend, encoding: "utf-8", windowsHide: true });
}
const test = base.extend<{ scenario: Scenario }>({
  scenario: async ({ page }, provideScenario) => {
    const data = JSON.parse(runFixture("create")) as Scenario;
    try { await provideScenario(data); }
    finally {
      try { await page.request.delete("/api/session", { headers: { origin } }); }
      finally { runFixture("cleanup", data.run, data.lead, data.property); }
    }
  },
});
function link(run?: string) {
  const backend = resolve(process.cwd(), "../backend");
  const output = execFileSync(resolve(backend, ".venv/Scripts/python.exe"), ["-m", "scripts.dashboard_login"], { cwd: backend, encoding: "utf-8", windowsHide: true });
  const value = JSON.parse(output) as { url?: string };
  if (!value.url) throw new Error("Could not issue the local owner sign-in link");
  const url = new URL(value.url); const target=new URL(origin);url.host=target.host;url.protocol=target.protocol;url.pathname = "/agent"; if (run) url.searchParams.set("run", run); return url.toString();
}
async function login(page: Page, scenario?: Scenario) {
  await page.goto(link(scenario?.run));
  await expect(page.getByRole("heading", { name: /a little more clarity/i })).toBeVisible();
  if (scenario) await expect(page.getByRole("heading", { name: scenario.lead_name })).toBeVisible();
}
test("locks private data before owner sign-in", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByText("Owner sign-in required")).toBeVisible();
  expect((await page.request.get("/api/owner/runs")).status()).toBe(401);
  await expect(page.getByText("Aarav Demo")).toHaveCount(0);
});
test("loads persisted plans, timeline, leads and a pending review without runtime errors", async ({ page, scenario }) => {
  const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await login(page, scenario);
  await expect(page.getByText(scenario.property_title, { exact: true })).toBeVisible();
  await expect(page.getByText("Paused for your decision.")).toBeVisible();
  await expect(page.locator("#execution .timeline > li")).toHaveCount(5);
  await expect(page.locator("#execution .badge-failed")).toHaveCount(1);
  const cookies = await page.context().cookies();
  expect(cookies.find(cookie => cookie.name === "soloestate_session")?.httpOnly).toBe(true);
  expect(await page.evaluate(() => document.cookie)).not.toContain("soloestate_session");
  expect(new URL(page.url()).hash).toBe("");
  expect(errors).toEqual([]);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
  await expect(page.getByText("Owner sign-in required")).toBeVisible();
});
test("shows the full review snapshot and closes it without deciding", async ({ page, scenario }) => {
  await login(page, scenario);
  const pending = await (await page.request.get("/api/owner/approvals")).json() as { approvals: { id: string; run_id: string }[] };
  const approval = pending.approvals.find(item => item.run_id === scenario.run);
  expect(approval).toBeTruthy();
  await page.locator(`[data-approval-id="${approval!.id}"]`).getByRole("button", { name: "Review", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Approve draft" })).toBeEnabled();
  await expect(dialog.locator(".message-preview p")).not.toBeEmpty();
  await page.getByRole("button", { name: "Close review" }).click();
  await expect(dialog).not.toBeVisible();
  const still = await (await page.request.get(`/api/owner/approvals/${approval!.id}`)).json() as { status: string };
  expect(still.status).toBe("pending");
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
});
test("supports a narrow viewport without horizontal overflow", async ({ page, scenario }) => {
  await page.setViewportSize({ width: 390, height: 844 }); await login(page, scenario);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.getByRole("button", { name: "Sign out", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "Owner reviews" }).click();
  await expect(page.getByRole("heading", { name: "Owner reviews" })).toBeVisible();
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
});
test("rejects a used sign-in link in another browser session", async ({ page, browser }) => {
  const url = link(); await page.goto(url);
  await expect(page.getByRole("heading", { name: /a little more clarity/i })).toBeVisible();
  const other = await browser.newContext(); const second = await other.newPage();
  try {
    await second.goto(url);
    await expect(second.getByText("Owner sign-in required")).toBeVisible();
    await expect(second.getByRole("alert").filter({ hasText: "already used" })).toBeVisible();
  } finally { await other.close(); }
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
});
test("approves only an isolated fictional draft through the owner review UI", async ({ page, scenario: data }) => {
    await page.goto(link(data.run));
    await page.locator(`[data-approval-id="${data.approval}"]`).getByRole("button", { name: "Review", exact: true }).click();
    await expect(page.getByRole("dialog")).toContainText("Fictional browser test. Never send.");
    await page.getByRole("button", { name: "Approve draft", exact: true }).click();
    await expect.poll(async () => {
      const response = await page.request.get(`/api/owner/approvals/${data.approval}`);
      return (await response.json()).status;
    }).toBe("approved");
});

test("runs a real read-only goal through the dashboard", async ({ page }) => {
  test.skip(process.env.SOLOESTATE_LIVE_UI !== "1", "Explicit opt-in required for billable real Meta requests");
  test.setTimeout(300_000);
  await login(page);
  await page.getByLabel("Business goal", { exact: true }).fill("Read the fictional leads created during 2–8 November 2026 in Asia/Kolkata, then finish using the verified tool results. Do not draft, request approval, send messages or book visits.");
  await page.getByLabel("Permissions").selectOption("read_only");
  const started = page.waitForResponse(response => response.url().endsWith("/api/owner/runs") && response.request().method() === "POST", { timeout: 240_000 });
  await page.getByRole("button", { name: "Run goal", exact: true }).click();
  const response = await started; const result = await response.json() as { run_id: string; status: string };
  expect(response.ok()).toBe(true); expect(result.status).toBe("completed");
  await expect(page.getByText("Validated plan execution finished.")).toBeVisible();
  const detail = await (await page.request.get(`/api/owner/runs/${result.run_id}`)).json() as { outcome: { messages_sent_by_app: number; booking_counts: object; draft_counts: object } };
  expect(detail.outcome.messages_sent_by_app).toBe(0);
  expect(detail.outcome.booking_counts).toEqual({}); expect(detail.outcome.draft_counts).toEqual({});
  console.log(`Verified live UI run: ${result.run_id}`);
  await page.getByRole("button", { name: "Sign out", exact: true }).click();
});
