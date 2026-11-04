"""Owner-managed immediate-reply messaging policy (migration 0015).

Covers the policy lifecycle (default-disabled seed, versioned/idempotent
owner updates, routine-only categories), dispatch binding at stage, current
revalidation at staged → submitting, legacy fail-closed with restage rebind,
and owner-mediated paths never bypassing the live policy.

Rollback by default; the update-vs-transition race commits with exact-ID
cleanup plus caps/policy save/restore.
"""
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Barrier, Thread
from uuid import uuid4

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app import crm_schemas as s
from app import dispatch
from app.catalog import CATALOG_VERSION
from app.config import Settings
from app.crm import CRM, CRMError, scalar
from app.database import connect
from app.services.meta_model import ModelResult

pytestmark = pytest.mark.d2_database


class StubModel:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    async def generate(self, prompt):
        self.calls += 1
        return ModelResult(response_id='resp-%d' % self.calls, model='test-meta',
                           text=self.texts[min(self.calls - 1, len(self.texts) - 1)])


class StubSubmit:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, recipient, text):
        self.calls.append((recipient, text))
        outcome = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def rp(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    with connect(Settings()) as conn:
        with conn.transaction(force_rollback=True):
            @contextmanager
            def factory():
                with conn.transaction():
                    yield conn
            prefix = 'd2rp-' + str(uuid4())
            yield dict(crm=CRM(factory), conn=conn, factory=factory, prefix=prefix,
                       key=lambda: prefix + ':' + str(uuid4()))


def _sender():
    return '1555%07d' % (uuid4().int % 10000000)


def _policy(b):
    with b['conn'].cursor(row_factory=dict_row) as cur:
        return cur.execute("SELECT * FROM soloestate.immediate_reply_policies WHERE id='whatsapp'").fetchone()


def _enable(b, categories=('welcome',)):
    from app.policies import update_reply_policy
    return update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=_policy(b)['version'], enabled=True,
        categories=list(categories), reason='Fictional D2 test enable'))


def _disable(b):
    from app.policies import update_reply_policy
    return update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=_policy(b)['version'], enabled=False,
        reason='Fictional D2 test disable'))


def _inbound(b, sender, body='Fictional hello'):
    from app import identity_links
    now = datetime.now(timezone.utc)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        link, _ = identity_links.observe(cur, 'whatsapp', sender, b['key']())
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s ORDER BY created_at DESC LIMIT 1',
                            (link['lead_id'],)).fetchone()
        wamid = 'wamid.RP' + uuid4().hex[:12]
        op_key = 'wa:inbound:' + wamid
        response = {'provider_ts': now.isoformat(),
                    'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                         'link_id': str(link['id'])}}
        cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                    'VALUES (%s,%s,%s,%s,%s,%s)',
                    (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb(response)))
        seq = cur.execute('SELECT coalesce(max(seq),0)+1 AS next FROM soloestate.conversation_messages '
                          'WHERE conversation_id=%s', (convo['id'],)).fetchone()['next']
        message = cur.execute('INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                              'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                              (uuid4(), convo['id'], seq, 'customer', body, op_key)).fetchone()
        cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        lead = cur.execute('SELECT * FROM soloestate.leads WHERE id=%s', (link['lead_id'],)).fetchone()
        convo = cur.execute('SELECT * FROM soloestate.conversations WHERE id=%s', (convo['id'],)).fetchone()
    return dict(link=link, lead=lead, convo=convo, message=message)


def _stage_kwargs(b, box, message, category='welcome'):
    return dict(request_key=dispatch.stable_key('whatsapp', 'reply', str(message['id'])),
                channel='whatsapp', purpose='reply', source_id=str(message['id']),
                lead_id=box['lead']['id'], conversation_id=box['convo']['id'], link_id=box['link']['id'],
                destination='+' + box['link']['provider_ref'], category=category,
                content_ref='reply:' + str(message['id']), catalog_version=CATALOG_VERSION,
                payload={'body': 'Fictional reply', 'catalog_version': CATALOG_VERSION},
                fact_deps=[])


def _used(b, day):
    with b['conn'].cursor() as cur:
        row = cur.execute('SELECT used FROM soloestate.delivery_caps WHERE day=%s AND channel=%s',
                          (day, 'whatsapp')).fetchone()
        return row[0] if row else 0


