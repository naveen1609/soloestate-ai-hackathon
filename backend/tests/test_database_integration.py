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


def test_visitor_actor_migration_applies_on_fresh_database(db):
    versions = [row[0] for row in db.execute('SELECT version FROM soloestate.schema_migrations ORDER BY version').fetchall()]
    assert len(versions) == 11 and versions[-1] == '0011_policy_resources_identity.sql'
    for table in ('crm_operations', 'lead_activities'):
        definition = db.execute('SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname=%s',
                                (f'{table}_actor_check',)).fetchone()[0]
        assert "'owner'" in definition and "'visitor'" in definition
    db.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) "
               "VALUES ('legacy-owner','probe','probe','owner',%s,'{}')", (uuid4(),))
    db.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) "
               "VALUES ('legacy-visitor','probe','probe','visitor',%s,'{}')", (uuid4(),))
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.transaction():
            db.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) "
                       "VALUES ('legacy-third','probe','probe','agent',%s,'{}')", (uuid4(),))
    assert db.execute("SELECT count(*) FROM soloestate.crm_operations WHERE request_key='legacy-third'").fetchone()[0] == 0
    lead, convo = uuid4(), uuid4()
    db.execute('INSERT INTO soloestate.leads(id,name,email) VALUES (%s,%s,%s)', (lead, 'C migration', 'c-migration@example.invalid'))
    db.execute('INSERT INTO soloestate.conversations(id,lead_id) VALUES (%s,%s)', (convo, lead))
    db.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) "
               "VALUES ('chain-op','probe','probe','visitor',%s,'{}')", (uuid4(),))
    message = db.execute('INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,request_key) '
                         "VALUES (%s,1,'customer','Hello','chain-op') RETURNING id", (convo,)).fetchone()[0]
    db.execute("INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key) "
               "VALUES (%s,'visitor','probe','{}','{}','chain-op')", (lead,))
    joined = db.execute("""SELECT m.id FROM soloestate.conversation_messages m
        JOIN soloestate.crm_operations o ON o.request_key=m.request_key
        JOIN soloestate.lead_activities a ON a.request_key=o.request_key
        WHERE m.id=%s AND o.actor='visitor' AND a.actor='visitor'""", (message,)).fetchone()
    assert joined[0] == message
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with db.transaction():
            db.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) "
                       "VALUES ('rollback-op','probe','probe','visitor',%s,'{}')", (uuid4(),))
            db.execute("INSERT INTO soloestate.lead_activities(lead_id,actor,reason,before_state,after_state,request_key) "
                       "VALUES (%s,'visitor','probe','{}','{}','rollback-op')", (uuid4(),))
    assert db.execute("SELECT count(*) FROM soloestate.crm_operations WHERE request_key='rollback-op'").fetchone()[0] == 0
    secured = db.execute("SELECT relname FROM pg_class WHERE relnamespace='soloestate'::regnamespace AND relname IN "
                         "('crm_operations','lead_activities') AND relrowsecurity").fetchall()
    assert {row[0] for row in secured} == {'crm_operations', 'lead_activities'}
    public = db.execute("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='PUBLIC' AND "
                        "table_schema='soloestate' AND table_name IN ('crm_operations','lead_activities')").fetchone()[0]
    assert public == 0


def test_rls_enabled_and_no_public_schema_access(db):
    rows = db.execute("""SELECT c.relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='soloestate' AND c.relkind='r'""").fetchall()
    assert len(rows) == 27
    assert all(row[0] for row in rows)
    public_access = db.execute("""SELECT EXISTS (SELECT 1 FROM pg_namespace n,
        LATERAL aclexplode(n.nspacl) a WHERE n.nspname='soloestate' AND a.grantee=0 AND a.privilege_type='USAGE')""").fetchone()[0]
    assert not public_access
