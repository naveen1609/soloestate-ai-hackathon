"""Issue a two-minute, single-use local dashboard sign-in URL. Never share the URL."""

import json

import psycopg

from app.config import Settings
from app.dashboard_sessions import DashboardSessions
from app.owner import authenticate_owner


def main():
    try:
        settings=Settings()
        authenticate_owner(settings.owner_api_token.get_secret_value() if settings.owner_api_token else None,settings)
        issued=DashboardSessions().issue()
        print(json.dumps({"url":"http://127.0.0.1:3000/#ticket="+issued["ticket"],"expires_at":issued["expires_at"],"single_use":True},indent=2))
        return 0
    except (PermissionError,psycopg.Error):
        print(json.dumps({"status":"error","message":"Could not issue owner sign-in link; check owner/database configuration."}))
        return 1


if __name__=="__main__":
    raise SystemExit(main())