def _api(b, authed=True):
    from fastapi.testclient import TestClient

    from app.crm_api import service
    from app.main import create_app
    from app.owner_api import require_owner

    app = create_app()
    if authed:
        app.dependency_overrides[require_owner] = lambda: 'owner'
    app.dependency_overrides[service] = lambda: b['crm']
    return TestClient(app)


def test_rp01_default_disabled_blocks_stage(rp):
    b = rp
    assert _policy(b)['enabled'] is False
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    assert out['created'] is True and out['record']['status'] == 'skipped'
    assert out['record']['skip_reason'] == 'policy_disabled'
    assert out['record']['reply_policy_id'] is None
    with b['conn'].cursor() as cur:
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_attempts WHERE outbox_id=%s',
                                  (out['record']['id'],)).fetchone()) == 0


def test_rp02_missing_policy_blocks_stage_and_transition(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    assert row['status'] == 'staged'
    with b['conn'].cursor() as cur:
        cur.execute("DELETE FROM soloestate.immediate_reply_policies WHERE id='whatsapp'")
    box2 = _inbound(b, _sender())
    out2 = dispatch.stage(b['crm'], **_stage_kwargs(b, box2, box2['message']))
    assert out2['record']['status'] == 'skipped' and out2['record']['skip_reason'] == 'reply_policy_missing'
    submit = StubSubmit(['wamid.NEVER'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='rp02', submit=submit)
    assert driven['status'] == 'blocked' and driven['reasons'] == ['reply_policy_missing']
    assert submit.calls == [] and _used(b, row['reserved_day']) == 0


def test_rp03_enabled_permits_submit_with_binding(rp):
    from app.policies import get_reply_policy
    b = rp
    box = _inbound(b, _sender())
    before_v = _policy(b)['version']
    updated = _enable(b, ('welcome', 'faq_answer'))
    assert updated['record']['enabled'] is True and updated['record']['version'] == before_v + 1
    assert updated['before']['enabled'] is False and updated['reason'] == 'Fictional D2 test enable'
    with b['conn'].cursor(row_factory=dict_row) as cur:
        op = cur.execute("SELECT actor, response FROM soloestate.crm_operations WHERE action='reply-policy:update' "
                         'ORDER BY created_at DESC LIMIT 1').fetchone()
        assert op['actor'] == 'owner' and op['response']['before']['enabled'] is False
    assert get_reply_policy(b['crm'])['record']['enabled'] is True
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    assert out['record']['status'] == 'staged'
    assert out['record']['reply_policy_id'] == 'whatsapp' and out['record']['reply_policy_version'] == updated['record']['version']
    submit = StubSubmit(['wamid.RP03'])
    assert dispatch.drive_row(b['factory'], out['record']['id'], worker='rp03', submit=submit)['status'] == 'sent'
    assert len(submit.calls) == 1


def test_rp04_api_rejects_sensitive_unknown_and_empty_enable(rp):
    from app.policies import update_reply_policy
    b = rp
    live_v = _policy(b)['version']
    with pytest.raises(CRMError, match='cannot be enabled'):
        update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
            request_key=b['key'](), expected_version=live_v, enabled=True,
            categories=['welcome', 'negotiation'], reason='Fictional D2 sensitive attempt'))
    with pytest.raises(CRMError, match='Unknown message categories'):
        update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
            request_key=b['key'](), expected_version=live_v, categories=['nope'],
            reason='Fictional D2 unknown attempt'))
    with pytest.raises(CRMError, match='at least one routine category'):
        update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
            request_key=b['key'](), expected_version=live_v, enabled=True, categories=[],
            reason='Fictional D2 empty attempt'))
    assert _policy(b)['version'] == live_v and _policy(b)['enabled'] is False


