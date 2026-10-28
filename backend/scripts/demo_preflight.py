"""Read-only demo readiness check. Never calls Meta, changes records or issues login links."""

import argparse
from contextlib import contextmanager
import json
from uuid import uuid4

import httpx

from app.config import Settings
from app.database import connect
from app.migrations import verify
from app.owner import authenticate_owner
from app.tools.schemas import ToolContext
from app.tools.service import BusinessTools


@contextmanager
def read_only_connection(settings):
    with connect(settings) as conn:
        conn.execute("SET TRANSACTION READ ONLY")
        yield conn


def database_check(settings):
    with read_only_connection(settings) as conn:
        result = verify(conn)
    return result


def tools_check(settings):
    tools = BusinessTools(lambda: read_only_connection(settings))
    context = ToolContext(run_id=uuid4())
    window = {"since": "2026-11-02T00:00:00+05:30", "until": "2026-11-09T00:00:00+05:30"}
    results = [tools.execute(name, args, context) for name, args in [
        ("get_leads", window),
        ("get_property_matches", {"lead_id": "10000000-0000-4000-8000-000000000001"}),
        ("get_available_slots", window),
    ]]
    if any(result.status != "succeeded" for result in results):
        raise ValueError("Unconfirmed tool check")
    return {"leads_in_window": len(results[0].data["leads"]),
            "seed_lead_matches": len(results[1].data["matches"]),
            "available_slots": len(results[2].data["slots"]),
            "availability_is_provisional": True}


def inspect(*, services=False, settings=None):
    checks = []
    def check(name, operation):
        try:
            details = operation()
            checks.append({"name": name, "status": "ok", "details": details})
            return True
        except Exception:
            # This command may be shared as evidence: never echo settings/provider/DB exceptions.
            checks.append({"name": name, "status": "error", "message": "Check local configuration, connectivity and the setup documentation. No success confirmed."})
            return False

    try:
        settings = settings or Settings()
    except Exception:
        return {"status": "error", "checks": [{"name": "settings", "status": "error",
                "message": "Invalid backend environment; check syntax and values locally."}],
                "model_connectivity": "not_tested", "business_writes": False}

    def credentials():
        if not settings.model_api_key or not settings.model_api_key.get_secret_value().strip() or not settings.model_name.strip():
            raise ValueError("Missing model settings")
        authenticate_owner(settings.owner_api_token.get_secret_value() if settings.owner_api_token else None, settings)
        return {"model_and_owner_configured": True, "model_connectivity": "not_tested"}
    check("credentials", credentials)
    if check("database", lambda: database_check(settings)):
        check("read_only_tools", lambda: tools_check(settings))

    if services:
        def health(url, frontend=False):
            response = httpx.get(url, timeout=5, trust_env=False)
            response.raise_for_status()
            value = response.json()
            if value.get("status") != "ok" or (frontend and value.get("backend", {}).get("status") != "ok"):
                raise ValueError("Invalid health response")
            return {"live": True, "confirms_model_or_database": False}
        check("backend_health", lambda: health("http://127.0.0.1:8000/health"))
        check("frontend_health", lambda: health("http://127.0.0.1:3000/api/health", True))
    return {"status": "ok" if all(item["status"] == "ok" for item in checks) else "error",
            "checks": checks, "model_connectivity": "not_tested", "business_writes": False,
            "note": "Readiness is checked now; live execution can still fail. Zero available slots is valid and must not be presented as bookable inventory."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", action="store_true", help="Also check local backend and frontend liveness")
    result = inspect(services=parser.parse_args().services)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
