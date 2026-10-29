"use client";
import Link from "next/link";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import { Approval, ApprovalListSchema, ApprovalSchema, date, labels, money, object, records, resultData, Run, RunList, RunListSchema, RunSchema, states, Step, text } from "../lib/types";

export const DEMO_GOAL = "Review the fictional leads for 2–8 November 2026 in Asia/Kolkata. Prioritize the strongest property matches, check available site visits, and prepare one personalized follow-up draft for the best match. Request my approval, then stop. Do not send messages or book visits.";
type Intent = { goal: string; mode: "read_only" | "draft_first"; request_key: string };
const INTENT_KEY = "soloestate_pending_request";
const SELECTED_KEY = "soloestate_selected_run";

function restoredIntent(): Intent | null {
  if (typeof window === "undefined") return null;
  try {
    const value = object(JSON.parse(sessionStorage.getItem(INTENT_KEY) ?? "null"));
    return typeof value.goal === "string" && typeof value.request_key === "string" && (value.mode === "read_only" || value.mode === "draft_first") ? value as Intent : null;
  } catch { return null; }
}
function restoredSelection(): string {
  if (typeof window === "undefined") return "";
  try { return new URL(window.location.href).searchParams.get("run") || sessionStorage.getItem(SELECTED_KEY) || ""; } catch { return ""; }
}

function Icon({ name, size = 20 }: { name: "home" | "activity" | "review" | "arrow" | "shield" | "logout"; size?: number }) {
  const paths = {
    home: <><path d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z" /><path d="M9 21v-8h6v8" /></>,
    activity: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
    review: <><rect x="4" y="5" width="16" height="16" rx="2" /><path d="M9 5V3h6v2M8 13l3 3 5-6" /></>,
    arrow: <><path d="M5 19 19 5M5 5h14v14" /></>,
    shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9S4 17 4 12V6Z" /><path d="m8 12 3 3 5-6" /></>,
    logout: <><path d="M9 4H4v16h5M10 12h11m-4-4 4 4-4 4" /></>,
  };
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>;
}

function Badge({ status }: { status: string }) { return <span className={`badge badge-${status}`}>{states[status] ?? status.replaceAll("_", " ")}</span>; }

function stepSummary(step: Step): string {
  const data = resultData(step);
  if (step.status === "failed") return text(step.error?.message) || "This step failed; no new action is confirmed.";
  if (step.status === "running") return "Waiting for the backend result…";
  switch (step.tool_name) {
    case "get_leads": return `${records(data.leads).length} leads returned${data.truncated ? " · results truncated" : ""}`;
    case "get_property_matches": return data.matching_ready === false ? "Matching requirements incomplete; no inventory conclusion." : `${records(data.matches).length} matching properties returned${data.truncated ? " · results truncated" : ""}`;
    case "get_available_slots": return `${records(data.slots).length} slots available at check time · booking requires a fresh check`;
    case "create_followup_draft": return `${data.created ? "Draft saved" : "Existing draft returned"} · no message sent`;
    case "request_approval": return `Review status: ${text(object(data.approval).status).replaceAll("_", " ")}`;
    case "book_site_visit": return `Booking record: ${text(object(data.booking).status)}${data.created === false ? " · existing record" : ""}`;
    case "get_run_status": return "Persisted run state inspected";
    default: return "Backend result recorded";
  }
}