def test_rp05_sensitive_category_never_stages(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    with pytest.raises(CRMError, match='non-routine'):
        dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message'], category='negotiation'))


def test_rp06_disable_in_window_blocks_without_submit(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    assert row['status'] == 'staged'
    _disable(b)
    submit = StubSubmit(['wamid.NEVER'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='rp06', submit=submit)
    assert driven['status'] == 'blocked' and driven['reasons'] == ['policy_disabled']
    assert submit.calls == []
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute('SELECT status FROM soloestate.delivery_outbox WHERE id=%s',
                           (row['id'],)).fetchone()['status'] == 'queued'
    assert _used(b, row['reserved_day']) == 0


def test_rp07_category_removed_in_window_blocks(rp):
    from app.policies import update_reply_policy
    b = rp
    _enable(b, ('welcome', 'faq_answer'))
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=_policy(b)['version'],
        categories=['faq_answer'], reason='Fictional D2 narrow'))
    submit = StubSubmit(['wamid.NEVER'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='rp07', submit=submit)
    assert driven['status'] == 'blocked' and driven['reasons'] == ['category_not_allowed']
    assert submit.calls == []
    assert _used(b, row['reserved_day']) == 0


def _committed_lead(sender):
    from app import identity_links
    now = datetime.now(timezone.utc)
    observe_key = 'd2rp-observe:' + str(uuid4())
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            link, _ = identity_links.observe(cur, 'whatsapp', sender, observe_key)
            cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        (observe_key, 'identity-link:observe', 'd2rp:' + observe_key, 'channel', link['id'],
                         Jsonb({'record': {'id': str(link['id'])}})))
            convo = cur.execute('SELECT * FROM soloestate.conversations WHERE lead_id=%s '
                                'ORDER BY created_at DESC LIMIT 1', (link['lead_id'],)).fetchone()
            cur.execute("UPDATE soloestate.conversations SET channel='whatsapp' WHERE id=%s", (convo['id'],))
            wamid = 'wamid.RP' + uuid4().hex[:12]
            op_key = 'wa:inbound:' + wamid
            response = {'provider_ts': now.isoformat(),
                        'inbound_evidence': {'channel': 'whatsapp', 'provider_ref': sender,
                                             'link_id': str(link['id'])}}
            cur.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) '
                        'VALUES (%s,%s,%s,%s,%s,%s)',
                        (op_key, 'channel:inbound', 'wa:' + wamid, 'channel', link['id'], Jsonb(response)))
            message = cur.execute(
                'INSERT INTO soloestate.conversation_messages(id,conversation_id,seq,role,body,request_key) '
                'VALUES (%s,%s,%s,%s,%s,%s) RETURNING *',
                (uuid4(), convo['id'], 1, 'customer', 'Fictional hello', op_key)).fetchone()
    return dict(link_id=str(link['id']), lead_id=str(link['lead_id']), convo_id=str(convo['id']),
                message_id=str(message['id']), op_keys=[observe_key, op_key])


def _policy_enable_committed():
    from app.policies import ROUTINE_CATEGORIES
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            before = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                                 "WHERE id='whatsapp' FOR UPDATE").fetchone()
            owned = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=true, categories=%s, '
                                'version=version+1, updated_at=clock_timestamp() WHERE id=%s '
                                'RETURNING enabled, categories, version',
                                (sorted(ROUTINE_CATEGORIES), 'whatsapp')).fetchone()
    return {'before': {'enabled': before['enabled'], 'categories': list(before['categories'])},
            'owned': {'enabled': owned['enabled'], 'categories': list(owned['categories']),
                      'version': owned['version']}}


def _policy_restore_committed(state):
    """Compare-and-set restore of before-values; None, else a conflict message.

    Versions advance monotonically and are never decremented or reused; a
    lost race preserves the current row untouched.
    """
    before, owned = state['before'], state['owned']
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            won = cur.execute('UPDATE soloestate.immediate_reply_policies SET enabled=%s, categories=%s, '
                              'version=version+1, updated_at=clock_timestamp() WHERE id=%s AND version=%s '
                              'AND enabled=%s AND categories=%s RETURNING version',
                              (before['enabled'], before['categories'], 'whatsapp', owned['version'],
                               owned['enabled'], owned['categories'])).fetchone()
            if won is not None:
                return None
            current = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                                  "WHERE id='whatsapp'").fetchone()
    return ('immediate-reply-policy restore refused: row changed since setup '
            '(owned version %s; current version %s, enabled=%s); preserved current state'
            % (owned['version'], current['version'], current['enabled']))


