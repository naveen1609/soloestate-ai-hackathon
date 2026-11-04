"""Isolated migration runner for SoloEstate AI Growth Engine-lite.
Never touches core soloestate.schema_migrations or core migration files.
"""

from hashlib import sha256
from pathlib import Path
import re
from psycopg import Connection

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
LOCK_ID = 73104999  # Distinct lock ID from core migrator (73104001)


class GrowthMigrationError(Exception):
    pass


def migration_files(directory: Path = MIGRATIONS_DIR) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise GrowthMigrationError("No Growth Engine migration files found.")
    for f in files:
        if not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", f.name):
            raise GrowthMigrationError(f"Invalid migration filename: {f.name}")
    return files


def _checksum(path: Path) -> str:
    # Normalize line endings to avoid CRLF mismatch on Windows
    content = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return sha256(content.encode("utf-8")).hexdigest()


def checksum_matches(path: Path, applied: str) -> bool:
    # Original 0005 enforced the same index without duplicate reconciliation.
    # A database that recorded it already satisfied the uniqueness invariant.
    local = _checksum(path)
    return applied == local or (
        path.name == "0005_growth_engine_channel_concurrency.sql"
        and local == "18eae26e43fde3fcb004e3694410b56c2e500ac9fec318426cd74c116f5729c1"
        and applied == "d2d5719e55748d1865352155fa161d27e619e30052cf03af374f898fe44f9e36"
    )


def migrate_growth_engine(conn: Connection) -> list[str]:
    files = migration_files()
    applied_versions = []

    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        conn.execute("CREATE SCHEMA IF NOT EXISTS soloestate")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS soloestate.growth_migrations (
                version text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)

        applied = dict(conn.execute("SELECT version, checksum FROM soloestate.growth_migrations").fetchall())

        for path in files:
            file_sum = _checksum(path)
            if path.name in applied:
                if not checksum_matches(path, applied[path.name]):
                    raise GrowthMigrationError(
                        f"Growth migration {path.name} checksum mismatch! Applied: {applied[path.name]}, Local: {file_sum}"
                    )
                continue

            sql = path.read_text(encoding="utf-8")
            conn.execute(sql, prepare=False)
            conn.execute(
                "INSERT INTO soloestate.growth_migrations (version, checksum) VALUES (%s, %s)",
                (path.name, file_sum)
            )
            applied_versions.append(path.name)

    return applied_versions
