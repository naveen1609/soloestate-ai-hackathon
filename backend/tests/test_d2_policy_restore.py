"""Immediate-reply-policy fixture restoration safety.

Committed fictional fixtures with exact cleanup: proves the shared policy row
is captured under lock, restored only through an atomic compare-and-set
against the fixture-owned state with monotonic versions, and that an
intervening owner write on an independent connection survives with an
explicit conflict while owned rows are still removed.
"""
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'frontend' / 'e2e'))
import outbox_fixture

from app import crm_schemas as s
from app.config import Settings
from app.crm import CRM, scalar
from app.database import connect

pytestmark = pytest.mark.d2_database


def _gated(request):
    if not request.config.getoption('--run-integrity-db-tests'):
        pytest.skip('Use --run-integrity-db-tests')


def _read_policy():
    with connect(Settings()) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            row = cur.execute('SELECT enabled, categories, version FROM soloestate.immediate_reply_policies '
                              "WHERE id='whatsapp'").fetchone()
    return {'enabled': row['enabled'], 'categories': list(row['categories']), 'version': row['version']}


def _scrub_op(key):
    with connect(Settings()) as conn:
        conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=%s', (key,))


def _intrude(tag):
    """Committed owner-style update on an independent connection."""
    from app.policies import update_reply_policy
    live = _read_policy()
    key = 'pr-intrude-' + tag
    record = update_reply_policy(CRM(), s.ReplyPolicyUpdate(
        request_key=key, expected_version=live['version'], enabled=False,
        categories=['faq_answer'], reason='Fictional PR intrude ' + tag))['record']
    return key, {'enabled': record['enabled'], 'categories': list(record['categories']),
                 'version': record['version']}


def _ensure_seed_values(tag):
    """Return the row to seed values through the versioned production update."""
    from app.policies import update_reply_policy
    live = _read_policy()
    if live['enabled'] is False and live['categories'] == []:
        return
    key = 'pr-restore-' + tag
    update_reply_policy(CRM(), s.ReplyPolicyUpdate(
        request_key=key, expected_version=live['version'], enabled=False,
        categories=[], reason='Fictional PR seed restore ' + tag))
    _scrub_op(key)


def _caps_used_total():
    with connect(Settings()) as conn:
        return scalar(conn.execute("SELECT coalesce(sum(used),0) FROM soloestate.delivery_caps "
                                   "WHERE channel='whatsapp'").fetchone())


def test_pr01_normal_restore_succeeds_with_monotonic_version(request):
    _gated(request)
    tag = uuid4().hex[:8]
    state = outbox_fixture._policy_enable_committed()
    try:
        assert state['owned']['enabled'] is True and state['owned']['version'] > 0
        assert _read_policy() == state['owned']
        assert outbox_fixture._policy_restore_committed(state) is None
        live = _read_policy()
        assert live['enabled'] == state['before']['enabled']
        assert live['categories'] == state['before']['categories']
        assert live['version'] == state['owned']['version'] + 1
    finally:
        _ensure_seed_values(tag)


def test_pr02_owner_update_between_setup_and_cleanup_survives(request):
    _gated(request)
    tag = uuid4().hex[:8]
    key = None
    state = outbox_fixture._policy_enable_committed()
    try:
        key, intruded = _intrude(tag)
        conflict = outbox_fixture._policy_restore_committed(state)
        assert conflict is not None and 'refused' in conflict
        assert 'preserved current state' in conflict
        assert _read_policy() == intruded
    finally:
        if key is not None:
            _scrub_op(key)
        _ensure_seed_values(tag)


def test_pr03_owner_update_between_read_and_write_survives(request):
    _gated(request)
    tag = uuid4().hex[:8]
    key = None
    intruded_flags = []
    state = outbox_fixture._policy_enable_committed()
    data = dict(outbox_ids=[], convo_ids=[], lead_ids=[], link_ids=[], op_keys=[],
                policy_state=state)
    try:
        def intrude():
            nonlocal key
            intrude_key, record = _intrude(tag)
            key = intrude_key
            intruded_flags.append(record)
        result = outbox_fixture.cleanup(data, _before_restore=intrude)
        assert len(intruded_flags) == 1
        assert result['cleaned'] is True and len(result['conflicts']) == 1
        assert 'refused' in result['conflicts'][0]
        assert _read_policy() == intruded_flags[0]
    finally:
        if key is not None:
            _scrub_op(key)
        _ensure_seed_values(tag)


