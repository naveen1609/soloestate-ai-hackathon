"""Run from backend: python -m scripts.database check|migrate|seed|verify."""

import argparse
import json

import psycopg
from pydantic import ValidationError

from app.config import Settings, EnvironmentFileError
from app.database import connect, DatabaseConfigurationError
from app.migrations import migrate, seed, verify, MigrationError


def run(command: str) -> int:
    try:
        settings = Settings()
        with connect(settings, migration=command != "check") as conn:
            if command == "check":
                assert conn.execute("SELECT 1").fetchone()[0] == 1
                result = {"connected": True}
            elif command == "migrate":
                result = {"applied": migrate(conn)}
            elif command == "seed":
                seed(conn)
                result = {"seeded": True}
            elif command == "verify":
                result = verify(conn)
            else:
                raise ValueError("Unsupported command")
        # Emit success only after the connection context commits successfully.
        print(json.dumps({"status": "ok", **result}))
        return 0
    except (DatabaseConfigurationError, EnvironmentFileError, MigrationError) as exc:
        result = {"code": "migration" if isinstance(exc, MigrationError) else "configuration", "message": str(exc)}
    except psycopg.Error as exc:
        result = {"code": "database_error", "sqlstate": exc.sqlstate,
                  "message": "Database operation failed; check connection, TLS, privileges, and migration order. No success is confirmed."}
    except (ValidationError, OSError):
        result = {"code": "configuration", "message": "Invalid backend settings or unavailable migration/certificate file."}
    print(json.dumps({"status": "error", **result}))
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "migrate", "seed", "verify"])
    raise SystemExit(run(parser.parse_args().command))