function Results({ run }: { run: Run | null }) {
  const steps = run?.live_steps ?? [];
  const leads = new Map<string, Record<string, unknown>>();
  const matches = new Map<string, Record<string, unknown>[]>();
  const incompleteMatches = new Map<string, string>();
  const slots = new Map<string, Record<string, unknown>>();
  for (const step of steps) {
    if (step.status !== "succeeded") continue;
    const data = resultData(step);
    if (step.tool_name === "get_leads") for (const lead of records(data.leads)) leads.set(text(lead.id), lead);
    if (step.tool_name === "get_property_matches") {
      const id=text(data.lead_id);matches.set(id,records(data.matches));
      if(data.matching_ready===false) incompleteMatches.set(id,[...(Array.isArray(data.missing_fields)?data.missing_fields:[]),...(Array.isArray(data.invalid_fields)?data.invalid_fields:[])].map(text).map(value=>value.replaceAll('_',' ')).join(', '));
      else incompleteMatches.delete(id);
    }
    if (step.tool_name === "get_available_slots") { slots.clear(); for (const slot of records(data.slots)) slots.set(text(slot.id), slot); }
  }
  return <section className="panel results-panel" id="results" aria-labelledby="results-heading">
    <div className="section-head"><div><p className="eyebrow">BUSINESS INTELLIGENCE</p><h2 id="results-heading">Leads & property matches</h2></div><span className="subtle">From verified tool results</span></div>
    {!leads.size && !matches.size ? <div className="empty"><Icon name="home" size={30} /><h3>Your results will appear here</h3><p>Run a goal or open a previous run to see the leads and matches the backend actually returned.</p></div> :
      <div className="lead-grid">{[...new Set([...leads.keys(), ...matches.keys()])].map(id => {
        const lead = leads.get(id); const properties = matches.get(id);
        return <article className="lead-card" key={id}>
          <div className="lead-title"><span className="avatar">{text(lead?.name).slice(0, 1) || "L"}</span><div><h3>{text(lead?.name) || `Lead ${id.slice(0, 8)}`}</h3><p>{lead ? `${lead.bedrooms==null?"Bedrooms unknown":text(lead.bedrooms)+" bed"} · ${money(lead.budget_min)} – ${money(lead.budget_max)}` : "Lead details not returned in this run"}</p></div></div>
          {lead && <p className="locality">{Array.isArray(lead.preferred_localities) ? lead.preferred_localities.join(" / ") : lead.preferred_localities===null?"Locality not answered":"Locality not returned"}</p>}
          {incompleteMatches.has(id) ? <p className="no-match">Matching requirements incomplete: {incompleteMatches.get(id)||"review the recorded answers"}.</p> : properties === undefined ? <p className="muted-result">Matches not checked in this run</p> : !properties.length ? <p className="no-match">No properties met the checked criteria.</p> : properties.map(property => <div className="property" key={text(property.id)}><div><strong>{text(property.title)}</strong><span>{text(property.locality)} · {text(property.bedrooms)} bedrooms</span></div><div className="property-price"><strong>{money(property.price)}</strong><span>Ranking score {text(property.score)}</span></div></div>)}
        </article>;
      })}</div>}
    {steps.some(step => step.tool_name === "get_available_slots" && step.status === "succeeded") && <div className="slots"><h3>Latest visit options <span>Availability is provisional</span></h3>{slots.size ? <div className="slot-grid">{[...slots.values()].map(slot => <span key={text(slot.id)}>{date(slot.starts_at)} <small>to {date(slot.ends_at)}</small></span>)}</div> : <p className="subtle">No slots were returned for the checked window.</p>}</div>}
  </section>;
}