def _policy_value_restore_committed(state, request_key):
    """Owner-style restore of before-values at the live version (monotonic).

    Only for tests that deliberately mutate the policy mid-test (the race arm
    above): both mutations are test-owned, so no conflict check applies. Uses
    the production versioned update, leaving one audit row the caller removes
    by exact request key. Skips when values already match.
    """
    from app.policies import update_reply_policy
    before = state['before']
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            live = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                               "WHERE id='whatsapp'").fetchone()
    if live['enabled'] == before['enabled'] and list(live['categories']) == list(before['categories']):
        return None
    return update_reply_policy(CRM(), s.ReplyPolicyUpdate(
        request_key=request_key, expected_version=live['version'],
        enabled=before['enabled'], categories=list(before['categories']),
        reason='Fictional D2 race value restore'))


def _cleanup_committed(box):
    from app.caps import refund_cap
    with connect(Settings()) as conn:
        held = conn.execute('SELECT reserved_day FROM soloestate.delivery_outbox WHERE lead_id=%s AND reserved',
                            (box['lead_id'],)).fetchall()
        for (rday,) in held:
            refund_cap(conn, rday, 'whatsapp')
        conn.execute('DELETE FROM soloestate.delivery_outbox WHERE lead_id=%s', (box['lead_id'],))
        conn.execute('DELETE FROM soloestate.conversation_messages WHERE conversation_id=%s', (box['convo_id'],))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (box['lead_id'],))
        conn.execute('DELETE FROM soloestate.conversations WHERE id=%s', (box['convo_id'],))
        conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key = ANY(%s)', (box['op_keys'],))
        conn.execute('DELETE FROM soloestate.identity_links WHERE id=%s', (box['link_id'],))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (box['lead_id'],))
    with connect(Settings()) as conn:
        assert conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=%s',
                            (box['lead_id'],)).fetchone()[0] == 0


def test_rp08_update_race_with_transition_serializes_safely(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')
    from datetime import date
    sender = _sender()
    box = _committed_lead(sender)
    policy_state = _policy_enable_committed()
    crm = CRM()
    restore_key = 'rp08-restore-' + str(uuid4())

    @contextmanager
    def committed_factory():
        with connect(Settings()) as conn:
            yield conn

    kwargs = dict(request_key=dispatch.stable_key('whatsapp', 'reply', box['message_id']),
                  channel='whatsapp', purpose='reply', source_id=box['message_id'],
                  lead_id=box['lead_id'], conversation_id=box['convo_id'], link_id=box['link_id'],
                  destination='+' + sender, category='welcome',
                  content_ref='reply:' + box['message_id'], catalog_version=CATALOG_VERSION,
                  payload={'body': 'Fictional reply', 'catalog_version': CATALOG_VERSION},
                  fact_deps=[])
    staged = dispatch.stage(crm, **kwargs)
    assert staged['record']['status'] == 'staged'
    day = staged['record']['reserved_day']
    day = date.fromisoformat(day) if isinstance(day, str) else day
    submit_calls = []
    barrier = Barrier(2)
    errors = {}

    def drive():
        try:
            barrier.wait(timeout=60)
            return dispatch.drive_row(committed_factory, staged['record']['id'], worker='rp08',
                                      submit=lambda recipient, text: submit_calls.append((recipient, text)) or 'wamid.RP08')
        except Exception as exc:  # noqa: BLE001 - surfaced below, never swallowed
            errors['drive'] = exc

    def disable():
        try:
            barrier.wait(timeout=60)
            with connect(Settings()) as conn:
                conn.execute('UPDATE soloestate.immediate_reply_policies SET enabled=false, version=version+1, '
                             "updated_at=clock_timestamp() WHERE id='whatsapp'")
        except Exception as exc:  # noqa: BLE001 - surfaced below, never swallowed
            errors['disable'] = exc

    threads = [Thread(target=drive), Thread(target=disable)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        assert not any(thread.is_alive() for thread in threads), 'race thread hung'
        assert not errors, errors
        with connect(Settings()) as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                final = cur.execute('SELECT status FROM soloestate.delivery_outbox WHERE id=%s',
                                    (staged['record']['id'],)).fetchone()['status']
                used = cur.execute('SELECT used FROM soloestate.delivery_caps WHERE day=%s AND channel=%s',
                                   (day, 'whatsapp')).fetchone()['used']
        # Either order is safe: a sent row consumed exactly one unit with one
        # provider call; a blocked row kept the provider silent and refunded.
        if final == 'sent':
            assert len(submit_calls) == 1 and used == 1
        else:
            assert final == 'queued' and submit_calls == [] and used == 0
    finally:
        _cleanup_committed(box)
        _policy_value_restore_committed(policy_state, restore_key)
        with connect(Settings()) as conn:
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=%s', (restore_key,))


def test_rp09_stale_version_rejected_without_changes(rp):
    from app.policies import update_reply_policy
    b = rp
    live_v = _policy(b)['version']
    with pytest.raises(CRMError, match='changed'):
        update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
            request_key=b['key'](), expected_version=live_v + 500, enabled=True,
            categories=['welcome'], reason='Fictional D2 stale attempt'))
    assert _policy(b)['version'] == live_v and _policy(b)['enabled'] is False


