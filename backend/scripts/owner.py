"""Local owner control. Credentials are read server-side and never printed."""

import argparse
import json
from pathlib import Path
import secrets
from uuid import UUID

import psycopg
from pydantic import ValidationError

from app.agent.store import RunConflict
from app.config import Settings, EnvironmentFileError
from app.database import DatabaseConfigurationError, connect
from app.owner import OwnerService, DecisionRequest, authenticate_owner


def initialize():
    settings = Settings()
    if settings.owner_api_token and settings.owner_api_token.get_secret_value():
        authenticate_owner(settings.owner_api_token.get_secret_value(), settings)
        return {"status":"ok","credential":"already_configured"}
    path = Path(Settings.model_config["env_file"])
    text = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    token = secrets.token_urlsafe(48)
    lines = text.splitlines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().lower().startswith("owner_api_token="):
            lines[index] = "OWNER_API_TOKEN=" + token
            replaced = True
    if not replaced:
        lines.append("OWNER_API_TOKEN=" + token)
    path.write_text("\n".join(lines)+"\n", encoding="utf-8")
    return {"status":"ok","credential":"generated_locally_not_displayed"}


def run(args):
    try:
        if args.command == "init":
            output = initialize()
        else:
            settings = Settings()
            authenticate_owner(settings.owner_api_token.get_secret_value() if settings.owner_api_token else None, settings)
            service = OwnerService()
            if args.command == "pending":
                with connect(settings) as conn:
                    ids = conn.execute("SELECT id FROM soloestate.approvals WHERE status='pending' ORDER BY created_at,id LIMIT 101").fetchall()
                output = {"approvals":[service.review(row[0]) for row in ids[:100]],"truncated":len(ids)>100}
            elif args.command == "review":
                output = service.review(UUID(args.approval_id))
            else:
                output = service.decide(UUID(args.approval_id), DecisionRequest(decision=args.decision,
                    expected_payload_hash=args.payload_hash, idempotency_key=args.request_key))
        print(json.dumps(output, indent=2))
        return 0
    except (PermissionError, RunConflict, EnvironmentFileError, DatabaseConfigurationError) as exc:
        output = {"status":"error","message":str(exc)}
    except psycopg.Error:
        output = {"status":"unconfirmed","message":"Database decision could not be confirmed; inspect before retrying."}
    except (ValidationError, ValueError, OSError):
        output = {"status":"error","message":"Invalid owner request or local configuration."}
    print(json.dumps(output))
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("pending")
    review = commands.add_parser("review")
    review.add_argument("approval_id")
    decide = commands.add_parser("decide")
    decide.add_argument("approval_id")
    decide.add_argument("--decision", choices=["approved","rejected"], required=True)
    decide.add_argument("--payload-hash", required=True)
    decide.add_argument("--request-key", required=True)
    raise SystemExit(run(parser.parse_args()))