function ReviewDialog({ approval, onClose, onSaved }: { approval: Approval; onClose: () => void; onSaved: (run: string) => Promise<void> }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const key = useRef(crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [attempted, setAttempted] = useState<"approved" | "rejected" | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const gate = useRef(false);
  useEffect(() => { const element = dialog.current; if (element && !element.open) element.showModal(); return () => { if (element?.open) element.close(); }; }, []);
  async function decide(decision: "approved" | "rejected") {
    if (gate.current || confirmed || (attempted && attempted !== decision)) return;
    setAttempted(decision);
    gate.current = true; setBusy(true); setError("");
    try {
      const acknowledgement = object(await api(`/api/owner/approvals/${approval.id}/decision`, { method: "POST", body: JSON.stringify({ decision, expected_payload_hash: approval.payload_hash, idempotency_key: key.current }) }));
      if (acknowledgement.approval_id !== approval.id || acknowledgement.decision !== decision) {
        throw new ApiError("Decision acknowledgement could not be verified. Retry the same decision or check the saved review.", 502);
      }
      setConfirmed(true);
      await onSaved(approval.run_id);
      onClose();
    } catch (err) { setError(err instanceof ApiError ? err.message : "The decision or workspace refresh could not be confirmed. Refresh before choosing a different action."); }
    finally { gate.current = false; setBusy(false); }
  }
  const payload = approval.payload;
  return <dialog ref={dialog} className="review-dialog" aria-labelledby="review-title" onCancel={event => { if (busy) event.preventDefault(); else onClose(); }}>
    <div className="dialog-top"><span className="eyebrow">OWNER REVIEW</span><button className="icon-button" aria-label="Close review" disabled={busy} onClick={onClose}>×</button></div>
    <h2 id="review-title">{approval.kind === "followup" ? "Review your follow-up" : "Review the site visit"}</h2>
    <p className="subtle">{approval.kind === "followup" ? "Approving saves an approved draft. It does not send this message." : "Approval permits this booking after the backend rechecks availability."}</p>
    <dl className="review-meta"><div><dt>Recipient</dt><dd>{text(payload.recipient)}</dd></div>{approval.kind === "followup" ? <><div><dt>Channel</dt><dd>{text(payload.channel)}</dd></div><div><dt>Version</dt><dd>{text(payload.version)}</dd></div></> : <><div><dt>Property</dt><dd>{text(payload.property_title)} · {money(payload.price)}</dd></div><div><dt>Visit</dt><dd>{date(payload.starts_at)} – {date(payload.ends_at)}</dd></div></>}</dl>
    {approval.kind === "followup" && <div className="message-preview"><h3>{text(payload.subject)}</h3><p>{text(payload.body)}</p></div>}
    <p className="review-fingerprint">Review snapshot · {approval.payload_hash.slice(0, 12)}… · {approval.status ?? "pending"}</p>
    {error && <div className="notice notice-error" role="alert">{error}</div>}
    <div className="dialog-actions"><button className="button button-danger" disabled={busy || confirmed || attempted === "approved" || approval.status !== "pending"} onClick={() => void decide("rejected")}>Reject</button><button className="button button-primary" disabled={busy || confirmed || attempted === "rejected" || approval.status !== "pending"} onClick={() => void decide("approved")}>{busy ? "Confirming decision…" : approval.kind === "followup" ? "Approve draft" : "Approve visit"}</button></div>
  </dialog>;
}

export default function Dashboard() {
  const [auth, setAuth] = useState<"loading" | "locked" | "ready">("loading");
  const [notice, setNotice] = useState("");
  const [toast, setToast] = useState("");
  const [online, setOnline] = useState<boolean | null>(null);
  const [restored] = useState(restoredIntent);
  const [goal, setGoal] = useState(restored?.goal ?? DEMO_GOAL);
  const [mode, setMode] = useState<Intent["mode"]>(restored?.mode ?? "draft_first");
  const [runs, setRuns] = useState<RunList>({ runs: [], truncated: false });
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [approvalsTruncated, setApprovalsTruncated] = useState(false);
  const [run, setRun] = useState<Run | null>(null);
  const [selected, setSelected] = useState(restoredSelection);
  const selectedRef = useRef(selected);
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const [hasIntent, setHasIntent] = useState(!!restored);
  const intent = useRef<Intent | null>(restored);
  const [review, setReview] = useState<Approval | null>(null);
  const [openingReview, setOpeningReview] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [synced, setSynced] = useState<Date | null>(null);
  const initialized = useRef(false);
  const polling = useRef(false);

  const problem = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) { setAuth("locked"); setRun(null); setRuns({ runs: [], truncated: false }); setApprovals([]); setReview(null); }
    setNotice(err instanceof ApiError ? err.message : "The backend response could not be validated. Refresh before taking another action.");
  }, []);
  const loadLists = useCallback(async () => {
    const [r, a] = await Promise.all([api("/api/owner/runs"), api("/api/owner/approvals")]);
    const recent = RunListSchema.parse(r); setRuns(recent);
    const pending = ApprovalListSchema.parse(a); setApprovals(pending.approvals); setApprovalsTruncated(pending.truncated);
    setLoaded(true); setSynced(new Date());
    return recent;
  }, []);
  const loadRun = useCallback(async (id: string) => {
    const value = RunSchema.parse(await api(`/api/owner/runs/${id}`));
    if (selectedRef.current === id) setRun(value);
  }, []);
  const choose = useCallback(async (id: string) => {
    selectedRef.current = id; setSelected(id); setRun(null);
    sessionStorage.setItem(SELECTED_KEY, id);
    const url = new URL(window.location.href); url.searchParams.set("run", id); window.history.replaceState({}, "", url);
    await loadRun(id);
  }, [loadRun]);
  const clearIntent = useCallback(() => { intent.current = null; setHasIntent(false); sessionStorage.removeItem(INTENT_KEY); }, []);
  const lookup = useCallback(async () => {
    if (!intent.current) return;
    const value = RunSchema.parse(await api(`/api/owner/runs?request_key=${encodeURIComponent(intent.current.request_key)}`));
    selectedRef.current = value.id; setSelected(value.id); setRun(value); sessionStorage.setItem(SELECTED_KEY, value.id); clearIntent();
  }, [clearIntent]);

  useEffect(() => {
    if (initialized.current) return;
    initialized.current = true;
    const url = new URL(window.location.href); const ticket = new URLSearchParams(url.hash.slice(1)).get("ticket") || url.searchParams.get("ticket");
    if (ticket) { url.searchParams.delete("ticket"); url.hash = ""; window.history.replaceState({}, "", url); }
    void (async () => {
      try {
        const session = object(await api("/api/session", ticket ? { method: "POST", body: JSON.stringify({ ticket }) } : undefined));
        setAuth(session.authenticated === true ? "ready" : "locked");
      } catch (err) { problem(err); setAuth("locked"); }
    })();
  }, [problem]);

  useEffect(() => {
    if (auth !== "ready") return;
    let active = true;
    async function refresh() {
      if (polling.current || !active) return;
      polling.current = true;
      try {
        const recent = await loadLists();
        if (intent.current) { try { await lookup(); } catch (err) { if (!(err instanceof ApiError && err.status === 409)) throw err; } }
        else if (selectedRef.current) await loadRun(selectedRef.current);
        else if (recent.runs[0]) await choose(recent.runs[0].id);
        const health = object(await api("/api/health")); if (active) setOnline(health.status === "ok");
      } catch (err) { if (active) { problem(err); setOnline(false); } }
      finally { polling.current = false; }
    }
    void refresh(); const timer = setInterval(() => void refresh(), 8000);
    return () => { active = false; clearInterval(timer); };
  }, [auth, choose, loadLists, loadRun, lookup, problem]);

  async function start(event: FormEvent) {
    event.preventDefault(); if (busyRef.current || !goal.trim()) return;
    const request = intent.current ?? { goal: goal.trim(), mode, request_key: crypto.randomUUID() };
    intent.current = request; setHasIntent(true); sessionStorage.setItem(INTENT_KEY, JSON.stringify(request));
    busyRef.current = true; setBusy(true); setNotice(""); setToast("");
    try {
      const result = object(await api("/api/owner/runs", { method: "POST", body: JSON.stringify(request) }));
      if (typeof result.run_id !== "string") throw new Error("Missing confirmed run ID");
      clearIntent(); await choose(result.run_id); await loadLists();
    } catch (err) { problem(err); try { await lookup(); } catch { /* Keep the stable request key for deliberate retry. */ } }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function resume(recover: boolean) {
    if (!run || busyRef.current) return;
    busyRef.current = true; setBusy(true); setNotice("");
    try { await api(`/api/owner/runs/${run.id}/resume`, { method: "POST", body: JSON.stringify({ recover }) }); await loadRun(run.id); await loadLists(); }
    catch (err) { problem(err); }
    finally { busyRef.current = false; setBusy(false); }
  }
  async function openReview(id: string) {
    setOpeningReview(id); setNotice("");
    try { setReview(ApprovalSchema.parse(await api(`/api/owner/approvals/${id}`))); } catch (err) { problem(err); }
    finally { setOpeningReview(""); }
  }
  async function logout() {
    try {
      const value = object(await api("/api/session", { method: "DELETE" }));
      setNotice(value.revocation_confirmed === false ? "Signed out locally. Server session revocation could not be confirmed." : "");
      setAuth("locked"); setToast(""); setGoal(DEMO_GOAL); setMode("draft_first"); setRun(null); setRuns({ runs: [], truncated: false }); setApprovals([]); setReview(null); clearIntent(); sessionStorage.removeItem(SELECTED_KEY);
    } catch (err) { problem(err); }
  }

  if (auth !== "ready") return <main className="login-shell"><div className="login-brand"><Icon name="home" size={28} /> SoloEstate<span>AI</span></div><section className="login-card"><span className="eyebrow">YOUR PRIVATE WORKSPACE</span><h1>One owner.<br />A business in motion.</h1><p>Your goals, your decisions, and a clear record of what actually happened.</p>{auth === "loading" ? <p role="status">Verifying your owner session…</p> : <><div className="login-lock"><Icon name="shield" /><div><strong>Owner sign-in required</strong><p>Open a fresh sign-in link to enter your workspace.</p></div></div><details open><summary>Local sign-in instructions</summary><p>With both apps running, run this from the backend terminal and open the returned link within two minutes:</p><code>.\.venv\Scripts\python.exe -m scripts.dashboard_login</code><p className="subtle">The link is single-use. Keep it private.</p></details></>}{notice && <div className="notice notice-error" role="alert">{notice}</div>}</section><p className="login-footer">Owner-directed execution. Customer-facing actions stay under your control.</p></main>;

  const current = run?.id === selected ? run : null;
  const steps = current?.live_steps ?? [];
  const leadIds = new Set(steps.filter(s => s.status === "succeeded" && s.tool_name === "get_leads").flatMap(s => records(resultData(s).leads).map(l => text(l.id))));
  const matchIds = new Set(steps.filter(s => s.status === "succeeded" && s.tool_name === "get_property_matches").flatMap(s => records(resultData(s).matches).map(p => text(p.id))));
  const executing = busy || (current?.status === "running" && current.worker_active);
  const outcome = current?.outcome;
  const model = outcome?.model_trace?.at(-1)?.model;
  const hasLeadCheck = steps.some(s => s.tool_name === "get_leads" && s.status === "succeeded");
  const hasMatchCheck = steps.some(s => s.tool_name === "get_property_matches" && s.status === "succeeded");

  return <div className="app-shell">
    <a className="skip-link" href="#goal">Skip to workspace</a>
    <aside className="sidebar"><a href="#goal" className="brand"><span className="brand-icon"><Icon name="home" size={24} /></span><span>SoloEstate<span className="brand-ai">AI</span><small>OWNER WORKSPACE</small></span></a><p className="nav-caption">AI EXECUTION</p><nav aria-label="Workspace"><a className="nav-active" href="#goal"><Icon name="home" />Overview</a><a href="#execution"><Icon name="activity" />Execution</a><a href="#reviews"><Icon name="review" />Owner reviews<span className="nav-count">{loaded ? approvals.length : "—"}</span></a></nav><div className="sidebar-bottom"><div className="owner-card"><span className="avatar">O</span><div><strong>Solo owner</strong><small>Private business workspace</small></div></div><p>Fictional demo · Bengaluru<br />INR · Asia/Kolkata</p></div></aside>
    <div className="workspace"><header className="topbar"><div>Workspace <span>/</span> <strong>Overview</strong></div><Link className="crm-agent-return" href="/">CRM overview</Link><div className="connection"><span className={`connection-dot ${online === true ? "online" : online === false ? "offline" : ""}`} />{online === true ? "Backend online" : online === false ? "Connection needs attention" : "Checking connection"}</div><button className="logout" aria-label="Sign out" onClick={() => void logout()}><Icon name="logout" size={17} />Sign out</button></header>
      <main className="dashboard-main"><div className="page-heading"><div><p className="eyebrow">YOUR BUSINESS, IN FOCUS</p><h1>A little more clarity.<br className="mobile-break" /> A lot more control.</h1><p>Set the goal. Follow the work. Make the decisions that matter.</p></div><span className="workspace-tag"><Icon name="shield" size={16} />Owner-directed</span></div>
      {notice && <div className="notice notice-error" role="alert"><span>{notice}</span><button onClick={() => { setNotice(""); void loadLists().catch(problem); }}>Refresh</button></div>}
      {toast && <div className="notice notice-success" role="status">{toast}</div>}
      <div className="metrics"><article><span>Leads reviewed</span><strong>{hasLeadCheck ? leadIds.size : "—"}</strong><small>Unique leads returned in this run</small></article><article><span>Property matches</span><strong>{hasMatchCheck ? matchIds.size : "—"}</strong><small>Verified inventory matches</small></article><article className="metric-review"><span>Awaiting your review</span><strong>{loaded ? `${approvals.length}${approvalsTruncated ? "+" : ""}` : "—"}</strong><small>Pending requests across runs</small></article><article><span>Confirmed visits</span><strong>{outcome?.booking_counts ? outcome.booking_counts.confirmed ?? 0 : "—"}</strong><small>Persisted bookings in this run</small></article></div>
      <div className="start-grid"><section className="panel goal-panel" id="goal"><div className="section-head"><div><p className="eyebrow">START WITH AN OUTCOME</p><h2>What needs your attention?</h2></div><span className="spark">✦</span></div><form onSubmit={event => void start(event)}><label className="sr-only" htmlFor="business-goal">Business goal</label><textarea id="business-goal" maxLength={20000} value={goal} onChange={event => setGoal(event.target.value)} disabled={executing || hasIntent} rows={5} /><div className="goal-controls"><label htmlFor="execution-mode">Permissions<select id="execution-mode" value={mode} onChange={event => setMode(event.target.value as Intent["mode"])} disabled={executing || hasIntent}><option value="draft_first">Drafts & approvals</option><option value="read_only">Read-only</option></select></label><button className="button button-primary" type="submit" disabled={executing || !goal.trim()}>{executing ? "Following execution…" : hasIntent ? "Retry saved request" : "Run goal"}{!executing && <Icon name="arrow" size={17} />}</button></div></form><div className="goal-foot"><p><Icon name="shield" size={15} />Follow-ups stay drafts. Bookings require approval.</p><button className="text-button" disabled={executing || hasIntent} onClick={() => setGoal(DEMO_GOAL)}>Use demo goal</button></div>{hasIntent && <button className="text-button recovery-link" onClick={() => void lookup().catch(problem)}>Check saved run before retrying</button>}<p className="billing-note">Uses Meta Model API. The demo goal uses the fixed week of 2–8 November 2026.</p></section>
      <section className="panel history-panel" aria-labelledby="history-heading"><div className="section-head"><h2 id="history-heading">Recent work</h2><span className="subtle">{runs.truncated ? "Latest 20" : runs.runs.length}</span></div>{!loaded ? <p className="small-empty" role="status">Loading saved runs…</p> : !runs.runs.length ? <p className="small-empty">No runs yet. Your first goal starts the record.</p> : <div className="run-list">{runs.runs.map(item => <button key={item.id} className={`run-item ${selected === item.id ? "selected" : ""}`} disabled={busy} onClick={() => { setNotice(""); void choose(item.id).catch(problem); }}><span className={`run-dot ${item.status}`} /><div><strong>{item.goal}</strong><span>{states[item.status]} · {date(item.updated_at)}</span></div><span className="run-arrow">↗</span></button>)}</div>}<p className="history-note">{synced ? `Synced ${synced.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })}` : "Waiting for backend confirmation"}</p></section></div>
      <section className="panel execution-panel" id="execution" aria-labelledby="execution-heading"><div className="section-head"><div><p className="eyebrow">VISIBLE, VERIFIED WORK</p><h2 id="execution-heading">Execution workspace</h2></div>{current && <Badge status={current.status} />}</div>{!selected ? <div className="empty"><Icon name="activity" size={32} /><h3>Every action leaves a trace</h3><p>Select a saved run or start a goal to see its validated plan and actual tool results.</p></div> : !current ? <p className="small-empty" role="status">Loading saved execution…</p> : <><div className="run-context"><span>Run {current.id.slice(0, 8)}</span><span>{model ?? "Model response not recorded yet"}</span><span>{outcome?.model_trace?.length ?? 0} model responses</span><span>{current.resume_count} resumes</span></div><div className="execution-columns"><section className="plan-column" aria-labelledby="plan-heading"><h3 id="plan-heading">Plan preview <span>Proposed, then validated</span></h3>{current.plan?.proposal ? <><p className="plan-summary">{current.plan.proposal.summary}</p><ol className="plan-list">{current.plan.proposal.steps.map((step, index) => <li key={`${index}-${step.tool}`}><span className="plan-number">{String(index + 1).padStart(2, "0")}</span><div><strong>{labels[step.tool] ?? step.tool}</strong><p>{step.purpose}</p><small>{step.required ? "Required" : "Conditional"}</small></div></li>)}</ol></> : <p className="small-empty">{current.status === "running" ? "Waiting for a validated model plan…" : "No validated plan was saved."}</p>}</section><section className="timeline-column" aria-labelledby="timeline-heading"><h3 id="timeline-heading">Tool timeline <span>Backend-confirmed states</span></h3>{!steps.length ? <p className="small-empty">No tool steps recorded yet.</p> : <ol className="timeline">{steps.map(step => <li key={step.id} className={`timeline-${step.status}`}><span className="timeline-marker">{step.status === "succeeded" ? "✓" : step.status === "failed" ? "×" : step.sequence}</span><div className="timeline-content"><div className="timeline-title"><strong>{labels[step.tool_name] ?? step.tool_name}</strong><Badge status={step.status} /></div><p>{stepSummary(step)}</p>{step.finished_at && <small>{date(step.finished_at)}</small>}<details><summary>View backend result</summary><pre>{JSON.stringify(step.result ?? step.error ?? { status: step.status }, null, 2)}</pre></details></div></li>)}</ol>}{current.steps_truncated && <p className="notice">The timeline is truncated to 200 steps.</p>}</section></div>{current.worker_active && <p className="execution-note" role="status"><span className="pulse-dot" />Worker active. Progress refreshes every 8 seconds. Lease until {date(current.lease_until)}.</p>}{(current.status === "pending" || current.status === "failed" || (current.status === "running" && !current.worker_active)) && <div className="continue-row"><p>{current.status === "pending" ? "Your decisions are saved. Continue the remaining work when ready." : "Inspect the saved evidence before recovering this run."}</p><button className="button button-primary" disabled={busy || current.worker_active} onClick={() => void resume(current.status !== "pending")}>{busy ? "Continuing…" : current.status === "pending" ? "Continue run" : "Recover saved run"}</button></div>}</> }</section>
      <Results run={current} />
      <div className="review-outcome-grid"><section className="panel approvals-panel" id="reviews" aria-labelledby="reviews-heading"><div className="section-head"><div><p className="eyebrow">YOU HAVE THE FINAL SAY</p><h2 id="reviews-heading">Owner reviews</h2></div><span className="count-pill">{loaded ? approvals.length : "—"}</span></div>{!loaded ? <p className="small-empty">Loading owner reviews…</p> : !approvals.length ? <div className="empty compact"><Icon name="shield" size={28} /><h3>Nothing waiting on you</h3><p>New drafts and booking requests appear here before consequential actions proceed.</p></div> : <div className="approval-list">{approvals.map(item => <article key={item.id} data-approval-id={item.id}><span className="review-icon"><Icon name={item.kind === "followup" ? "review" : "home"} /></span><div><strong>{item.kind === "followup" ? text(item.payload.subject) || "Follow-up draft" : text(item.payload.property_title) || "Site visit"}</strong><p>{text(item.payload.recipient)}</p><small>{item.kind === "followup" ? "Draft only · not sent" : date(item.payload.starts_at)}</small></div><button className="button button-light" disabled={!!openingReview} onClick={() => void openReview(item.id)}>{openingReview === item.id ? "Opening…" : "Review"}</button></article>)}</div>}{approvalsTruncated && <p className="subtle">Showing the first 100 pending requests.</p>}</section>
      <section className="panel outcome-panel" aria-labelledby="outcome-heading"><div className="section-head"><div><p className="eyebrow">EVIDENCE OVER ASSUMPTIONS</p><h2 id="outcome-heading">Outcome report</h2></div><Icon name="shield" /></div>{!current || !outcome?.reason ? <p className="small-empty">A report appears when the backend confirms a stop or finish. Running work is not reported as complete.</p> : <><Badge status={current.status} /><h3>{current.status === "awaiting_approval" ? "Paused for your decision." : current.status === "completed" ? "Validated plan execution finished." : current.status === "pending" ? "Ready for your next step." : current.status === "cancelled" ? "Stopped by owner decision." : "Execution stopped. Review the evidence."}</h3><dl className="outcome-counts"><div><dt>Draft records</dt><dd>{outcome.draft_counts ? Object.values(outcome.draft_counts).reduce((a, b) => a + b, 0) : "—"}</dd></div><div><dt>Pending approvals</dt><dd>{outcome.approval_counts ? outcome.approval_counts.pending ?? 0 : "—"}</dd></div><div><dt>Confirmed visits</dt><dd>{outcome.booking_counts ? outcome.booking_counts.confirmed ?? 0 : "—"}</dd></div><div><dt>Messages sent by app</dt><dd>{outcome.messages_sent_by_app ?? "Not confirmed"}</dd></div></dl><p className="outcome-reason">{outcome.reason.replaceAll("_", " ")}</p><p className="scope-note">This report confirms persisted tool evidence. It does not independently certify every interpretation of the business goal.</p></>}</section></div>
      <footer className="dashboard-footer"><span>SoloEstate AI · Owner-directed business operations</span><span>Plans are proposals. Results are verified.</span></footer>
      </main>
    </div>
    {review && <ReviewDialog key={review.id} approval={review} onClose={() => setReview(null)} onSaved={async id => { setToast("Owner decision recorded by the backend. No message was sent."); await choose(id); await loadLists(); }} />}
  </div>;
}
