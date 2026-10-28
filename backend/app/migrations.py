"""Execute only repository-owned migration and seed files; no user/model SQL."""

from hashlib import sha256
from pathlib import Path
import re

from psycopg import Connection
from psycopg import sql

SQL_ROOT = Path(__file__).resolve().parents[2] / "supabase"
LOCK_ID = 73104001


class MigrationError(Exception):
    pass


def migration_files(root: Path = SQL_ROOT) -> list[Path]:
    files = sorted((root / "migrations").glob("*.sql"))
    if not files or any(not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", path.name) for path in files):
        raise MigrationError("Migration files are missing or have invalid names.")
    if len({path.name[:4] for path in files}) != len(files):
        raise MigrationError("Duplicate migration version detected.")
    return files


def _checksum(path: Path) -> str:
    # Git CRLF conversion on Windows must not cause false migration drift.
    return sha256(path.read_text(encoding="utf-8").replace("\r\n", "\n").encode()).hexdigest()


def _validate_history(conn: Connection, files: list[Path]) -> dict[str, str]:
    applied = dict(conn.execute("SELECT version, checksum FROM soloestate.schema_migrations").fetchall())
    local = {path.name: _checksum(path) for path in files}
    if any(local.get(name) != checksum for name, checksum in applied.items()):
        raise MigrationError("Applied migration differs from repository files; restore it and add a new migration.")
    return applied


def migrate(conn: Connection, root: Path = SQL_ROOT) -> list[str]:
    files = migration_files(root)
    added = []
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        conn.execute("CREATE SCHEMA IF NOT EXISTS soloestate")
        conn.execute("REVOKE ALL ON SCHEMA soloestate FROM PUBLIC")
        conn.execute("""CREATE TABLE IF NOT EXISTS soloestate.schema_migrations (
            version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS soloestate.seed_history (
            name text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
        conn.execute("ALTER TABLE soloestate.schema_migrations ENABLE ROW LEVEL SECURITY")
        conn.execute("ALTER TABLE soloestate.seed_history ENABLE ROW LEVEL SECURITY")
        conn.execute("REVOKE ALL ON soloestate.schema_migrations, soloestate.seed_history FROM PUBLIC")
        applied = _validate_history(conn, files)
        for path in files:
            if path.name in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"), prepare=False)
            conn.execute("INSERT INTO soloestate.schema_migrations (version, checksum) VALUES (%s, %s)",
                         (path.name, _checksum(path)))
            added.append(path.name)
    return added


def seed(conn: Connection, root: Path = SQL_ROOT) -> None:
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
        files = migration_files(root)
        applied = _validate_history(conn, files)
        if set(applied) != {path.name for path in files}:
            raise MigrationError("Apply all migrations before seeding.")
        path = root / "seed.sql"
        checksum = _checksum(path)
        previous = conn.execute("SELECT checksum FROM soloestate.seed_history WHERE name = %s", (path.name,)).fetchone()
        if previous and previous[0] != checksum:
            raise MigrationError("Seed file changed since application; use a reviewed migration for existing data.")
        conn.execute(path.read_text(encoding="utf-8"), prepare=False)
        conn.execute("INSERT INTO soloestate.seed_history (name, checksum) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
                     (path.name, checksum))


def verify(conn: Connection, root: Path = SQL_ROOT) -> dict:
    files = migration_files(root)
    applied = _validate_history(conn, files)
    if set(applied) != {path.name for path in files}:
        raise MigrationError("Pending migrations; run the migrate command.")
    seed_record = conn.execute("SELECT checksum FROM soloestate.seed_history WHERE name = %s", ("seed.sql",)).fetchone()
    if not seed_record or seed_record[0] != _checksum(root / "seed.sql"):
        raise MigrationError("Seed history is missing or differs from the repository seed file.")
    # Fixed queries only. Count the exact demo IDs, not unrelated business records.
    counts = {}
    for table, prefix, total in [("leads", "10000000", 4), ("properties", "20000000", 3), ("visit_slots", "30000000", 6)]:
        ids = [f"{prefix}-0000-4000-8000-{i:012d}" for i in range(1, total + 1)]
        count = conn.execute(sql.SQL("SELECT count(*) FROM soloestate.{} WHERE id = ANY(%s::uuid[])").format(sql.Identifier(table)), (ids,)).fetchone()[0]
        if count != total:
            raise MigrationError("Fictional seed records are missing; run the seed command.")
        counts[table] = count
    return {"migrations": len(applied), "seed_records": counts, "demo_week": "2026-11-02/2026-11-08", "currency": "INR"}