def test_rp10_identical_replay_and_changed_conflict(rp):
    from app.policies import update_reply_policy
    b = rp
    key = b['key']()
    live_v = _policy(b)['version']
    first = update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=key, expected_version=live_v, enabled=True,
        categories=['welcome'], reason='Fictional D2 replay'))
    second = update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=key, expected_version=live_v, enabled=True,
        categories=['welcome'], reason='Fictional D2 replay'))
    assert second == first and _policy(b)['version'] == live_v + 1
    with pytest.raises(CRMError, match='different operation'):
        update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
            request_key=key, expected_version=live_v + 1, enabled=True,
            categories=['faq_answer'], reason='Fictional D2 changed'))


def test_rp11_retry_requeue_cannot_bypass_disabled(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    failed = dispatch.SubmitFailed('permanent provider rejection')
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp11',
                              submit=StubSubmit([failed])) == {'status': 'dead'}
    _disable(b)
    with pytest.raises(CRMError, match='disabled'):
        dispatch.requeue(b['crm'], row['id'], dispatch.OutboxRequeue(
            request_key=b['key'](), reason='Fictional D2 bypass attempt'))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute('SELECT status FROM soloestate.delivery_outbox WHERE id=%s',
                           (row['id'],)).fetchone()['status'] == 'dead'
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_attempts WHERE outbox_id=%s',
                                  (row['id'],)).fetchone()) == 3
    _enable(b)
    dispatch.requeue(b['crm'], row['id'], dispatch.OutboxRequeue(
        request_key=b['key'](), reason='Fictional D2 legitimate requeue'))
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp11b',
                              submit=StubSubmit(['wamid.RP11']))['status'] == 'sent'


def test_rp12_unknown_ignores_policy_but_resend_obeys_it(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp12',
                              submit=StubSubmit([dispatch.SubmitUnknown('timeout')])) == {'status': 'unknown'}
    _disable(b)
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp12b',
                              submit=StubSubmit(['wamid.NEVER'])) == {'status': 'unknown'}
    with pytest.raises(CRMError, match='disabled'):
        dispatch.resolve_unknown(b['crm'], row['id'], dispatch.OutboxResolve(
            request_key=b['key'](), action='authorize_resend', reason='Fictional D2 bypass attempt'))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute('SELECT status FROM soloestate.delivery_outbox WHERE id=%s',
                           (row['id'],)).fetchone()['status'] == 'unknown'
        assert scalar(cur.execute('SELECT count(*) FROM soloestate.delivery_attempts WHERE outbox_id=%s',
                                  (row['id'],)).fetchone()) == 1
    updated = _enable(b)
    resolved = dispatch.resolve_unknown(b['crm'], row['id'], dispatch.OutboxResolve(
        request_key=b['key'](), action='authorize_resend', reason='Fictional D2 legitimate resend'))
    assert resolved['record']['reply_policy_version'] == updated['record']['version']


