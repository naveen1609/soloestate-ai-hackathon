"""D2 migration checks (0012/0014/0015 contracts on the retained schema). Rollback only.

Fresh empty-database apply is unexecuted: no dedicated disposable database
is configured (MIGRATION_DATABASE_URL points at the retained application
database, which must never be reset). Recovery strategy is atomic apply:
migrate() runs all pending files in one transaction, so a failed file
leaves no partial state and no ledger row.
"""
from contextlib import contextmanager
from uuid import uuid4

import pytest
from psycopg import errors
from psycopg.rows import dict_row

from app import crm_schemas as s
from app.config import Settings
from app.crm import CRM, scalar
from app.database import connect

pytestmark = pytest.mark.d2_database


@pytest.fixture
def mg(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            prefix = 'd2mg-' + str(uuid4())
            yield dict(crm=CRM(factory), conn=conn, factory=factory, prefix=prefix,
                       key=lambda: prefix + ':' + str(uuid4()))


def test_mig_ledger_records_0012_and_0014(mg):
    with mg['conn'].cursor() as cur:
        rows = cur.execute("SELECT version FROM soloestate.schema_migrations WHERE version IN "
                           "('0012_channel_delivery.sql','0014_attempt_superseded.sql') ORDER BY version").fetchall()
        assert [row[0] for row in rows] == ['0012_channel_delivery.sql', '0014_attempt_superseded.sql']


def test_mig_ledger_records_0015_and_seed_is_disabled(mg):
    with mg['conn'].cursor(row_factory=dict_row) as cur:
        rows = cur.execute("SELECT version FROM soloestate.schema_migrations WHERE version=%s",
                           ('0015_immediate_reply_policy.sql',)).fetchall()
        assert [row['version'] for row in rows] == ['0015_immediate_reply_policy.sql']
        seed = cur.execute("SELECT * FROM soloestate.immediate_reply_policies WHERE id='whatsapp'").fetchone()
        # Seed values are exact; the version is monotonic by rule (fixture
        # restorations advance it, never roll it back), so only a floor holds.
        assert seed['enabled'] is False and seed['categories'] == [] and seed['version'] >= 1
        nullable = cur.execute("SELECT is_nullable FROM information_schema.columns WHERE table_schema='soloestate' "
                               "AND table_name='delivery_outbox' AND column_name IN "
                               "('reply_policy_id','reply_policy_version')").fetchall()
        assert sorted(row['is_nullable'] for row in nullable) == ['YES', 'YES']


def test_mig_conversation_channel_check(mg):
    lead = mg['crm'].save('leads', s.LeadCreate(request_key=mg['key'](), name=mg['prefix'],
                                                email=mg['prefix'] + '@example.invalid'))['record']
    with mg['conn'].cursor() as cur:
        cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id,channel) VALUES (%s,%s,%s,%s)',
                    (uuid4(), lead['id'], None, 'whatsapp'))
        cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id,channel) VALUES (%s,%s,%s,%s)',
                    (uuid4(), lead['id'], None, None))
        with pytest.raises(errors.CheckViolation):
            with mg['conn'].transaction():
                cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id,channel) '
                            'VALUES (%s,%s,%s,%s)', (uuid4(), lead['id'], None, 'sms'))


def test_mig_grounding_validated_and_empty_citations(mg):
    lead = mg['crm'].save('leads', s.LeadCreate(request_key=mg['key'](), name=mg['prefix'],
                                                email=mg['prefix'] + '@example.invalid'))['record']
    with mg['conn'].cursor(row_factory=dict_row) as cur:
        convo = cur.execute('INSERT INTO soloestate.conversations(id,lead_id,property_id) VALUES (%s,%s,%s) RETURNING id',
                            (uuid4(), lead['id'], None)).fetchone()['id']
        op = mg['key']()
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,response) '
                    'VALUES (%s,%s,%s,%s,%s)', (op, 'test:mig', op, 'owner', '{}'))
        cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,grounding,
            citations,request_key) VALUES (%s,1,'assistant','Fictional validated','validated','[]',%s)""",
                    (convo, mg['key']()))
        with pytest.raises(errors.CheckViolation):
            with mg['conn'].transaction():
                cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,grounding,
                    citations,request_key) VALUES (%s,2,'assistant','Fictional','validated','[{}]',%s)""",
                            (convo, mg['key']()))
        with pytest.raises(errors.CheckViolation):
            with mg['conn'].transaction():
                cur.execute("""INSERT INTO soloestate.conversation_messages(conversation_id,seq,role,body,grounding,
                    request_key) VALUES (%s,3,'assistant','Fictional','bogus',%s)""", (convo, mg['key']()))