def test_pr04_owned_rows_removed_despite_restore_conflict(request):
    _gated(request)
    tag = uuid4().hex[:8]
    key = None
    intruded = None
    used_before = _caps_used_total()
    data = json.loads(json.dumps(outbox_fixture.plan()))
    try:
        key, intruded = _intrude(tag)
        result = outbox_fixture.cleanup(data)
        assert result['cleaned'] is True and len(result['conflicts']) == 1
        assert 'refused' in result['conflicts'][0]
        assert _read_policy() == intruded
        with connect(Settings()) as conn:
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.delivery_outbox WHERE id=ANY(%s::uuid[])',
                                       (data['outbox_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.delivery_attempts '
                                       'WHERE outbox_id=ANY(%s::uuid[])', (data['outbox_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.conversation_messages '
                                       'WHERE conversation_id=ANY(%s::uuid[])', (data['convo_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.conversation_summaries '
                                       'WHERE conversation_id=ANY(%s::uuid[])', (data['convo_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.conversations WHERE id=ANY(%s::uuid[])',
                                       (data['convo_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.lead_activities '
                                       'WHERE lead_id=ANY(%s::uuid[])', (data['lead_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.identity_links WHERE id=ANY(%s::uuid[])',
                                       (data['link_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=ANY(%s::uuid[])',
                                       (data['lead_ids'],)).fetchone()) == 0
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.crm_operations '
                                       'WHERE request_key=ANY(%s)', (data['op_keys'],)).fetchone()) == 0
        assert _caps_used_total() == used_before
    finally:
        if key is not None:
            _scrub_op(key)
        _ensure_seed_values(tag)
        outbox_fixture.cleanup(data)


def test_pr05_unrelated_records_and_audit_intact_through_conflict(request):
    _gated(request)
    tag = uuid4().hex[:8]
    key = None
    sentinel_lead = None
    sentinel_op = 'pr-sentinel-op-' + tag
    state = outbox_fixture._policy_enable_committed()
    data = dict(outbox_ids=[], convo_ids=[], lead_ids=[], link_ids=[], op_keys=[],
                policy_state=state)
    try:
        sentinel_lead = CRM().save('leads', s.LeadCreate(
            request_key='pr-sentinel-lead-' + tag, name='Fictional PR sentinel ' + tag,
            email='pr-sentinel-' + tag + '@example.invalid'))['record']
        with connect(Settings()) as conn:
            conn.execute('INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,'
                         'response) VALUES (%s,%s,%s,%s,%s,%s::jsonb)',
                         (sentinel_op, 'pr:sentinel', 'pr:' + tag, 'owner', sentinel_lead['id'],
                          '{"note":"sentinel"}'))
        key, intruded = _intrude(tag)
        result = outbox_fixture.cleanup(data)
        assert len(result['conflicts']) == 1
        assert _read_policy() == intruded
        with connect(Settings()) as conn:
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.leads WHERE id=%s',
                                       (sentinel_lead['id'],)).fetchone()) == 1
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                       (sentinel_op,)).fetchone()) == 1
            assert scalar(conn.execute('SELECT count(*) FROM soloestate.crm_operations WHERE request_key=%s',
                                       (key,)).fetchone()) == 1
    finally:
        with connect(Settings()) as conn:
            conn.execute('DELETE FROM soloestate.crm_operations WHERE request_key=ANY(%s)',
                         ([sentinel_op, 'pr-sentinel-lead-' + tag],))
            if sentinel_lead is not None:
                conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=%s', (sentinel_lead['id'],))
                conn.execute('DELETE FROM soloestate.leads WHERE id=%s', (sentinel_lead['id'],))
        if key is not None:
            _scrub_op(key)
        _ensure_seed_values(tag)