def test_rp13_sent_then_disabled_changes_nothing(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    submit = StubSubmit(['wamid.RP13'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp13', submit=submit)['status'] == 'sent'
    _disable(b)
    with b['conn'].cursor(row_factory=dict_row) as cur:
        assert cur.execute('SELECT status FROM soloestate.delivery_outbox WHERE id=%s',
                           (row['id'],)).fetchone()['status'] == 'sent'
    assert len(submit.calls) == 1
    box2 = _inbound(b, _sender())
    out2 = dispatch.stage(b['crm'], **_stage_kwargs(b, box2, box2['message']))
    assert out2['record']['status'] == 'skipped' and out2['record']['skip_reason'] == 'policy_disabled'


def test_rp14_legacy_unbound_fails_closed_then_restage_rebinds(rp):
    from app.policies import update_reply_policy
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    with b['conn'].cursor() as cur:
        cur.execute('UPDATE soloestate.delivery_outbox SET reply_policy_id=NULL, reply_policy_version=NULL '
                    'WHERE id=%s', (row['id'],))
    submit = StubSubmit(['wamid.NEVER'])
    driven = dispatch.drive_row(b['factory'], row['id'], worker='rp14', submit=submit)
    assert driven['status'] == 'blocked' and driven['reasons'] == ['reply_policy_unbound']
    assert submit.calls == [] and _used(b, row['reserved_day']) == 0
    update_reply_policy(b['crm'], s.ReplyPolicyUpdate(
        request_key=b['key'](), expected_version=_policy(b)['version'],
        categories=['welcome', 'faq_answer'], reason='Fictional D2 evolve'))
    current = _policy(b)
    submit2 = StubSubmit(['wamid.RP14'])
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp14b', submit=submit2)['status'] == 'sent'
    assert len(submit2.calls) == 1
    with b['conn'].cursor(row_factory=dict_row) as cur:
        rebound = cur.execute('SELECT reply_policy_id, reply_policy_version FROM soloestate.delivery_outbox '
                              'WHERE id=%s', (row['id'],)).fetchone()
        assert rebound['reply_policy_id'] == 'whatsapp' and rebound['reply_policy_version'] == current['version']


def test_rp15_resend_refused_when_disabled(rp):
    b = rp
    _enable(b)
    box = _inbound(b, _sender())
    out = dispatch.stage(b['crm'], **_stage_kwargs(b, box, box['message']))
    row = out['record']
    assert dispatch.drive_row(b['factory'], row['id'], worker='rp15',
                              submit=StubSubmit([dispatch.SubmitUnknown('timeout')])) == {'status': 'unknown'}
    assert _used(b, row['reserved_day']) == 1
    _disable(b)
    with pytest.raises(CRMError, match='disabled'):
        dispatch.resolve_unknown(b['crm'], row['id'], dispatch.OutboxResolve(
            request_key=b['key'](), action='authorize_resend', reason='Fictional D2 bypass attempt'))
    with b['conn'].cursor(row_factory=dict_row) as cur:
        fresh = cur.execute('SELECT status, reserved FROM soloestate.delivery_outbox WHERE id=%s',
                            (row['id'],)).fetchone()
        assert fresh['status'] == 'unknown' and fresh['reserved'] is True
    assert _used(b, row['reserved_day']) == 1


def test_rp16_owner_routes_require_owner_and_roundtrip(rp):
    b = rp
    with _api(b, authed=False) as api:
        assert api.get('/owner/crm/immediate-reply-policy').status_code == 401
        assert api.patch('/owner/crm/immediate-reply-policy', json={'request_key': 'x'}).status_code == 401
    with _api(b) as api:
        fetched = api.get('/owner/crm/immediate-reply-policy')
        live_v = fetched.json()['record']['version']
        assert fetched.status_code == 200 and fetched.json()['record']['enabled'] is False
        saved = api.patch('/owner/crm/immediate-reply-policy',
                          json={'request_key': b['key'](), 'expected_version': live_v, 'enabled': True,
                                'categories': ['welcome'], 'reason': 'Fictional D2 route enable'})
        assert saved.status_code == 200 and saved.json()['record']['version'] == live_v + 1
        stale = api.patch('/owner/crm/immediate-reply-policy',
                          json={'request_key': b['key'](), 'expected_version': live_v, 'enabled': False,
                                'reason': 'Fictional D2 route stale'})
        assert stale.status_code == 409