def test_mig_attempt_single_open_and_superseded(mg):
    lead = mg['crm'].save('leads', s.LeadCreate(request_key=mg['key'](), name=mg['prefix'],
                                                email=mg['prefix'] + '@example.invalid'))['record']
    with mg['conn'].cursor(row_factory=dict_row) as cur:
        outbox = cur.execute("""INSERT INTO soloestate.delivery_outbox(request_key,channel,purpose,source_id,lead_id,
            destination,destination_basis,category,content_ref,policy_version,consent,payload,catalog_version)
            VALUES (%s,'whatsapp','mig','mig',%s,'+15550009999','in_thread','welcome','mig',1,'{}','{}','v') RETURNING id""",
                               (mg['key'](), lead['id'])).fetchone()['id']
        cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,1,%s)',
                    (outbox, 'staged'))
        with pytest.raises(errors.UniqueViolation):
            with mg['conn'].transaction():
                cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,2,%s)',
                            (outbox, 'unknown'))
        cur.execute("UPDATE soloestate.delivery_attempts SET status='superseded' WHERE outbox_id=%s", (outbox,))
        cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,2,%s)',
                    (outbox, 'unknown'))
        with pytest.raises(errors.CheckViolation):
            with mg['conn'].transaction():
                cur.execute('INSERT INTO soloestate.delivery_attempts(outbox_id,attempt_no,status) VALUES (%s,3,%s)',
                            (outbox, 'bogus'))
        with pytest.raises(errors.ForeignKeyViolation):
            with mg['conn'].transaction():
                cur.execute("""INSERT INTO soloestate.delivery_outbox(request_key,channel,purpose,source_id,lead_id,
                    destination,destination_basis,category,content_ref,policy_version,consent,payload,catalog_version)
                    VALUES (%s,'whatsapp','mig','mig2',%s,'+1','in_thread','welcome','m',1,'{}','{}','v')""",
                            (mg['key'](), uuid4()))


def test_mig_webhook_status_and_conflict_fk(mg):
    with mg['conn'].cursor() as cur:
        cur.execute("""INSERT INTO soloestate.webhook_events(provider_event_id,channel,event_kind,provider_msg_id,
            received_at,payload,payload_hash,status) VALUES (%s,'whatsapp','message','wamid.MIG',clock_timestamp(),
            '{}','abc','received')""", ('msg:' + mg['key'](),))
        with pytest.raises(errors.CheckViolation):
            with mg['conn'].transaction():
                cur.execute("""INSERT INTO soloestate.webhook_events(provider_event_id,channel,event_kind,provider_msg_id,
                    received_at,payload,payload_hash,status) VALUES (%s,'whatsapp','message','wamid.MIG2',
                    clock_timestamp(),'{}','abc','bogus')""", ('msg:' + mg['key'](),))
        with pytest.raises(errors.ForeignKeyViolation):
            with mg['conn'].transaction():
                cur.execute('INSERT INTO soloestate.webhook_conflicts(provider_event_id,payload,payload_hash,received_at) '
                            'VALUES (%s,%s,%s,clock_timestamp())', ('missing:' + mg['key'](), '{}', 'abc'))


def test_mig_rls_enabled_and_no_public_access(mg):
    tables = ('conversation_summaries', 'delivery_outbox', 'delivery_attempts', 'webhook_events',
              'webhook_conflicts', 'immediate_reply_policies')
    with mg['conn'].cursor() as cur:
        for table in tables:
            assert scalar(cur.execute('SELECT rowsecurity FROM pg_tables WHERE schemaname=%s AND tablename=%s',
                                      ('soloestate', table)).fetchone()) is True
            assert scalar(cur.execute('SELECT has_table_privilege(%s, %s, %s)',
                                      ('public', 'soloestate.' + table, 'SELECT')).fetchone()) is False
