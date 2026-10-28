// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import Dashboard from "./dashboard";

const RUN = "40000000-0000-4000-8000-000000000001";
const APPROVAL = "50000000-0000-4000-8000-000000000001";
const LEAD = "10000000-0000-4000-8000-000000000001";
const payload = { recipient: "aarav@example.invalid", subject: "Your Cedar Apartment visit", body: "Hi Aarav, this is a draft for your review.", channel: "email", version: 1 };
function fixture() {
  const step = (sequence: number, tool_name: string, data: object) => ({ id: `step-${sequence}`, sequence, tool_name, status: "succeeded", arguments: {}, result: { status: "succeeded", data }, error: null, finished_at: "2026-10-27T12:00:00Z" });
  return {
    id: RUN, goal: "Review fictional leads and prepare a draft", request_key: "saved-request", status: "awaiting_approval",
    plan: { proposal: { summary: "Review verified leads before drafting", steps: [{ tool: "get_leads", purpose: "Read the real leads", required: true }] } },
    outcome: { reason: "owner_approval_required", draft_counts: { draft: 1 }, approval_counts: { pending: 1 }, booking_counts: {}, messages_sent_by_app: 0, model_trace: [{ response_id: "resp_test", model: "muse-spark-1.3" }] },
    resume_count: 0, lease_until: null, worker_active: false, steps_truncated: false,
    live_steps: [
      step(1, "get_leads", { leads: [{ id: LEAD, name: "Aarav Demo", bedrooms: 2, budget_min: "8000000", budget_max: "12000000", preferred_localities: ["Indiranagar"] }], truncated: false }),
      step(2, "get_property_matches", { lead_id: LEAD, matches: [{ id: "property-1", title: "Demo Cedar Apartment", locality: "Indiranagar", price: "10500000", bedrooms: 2, score: 100 }], truncated: false }),
    ],
  };
}
function backend(options: { locked?: boolean; empty?: boolean; run?: unknown; decision?: (init?: RequestInit) => Promise<Response>; start?: (init?: RequestInit) => Promise<Response> } = {}) {
  const current = options.run ?? fixture();
  const fetch = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url === "/api/session") return Response.json({ authenticated: !options.locked });
    if (url === "/api/health") return Response.json({ status: "ok" });
    if (url === "/api/owner/runs" && init?.method === "POST") return options.start ? options.start(init) : Response.json({ run_id: RUN, status: "awaiting_approval" });
    if (url === "/api/owner/runs") return Response.json({ runs: options.empty ? [] : [{ id: RUN, goal: fixture().goal, request_key: "saved-request", status: "awaiting_approval", updated_at: "2026-10-27T12:00:00Z" }], truncated: false });
    if (url === "/api/owner/approvals") return Response.json({ approvals: options.empty ? [] : [{ id: APPROVAL, run_id: RUN, kind: "followup", payload, payload_hash: "a".repeat(64) }], truncated: false });
    if (url === `/api/owner/runs/${RUN}`) return Response.json(current);
    if (url.startsWith("/api/owner/runs?")) return Response.json({ message: "Request key was not found" }, { status: 409 });
    if (url === `/api/owner/approvals/${APPROVAL}`) return Response.json({ id: APPROVAL, run_id: RUN, kind: "followup", status: "pending", payload, payload_hash: "a".repeat(64) });
    if (url.endsWith("/decision")) return options.decision ? options.decision(init) : Response.json({ approval_id: APPROVAL, decision: "approved" });
    throw new Error(`Unhandled fixture request ${url}`);
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}
beforeEach(() => { sessionStorage.clear(); window.history.replaceState({}, "", "/"); });

