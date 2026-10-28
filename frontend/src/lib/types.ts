import { z } from "zod";

export const RunState = z.enum(["pending", "running", "awaiting_approval", "completed", "failed", "cancelled"]);
const record = z.record(z.string(), z.unknown());
const Counts = z.record(z.string(), z.number().int().nonnegative());
export const StepSchema = z.object({
  id: z.string(), sequence: z.number().int(), tool_name: z.string(),
  status: z.enum(["pending", "running", "succeeded", "failed", "awaiting_approval"]),
  arguments: record.optional(), result: record.nullable(), error: record.nullable(),
  created_at: z.string().optional(), finished_at: z.string().nullable().optional(),
});
export const RunSchema = z.object({
  id: z.string(), goal: z.string(), request_key: z.string(), status: RunState,
  plan: z.object({ proposal: z.object({ summary: z.string(), steps: z.array(z.object({ tool: z.string(), purpose: z.string(), required: z.boolean() })) }).optional() }).nullable(),
  outcome: z.object({ reason: z.string().optional(), model_trace: z.array(z.object({ response_id: z.string(), model: z.string() })).optional(),
    draft_counts: Counts.optional(), approval_counts: Counts.optional(), booking_counts: Counts.optional(),
    messages_sent_by_app: z.number().optional(), completion_scope: z.string().optional(),
    owner_decisions: z.array(record).optional(), events: z.array(record).optional(),
  }).nullable(),
  live_steps: z.array(StepSchema), steps_truncated: z.boolean(), resume_count: z.number(),
  lease_until: z.string().nullable(), worker_active: z.boolean(),
});
export const RunListSchema = z.object({ runs: z.array(z.object({ id: z.string(), request_key: z.string(), goal: z.string(), status: RunState, updated_at: z.string() })), truncated: z.boolean() });
export const ApprovalSchema = z.object({ id: z.string(), run_id: z.string(), kind: z.enum(["followup", "booking"]), payload: record, payload_hash: z.string(), status: z.string().optional() });
export const ApprovalListSchema = z.object({ approvals: z.array(ApprovalSchema), truncated: z.boolean() });
export type Run = z.infer<typeof RunSchema>;
export type Step = z.infer<typeof StepSchema>;
export type RunList = z.infer<typeof RunListSchema>;
export type Approval = z.infer<typeof ApprovalSchema>;

export const labels: Record<string, string> = {
  get_leads: "Read leads", get_property_matches: "Match properties", get_available_slots: "Check visit slots",
  create_followup_draft: "Prepare follow-up draft", request_approval: "Request owner approval",
  book_site_visit: "Book site visit", get_run_status: "Inspect run status",
};
export const states: Record<string, string> = { pending: "Ready to continue", running: "In progress", awaiting_approval: "Needs your review", completed: "Completed", failed: "Stopped with an error", cancelled: "Cancelled", succeeded: "Succeeded" };

export function resultData(step: Step): Record<string, unknown> {
  const data = step.result?.data;
  return data && typeof data === "object" && !Array.isArray(data) ? data as Record<string, unknown> : {};
}
export function object(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}
export function records(value: unknown): Record<string, unknown>[] { return Array.isArray(value) ? value.map(object) : []; }
export function text(value: unknown): string { return typeof value === "string" || typeof value === "number" ? String(value) : ""; }
export function money(value: unknown): string {
  if (value === null || value === undefined || value === "") return "Price unavailable";
  const amount = Number(value);
  return Number.isFinite(amount) ? new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(amount) : "Price unavailable";
}
export function date(value: unknown): string {
  if (typeof value !== "string" || Number.isNaN(Date.parse(value))) return "Time unavailable";
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)) + " IST";
}
