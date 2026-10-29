# AGENTS.md — SoloEstate AI

## Mandatory handoff and edit ownership

Before editing, read [project state](docs/PROJECT_STATE.md) and [current handoff](docs/HANDOFF.md), then follow their links to the relevant architecture, schema, API and verification documents. These files replace reliance on a previous chat; they do not replace inspecting the actual source. Owner instructions take precedence. A proposed next increment is not authorization to implement it.

Inspect `git status --short --branch`, the current branch/commit, recent commits, `git diff --stat`, `git diff --check`, and staged changes. Preserve all existing uncommitted and untracked work. Do not reset, clean, revert, overwrite unrelated work, commit, push, deploy, alter credentials or run database setup just to obtain a clean baseline. Do not inspect or print secret values for a handoff.

**Only one coding agent may edit this repository at a time.** Check the handoff's edit ownership and ask the owner to coordinate if another editor is active. Do not start a second write-capable coding agent, even in another worktree or chat, while an editor is working. Any separately authorized review agent must remain read-only. A clean Git status is not proof that no other agent is active. Record a temporary editor claim in HANDOFF.md when beginning authorized editing and release it when handing off.

Work in small, logically separated increments. Preserve the current agent registry, consent boundaries, booking integrity and shared CRM data. Run relevant tests and record the exact command, source state, actual result, skipped/deselected cases and limitations. **Never claim an unrun test passed.** Database/browser tests, login links, seeding and model probes have side effects; use the explicit database-free test command in testing.md for read-only inspections. Existing historical results are evidence for their recorded revision, not automatic proof of the current tree or live database.

Before finishing, update PROJECT_STATE.md when capabilities/status change, update HANDOFF.md with the current Git state, existing changes, executed checks, pending work and next acceptance criteria, and update the canonical reference/verification document rather than creating parallel descriptions. Keep links valid and preserve dated historical evidence. Changes to these documentation files must themselves be included in the owner's eventual commit; do not silently leave the handoff ignored or claim it is already committed.

Never contact real customers or enable WhatsApp, email, SMS, website customer messaging, external calendar delivery, webhooks or other external delivery without explicit owner authorization for that integration and action. Provider account/credential setup, deployment and publication also require explicit authorization. The project's future product vision is not permission to activate integrations. Use isolated fictional fixtures; never reset the demo database. No model may grant consent, expand its own authority or bypass an owner policy.

## Project mission
Build SoloEstate AI, an owner-directed AI Business Operating System for a one-person real estate business. The application converts natural-language goals into validated plans, executes permitted business tools, persists outcomes, requests human approval for consequential actions, and reports verified results.

## Hackathon constraints
- Build this implementation in this fresh repository during the Meta Global AI Developer Hackathon.
- Use Meta Model API meaningfully inside the submitted product, initially through Muse Spark.
- Do not claim that using a Meta model for coding alone satisfies product model integration.
- Do not copy implementation code from previous projects unless the official hackathon rules explicitly permit it.
- Keep a clear Git history and document significant design decisions.
- Verify the official rules and submission requirements before final submission.

## Architecture
- Frontend: Next.js, TypeScript, responsive UI.
- Backend: Python, FastAPI, Pydantic.
- Product model: Meta Model API using the configured model ID.
- Persistence: Supabase PostgreSQL.
- Tests: pytest for backend and appropriate frontend tests.
- Keep the model provider behind a small service interface so it can be tested and changed safely.

## Agent design
- The model proposes plans and selects among explicitly registered business tools.
- The backend validates every model response and tool argument.
- Execute only allowlisted tools with typed schemas.
- Return actual tool results to the model before it chooses subsequent actions.
- Bound iteration count, timeouts, and retries.
- Persist execution runs, steps, approvals, and outcomes.
- Support recovery when information is missing, a tool fails, or a slot is unavailable.
- Never fabricate successful actions or business records.

## Safety and correctness
- Read-only tools may execute without approval when authorized.
- Customer-facing messages must remain drafts until approved.
- Appointment booking requires valid approval and a fresh availability check.
- Protect against duplicate messages and double booking.
- Use transactions or database constraints for critical state changes.
- Never execute arbitrary SQL, shell commands, or unregistered tools based on model output.
- Never expose API keys or service-role credentials in the frontend.
- Use fictional seed data for demos and tests.

## Engineering workflow
1. Inspect the repository and current environment before making changes.
2. Propose a small implementation plan for each task.
3. Implement one coherent increment at a time.
4. Add or update tests with every meaningful behavior change.
5. Run relevant tests, linting, and type checks.
6. Report files changed, commands run, results, and remaining issues.
7. Do not claim a test passed unless it was actually executed.
8. Avoid unrelated refactors, unnecessary dependencies, and premature abstractions.

## Definition of done
A feature is complete only when its intended behavior is implemented, errors are handled, relevant tests pass, and any setup instructions are documented.

## Delivery priority
Prioritize a repeatable end-to-end demo, correct tool execution, persistence, approval gates, recovery, and clear evidence of meaningful Meta model usage before optional features or visual polish.