it("locks the workspace without a valid session and does not fetch private records", async () => {
  const fetch = backend({ locked: true }); render(<Dashboard />);
  expect(await screen.findByText("Owner sign-in required")).toBeVisible();
  expect(fetch.mock.calls.some(([url]) => String(url).startsWith("/api/owner"))).toBe(false);
});
it("consumes a fragment sign-in ticket once under Strict Mode and removes it from the address", async () => {
  window.history.replaceState({},"","/#ticket=login_one_time_owner_link");
  const fetch=backend();render(<StrictMode><Dashboard /></StrictMode>);
  expect(await screen.findByText("Aarav Demo")).toBeVisible();
  const exchanges=fetch.mock.calls.filter(([url,init])=>String(url)==="/api/session" && init?.method==="POST");
  expect(exchanges).toHaveLength(1);
  expect(window.location.hash).toBe("");
});
it("shows backend-confirmed plan, timeline, lead matches and an honest outcome", async () => {
  backend(); render(<Dashboard />);
  expect(await screen.findByText("Aarav Demo")).toBeVisible();
  expect(screen.getByText("Demo Cedar Apartment")).toBeVisible();
  expect(screen.getByText("Paused for your decision.")).toBeVisible();
  expect(screen.getByText("Review verified leads before drafting")).toBeVisible();
  const report = screen.getByRole("region", { name: "Outcome report" });
  expect(within(report).getByText("Messages sent by app").parentElement).toHaveTextContent("0");
  expect(screen.queryByText(/message sent successfully/i)).not.toBeInTheDocument();
});
it("does not invent results or claim completion when there are no saved runs", async () => {
  backend({ empty: true }); render(<Dashboard />);
  expect(await screen.findByText("No runs yet. Your first goal starts the record.")).toBeVisible();
  expect(screen.getByText("Your results will appear here")).toBeVisible();
  expect(screen.queryByText("Validated plan execution finished.")).not.toBeInTheDocument();
});
it("renders a running step without a final success report", async () => {
  const run = { ...fixture(), status: "running", worker_active: true, outcome: { model_trace: [] }, live_steps: [{ ...fixture().live_steps[0], status: "running", result: null, finished_at: null }] };
  backend({ run }); render(<Dashboard />);
  expect(await screen.findByText("Waiting for the backend result…")).toBeVisible();
  expect(screen.queryByText("Paused for your decision.")).not.toBeInTheDocument();
  expect(screen.queryByText("Validated plan execution finished.")).not.toBeInTheDocument();
});
it("displays failed tool evidence and recovery controls without claiming success", async () => {
  const run = { ...fixture(), status: "failed", outcome: { ...fixture().outcome, reason: "database_error" }, live_steps: [{ ...fixture().live_steps[0], status: "failed", result: null, error: { code: "database_error", message: "Database result could not be confirmed" } }] };
  backend({ run }); render(<Dashboard />);
  expect(await screen.findByText("Database result could not be confirmed")).toBeVisible();
  expect(screen.getByRole("button", { name: "Recover saved run" })).toBeEnabled();
  expect(screen.queryByText("Validated plan execution finished.")).not.toBeInTheDocument();
});
it("opens the full review snapshot without deciding it automatically", async () => {
  const user = userEvent.setup(); const fetch = backend(); render(<Dashboard />);
  await user.click(await screen.findByRole("button", { name: "Review" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText(payload.body)).toBeVisible();
  expect(within(dialog).getByText(payload.recipient)).toBeVisible();
  expect(fetch.mock.calls.filter(([url]) => String(url).endsWith("/decision"))).toHaveLength(0);
});
it("binds an explicit owner decision to the reviewed hash", async () => {
  const user = userEvent.setup(); const fetch = backend(); render(<Dashboard />);
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Approve draft" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  const request = fetch.mock.calls.find(([url]) => String(url).endsWith("/decision"));
  expect(JSON.parse(request![1]!.body as string)).toMatchObject({ decision: "approved", expected_payload_hash: "a".repeat(64) });
});
it.each([
  {},
  { approval_id: "another-approval", decision: "approved" },
  { approval_id: APPROVAL, decision: "rejected" },
])("does not confirm approval from an unrelated or malformed HTTP 200 response: %j", async acknowledgement => {
  const user = userEvent.setup();
  backend({ decision: async () => Response.json(acknowledgement) }); render(<Dashboard />);
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Approve draft" }));
  expect(await screen.findByRole("dialog")).toBeVisible();
  expect(await screen.findByText(/Decision acknowledgement could not be verified/)).toBeVisible();
  expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
});

it("sends an explicit rejection without granting approval", async () => {
  const user = userEvent.setup();
  const fetch = backend({ decision: async () => Response.json({ approval_id: APPROVAL, decision: "rejected" }) });
  render(<Dashboard />);
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Reject" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  const decisions = fetch.mock.calls.filter(([url]) => String(url).endsWith("/decision"));
  expect(decisions).toHaveLength(1);
  expect(JSON.parse(decisions[0][1]!.body as string)).toMatchObject({ decision: "rejected", expected_payload_hash: "a".repeat(64) });
});

it("does not report missing outcome counts as confirmed zeros", async () => {
  backend({ run: { ...fixture(), outcome: { reason: "database_error" }, status: "failed" } });
  render(<Dashboard />);
  const report = await screen.findByRole("region", { name: "Outcome report" });
  await waitFor(() => expect(within(report).getByText("Messages sent by app").parentElement).toHaveTextContent("Not confirmed"));
  expect(within(report).getByText("Confirmed visits").parentElement).toHaveTextContent("—");
  expect(within(report).getByText("Draft records").parentElement).toHaveTextContent("—");
});

it("removes an earlier visit option when the latest verified availability check is empty", async () => {
  const run = fixture();
  run.live_steps.push(
    { ...run.live_steps[0], id: "old-slots", sequence: 3, tool_name: "get_available_slots", result: { status: "succeeded", data: { slots: [{ id: "slot-old", starts_at: "2026-11-05T04:30:00Z", ends_at: "2026-11-05T05:00:00Z" }] } } },
    { ...run.live_steps[0], id: "new-slots", sequence: 4, tool_name: "get_available_slots", result: { status: "succeeded", data: { slots: [] } } },
  );
  backend({ run }); const view = render(<Dashboard />);
  expect(await screen.findByText("No slots were returned for the checked window.")).toBeVisible();
  expect(view.container.querySelector(".slot-grid")).toBeNull();
});
it("keeps an uncertain decision open and retries the same key without an opposite decision", async () => {
  const user = userEvent.setup(); const decision = vi.fn().mockResolvedValueOnce(Response.json({ message: "Decision not confirmed" }, { status: 503 })).mockResolvedValueOnce(Response.json({ approval_id: APPROVAL, decision: "approved", replayed: true }));
  backend({ decision }); render(<Dashboard />);
  await user.click(await screen.findByRole("button", { name: "Review" }));
  await user.click(screen.getByRole("button", { name: "Approve draft" }));
  expect(await screen.findByText("Decision not confirmed")).toBeVisible();
  expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  await user.click(screen.getByRole("button", { name: "Approve draft" }));
  await waitFor(() => expect(decision).toHaveBeenCalledTimes(2));
  const bodies = decision.mock.calls.map(([init]) => JSON.parse(init!.body as string));
  expect(bodies[0].idempotency_key).toBe(bodies[1].idempotency_key);
});
it("prevents duplicate goal submissions while the accepted request is in flight", async () => {
  let resolve!: (response: Response) => void;
  const start = vi.fn(() => new Promise<Response>(r => { resolve = r; }));
  backend({ start }); render(<Dashboard />);
  const button = await screen.findByRole("button", { name: "Run goal" });
  fireEvent.click(button); fireEvent.click(button);
  await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("button", { name: "Following execution…" })).toBeDisabled();
  await act(async () => resolve(Response.json({ run_id: RUN, status: "awaiting_approval" })));
});
it("preserves the goal request key after an unconfirmed response", async () => {
  const user = userEvent.setup(); const start = vi.fn().mockResolvedValue(Response.json({ message: "Execution not confirmed" }, { status: 503 }));
  backend({ start }); render(<Dashboard />);
  await user.click(await screen.findByRole("button", { name: "Run goal" }));
  expect(await screen.findByText("Execution not confirmed")).toBeVisible();
  await user.click(await screen.findByRole("button", { name: "Retry saved request" }));
  await waitFor(() => expect(start).toHaveBeenCalledTimes(2));
  const requests = start.mock.calls.map(([init]) => JSON.parse(init!.body as string));
  expect(requests[0].request_key).toBe(requests[1].request_key);
});
it("fails closed on malformed run data", async () => {
  backend({ run: { ...fixture(), status: "invented_success", live_steps: "not-a-timeline" } }); render(<Dashboard />);
  expect(await screen.findByRole("alert")).toHaveTextContent("could not be validated");
  expect(screen.queryByText("Aarav Demo")).not.toBeInTheDocument();
});


