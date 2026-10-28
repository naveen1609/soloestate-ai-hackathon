"""Server-only PostgreSQL connections. Never log connection strings or exceptions."""

import psycopg
from psycopg.conninfo import conninfo_to_dict

from app.config import Settings


class DatabaseConfigurationError(Exception):
    pass


def connection_options(settings: Settings, *, migration: bool = False) -> dict:
    secret = (settings.migration_database_url if migration else None) or settings.database_url
    if secret is None or not secret.get_secret_value().strip():
        raise DatabaseConfigurationError("Set DATABASE_URL in backend/.env; migrations may use MIGRATION_DATABASE_URL.")
    try:
        options = conninfo_to_dict(secret.get_secret_value())
    except psycopg.Error:
        raise DatabaseConfigurationError("Invalid PostgreSQL connection string; use the Supabase Connect URI.") from None
    if "@" in options.get("host", ""):
        raise DatabaseConfigurationError("Database URI contains an unescaped @ in the password; percent-encode password characters (for example @ as %40).")
    # SSL cannot be disabled for a remote server, even if the URI requests it.
    if settings.database_sslmode == "disable" and options.get("host") not in {"127.0.0.1", "localhost", "::1"}:
        raise DatabaseConfigurationError("DATABASE_SSLMODE=disable is permitted only for a loopback PostgreSQL server.")
    options.update(sslmode=settings.database_sslmode, connect_timeout=10, application_name="soloestate-ai",
                   options="-c statement_timeout=15000 -c lock_timeout=5000", prepare_threshold=None)
    if settings.database_sslrootcert:
        options["sslrootcert"] = settings.database_sslrootcert
    return options


def connect(settings: Settings, *, migration: bool = False) -> psycopg.Connection:
    return psycopg.connect(**connection_options(settings, migration=migration))
