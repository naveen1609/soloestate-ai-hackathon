"""CLI-only real Meta agent execution. May create fictional drafts and pending approvals."""

import argparse
import asyncio
import json
from uuid import UUID, uuid4

import psycopg
from pydantic import ValidationError

from app.agent.orchestrator import AgentRunner
from app.agent.schemas import RunRequest
from app.agent.store import RunConflict, RunStore
from app.config import Settings, EnvironmentFileError
from app.database import DatabaseConfigurationError
from app.services.meta_model import MetaModelService, ModelServiceError

DEMO_GOAL = """Review the fictional leads created during 2–8 November 2026 in Asia/Kolkata.
Compare their property matches and prioritize the strongest fit. Read available visits in that same week.
Prepare ONE personalized email follow-up draft for the strongest matched lead, proposing an available site visit.
Request owner approval of that draft, then stop. Never send a message or book a visit."""


async def run(args) -> int:
    try:
        if args.status:
            print(json.dumps(RunStore().status(UUID(args.status)), indent=2))
            return 0
        if args.lookup_key:
            print(json.dumps(RunStore().find(args.lookup_key), indent=2))
            return 0
        settings = Settings()
        async with MetaModelService(settings) as model:
            runner = AgentRunner(model, settings=settings)
            if args.resume:
                result = await runner.resume(UUID(args.resume), recover=args.recover)
            else:
                request = RunRequest(goal=args.goal, request_key=args.request_key or str(uuid4()),
                                     mode="read_only" if args.read_only else "draft_first")
                result = await runner.run(request)
        print(json.dumps(result, indent=2))
        return 0 if result["status"] in {"completed", "awaiting_approval"} else 1
    except (RunConflict, DatabaseConfigurationError, EnvironmentFileError, ModelServiceError) as exc:
        output = {"status": "error", "code": getattr(exc, "code", "configuration_or_conflict"), "message": str(exc)}
    except psycopg.Error:
        output = {"status": "unconfirmed", "code": "database_error", "message": "Execution or persistence could not be confirmed. Inspect the run before retrying; never assume the goal completed."}
    except (ValidationError, ValueError, OSError):
        output = {"status": "error", "code": "configuration", "message": "Invalid goal, identifier, settings, or local environment file."}
    print(json.dumps(output))
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--goal", default=DEMO_GOAL)
    parser.add_argument("--request-key", help="Stable key to retrieve the same completed/stopped run without executing it again")
    parser.add_argument("--read-only", action="store_true", help="Forbid all mutation tools at the server boundary")
    operation = parser.add_mutually_exclusive_group()
    operation.add_argument("--status", help="Read a persisted run UUID; does not call the model")
    operation.add_argument("--resume", help="Continue a decided approval or explicitly recover a stale worker")
    operation.add_argument("--lookup-key", help="Find a persisted run after losing its original response; no model call")
    parser.add_argument("--recover", action="store_true", help="Recover a failed/expired run; bounded to three resumes")
    args = parser.parse_args()
    if args.recover and not args.resume:
        parser.error("--recover requires --resume")
    raise SystemExit(asyncio.run(run(args)))
