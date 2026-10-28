"""Read-only tool smoke check against the fictional demo week. No model call."""

import json
from uuid import uuid4

from app.tools.schemas import ToolContext
from app.tools.service import BusinessTools, tool_definitions


def check() -> int:
    tools = BusinessTools()
    context = ToolContext(run_id=uuid4())  # Reads do not create an execution run.
    window = {"since": "2026-11-02T00:00:00+05:30", "until": "2026-11-09T00:00:00+05:30"}
    requests = [
        ("get_leads", window),
        ("get_property_matches", {"lead_id": "10000000-0000-4000-8000-000000000001"}),
        ("get_available_slots", window),
    ]
    results = [tools.execute(name, args, context) for name, args in requests]
    if any(result.status != "succeeded" for result in results):
        print(json.dumps({"status": "error", "results": [r.model_dump(mode="json") for r in results]}, indent=2))
        return 1
    print(json.dumps({
        "status": "ok", "registered_tools": [tool["name"] for tool in tool_definitions()],
        "demo_week": "2026-11-02/2026-11-08", "read_only": True,
        "leads_in_window": len(results[0].data["leads"]),
        "seed_lead_matches": len(results[1].data["matches"]),
        "available_slots": len(results[2].data["slots"]),
        "availability_requires_recheck": True,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
