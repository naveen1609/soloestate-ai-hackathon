"""Real PostgreSQL tests. Require a dedicated EMPTY database, never app data."""

import os
from uuid import uuid4

import psycopg
import pytest

from app.config import Settings
from app.database import connect
from app.migrations import migrate, seed, verify
from app.tools.service import BusinessTools
from app.tools.schemas import ToolContext
from app.owner import OwnerService, DecisionRequest
from contextlib import contextmanager

pytestmark = pytest.mark.database


@pytest.fixture
def db():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a dedicated empty PostgreSQL test database")
    settings = Settings(_env_file=None, database_url=url,
                        database_sslmode=os.environ.get("TEST_DATABASE_SSLMODE", "verify-full"),
                        database_sslrootcert=os.environ.get("TEST_DATABASE_SSLROOTCERT"))
    with connect(settings) as conn:
        if conn.execute("SELECT to_regnamespace('soloestate')").fetchone()[0] is not None:
            pytest.fail("Refusing to run integration tests: soloestate schema already exists; use an empty test database.")
        with conn.transaction(force_rollback=True):
            migrate(conn)
            seed(conn)
            yield conn


def test_migrations_and_seed_are_repeatable(db):
    assert migrate(db) == []
    seed(db)
    assert verify(db)["seed_records"] == {"leads": 4, "properties": 3, "visit_slots": 6}
    assert db.execute("SELECT count(*) FROM soloestate.runs").fetchone()[0] == 0


def test_budget_and_foreign_key_constraints(db):
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            db.execute("UPDATE soloestate.leads SET budget_max = -1")
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.transaction():
            db.execute("INSERT INTO soloestate.followup_drafts (id, run_id, lead_id, idempotency_key, subject, body) VALUES (%s,%s,%s,'bad','Hello','Body')", (uuid4(), uuid4(), uuid4()))


def test_overlapping_slots_rejected_adjacent_allowed(db):
    with pytest.raises(psycopg.errors.ExclusionViolation):
        with db.transaction():
            db.execute("INSERT INTO soloestate.visit_slots VALUES (%s,'2026-11-05T04:45:00Z','2026-11-05T05:15:00Z',true)", (uuid4(),))
    db.execute("INSERT INTO soloestate.visit_slots VALUES (%s,'2026-11-05T05:30:00Z','2026-11-05T06:00:00Z',true)", (uuid4(),))


def test_booking_uniqueness_and_approval_binding(db):
    run = uuid4()
    lead = "10000000-0000-4000-8000-000000000001"
    slot = "30000000-0000-4000-8000-000000000001"
    prop = "20000000-0000-4000-8000-000000000001"
    db.execute("INSERT INTO soloestate.runs (id, request_key, goal) VALUES (%s,'test-run','Test goal')", (run,))
    @contextmanager
    def factory():
        with db.transaction():
            yield db
    tools, owner = BusinessTools(factory), OwnerService(factory)
    approvals = []
    for i in range(2):
        result = tools.execute("request_approval", {"kind":"booking","lead_id":lead,"slot_id":slot,"property_id":prop,"idempotency_key":f"approval-{i}"}, ToolContext(run_id=run))
        approval = result.data["approval"]["id"]
        owner.decide(approval, DecisionRequest(decision="approved",expected_payload_hash=owner.review(approval)["payload_hash"],idempotency_key=str(uuid4())))
        approvals.append(approval)
    insert = """INSERT INTO soloestate.bookings (id,run_id,approval_id,lead_id,slot_id,property_id,idempotency_key)
        VALUES (%s,%s,%s,%s,%s,%s,%s)"""
    db.execute(insert, (uuid4(), run, approvals[0], lead, slot, prop, "booking-1"))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with db.transaction():
            db.execute(insert, (uuid4(), run, approvals[1], lead, slot, prop, "booking-2"))
    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation)):
        with db.transaction():
            db.execute(insert, (uuid4(), run, approvals[1], lead, "30000000-0000-4000-8000-000000000002", prop, "wrong-slot"))
    db.execute("UPDATE soloestate.bookings SET status = 'cancelled'")
    db.execute(insert, (uuid4(), run, approvals[1], lead, slot, prop, "booking-2"))


def test_rls_enabled_and_no_public_schema_access(db):
    rows = db.execute("""SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='soloestate' AND c.relkind='r'""").fetchall()
    assert len(rows) == 14
    assert all(row[0] for row in rows)
    public_access = db.execute("""SELECT EXISTS (SELECT 1 FROM pg_namespace n,
        LATERAL aclexplode(n.nspacl) a WHERE n.nspname='soloestate' AND a.grantee=0 AND a.privilege_type='USAGE')""").fetchone()[0]
    assert not public_access
