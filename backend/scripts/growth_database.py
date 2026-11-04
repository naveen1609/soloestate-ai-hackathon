"""CLI runner for SoloEstate AI Growth Engine-lite migrations.
Run from backend:
    python -m scripts.growth_database check [--database-url <URL>]
    python -m scripts.growth_database migrate [--database-url <URL>]
    python -m scripts.growth_database verify [--database-url <URL>]

Supports TEST_DATABASE_URL environment variable for disposable PostgreSQL instances.
Never modifies core migrations or core schema history.
"""

import argparse
import json
import os
import sys

import psycopg
from pydantic import ValidationError, SecretStr

from app.config import Settings, EnvironmentFileError
from app.database import connect, DatabaseConfigurationError
from app.growth_engine.migrator import migrate_growth_engine, GrowthMigrationError, migration_files, _checksum, checksum_matches


def run(command: str, database_url: str | None = None) -> int:
    try:
        url = database_url or os.environ.get("TEST_DATABASE_URL")
        if url:
            # Connect directly to provided URL
            conn = psycopg.connect(url, autocommit=False)
        else:
            settings = Settings()
            # Safety check against accidental production runs
            if settings.database_url:
                db_str = settings.database_url.get_secret_value()
                if "supabase" in db_str and os.environ.get("ALLOW_PRODUCTION_GROWTH_MIGRATE") != "1":
                    print(json.dumps({
                        "status": "error",
                        "code": "production_blocked",
                        "message": "Direct migration against Supabase production is blocked. Provide a disposable database via --database-url or TEST_DATABASE_URL."
                    }))
                    return 1
            conn = connect(settings, migration=True)

        with conn:
            if command == "check":
                assert conn.execute("SELECT 1").fetchone()[0] == 1
                result = {"connected": True}
            elif command == "migrate":
                applied = migrate_growth_engine(conn)
                result = {"applied": applied}
            elif command == "verify":
                files = migration_files()
                history = dict(conn.execute("SELECT version, checksum FROM soloestate.growth_migrations").fetchall())
                for f in files:
                    local_sum = _checksum(f)
                    if f.name not in history:
                        raise GrowthMigrationError(f"Migration {f.name} has not been applied.")
                    if not checksum_matches(f, history[f.name]):
                        raise GrowthMigrationError(f"Migration {f.name} checksum mismatch! Applied: {history[f.name]}, Local: {local_sum}")
                result = {"verified": True, "applied_count": len(history)}
            else:
                raise ValueError("Unsupported command")

        print(json.dumps({"status": "ok", **result}))
        return 0
    except (DatabaseConfigurationError, EnvironmentFileError, GrowthMigrationError) as exc:
        result = {"code": "migration" if isinstance(exc, GrowthMigrationError) else "configuration", "message": str(exc)}
    except psycopg.Error as exc:
        result = {
            "code": "database_error",
            "sqlstate": getattr(exc, "sqlstate", None),
            "message": f"Database operation failed: {exc}",
        }
    except (ValidationError, OSError) as exc:
        result = {"code": "configuration", "message": f"Invalid backend settings: {exc}"}
    except Exception as exc:
        result = {"code": "error", "message": str(exc)}

    print(json.dumps({"status": "error", **result}))
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "migrate", "verify"])
    parser.add_argument("--database-url", default=None, help="PostgreSQL connection string (e.g. for disposable test DB)")
    args = parser.parse_args()
    sys.exit(run(args.command, args.database_url))
