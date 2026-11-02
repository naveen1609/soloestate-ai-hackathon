from datetime import date, datetime, time, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app import crm_schemas as s
from app.caps import cooldown_ok
from app.crm import CRMError
from app.identity_links import match_optout_keyword
from app.policies import (DAY_CAP_DEFAULT, MESSAGE_CATEGORIES, OWNER_RESOURCE_ID, ROUTINE_CATEGORIES,
                          SENSITIVE_CATEGORIES, evaluate_window, validate_categories)
from app.booking_requests import governing, normalize_window, request_hash_for
from app.snapshots import (build_booking_v1, build_booking_v2, build_followup_v1, build_followup_v2,
                           read_booking_snapshot, read_followup_snapshot, select_identity)

IST = timezone(timedelta(hours=5, minutes=30))


def policy(**overrides):
    base = dict(timezone='Asia/Kolkata', working_days=[0, 1, 3, 4, 5, 6], weekly_holiday=2,
                hours_start=time(10, 0), hours_end=time(17, 0), holiday_dates=[], notice_hours=16,
                min_duration_min=15, default_duration_min=30, max_duration_min=120,
                buffer_minutes=0, version=1)
    base.update(overrides)
    return base


def ist(day, hour, minute=0):
    return datetime(2026, 11, day, hour, minute, tzinfo=IST)


def test_evaluate_window_passes_clean_request():
    verdict = evaluate_window(policy(), ist(4, 11), ist(4, 11, 30), ist(1, 9))
    assert verdict == {'passed': True, 'reasons': [], 'policy_version': 1}


def test_evaluate_window_tuesday_and_holiday():
    assert evaluate_window(policy(), ist(3, 11), ist(3, 11, 30), ist(1, 9))['reasons'] == ['weekly_holiday', 'outside_working_day']
    dated = policy(holiday_dates=[date(2026, 11, 4)])
    assert 'holiday_date' in evaluate_window(dated, ist(4, 11), ist(4, 11, 30), ist(1, 9))['reasons']


def test_evaluate_window_sunday_open_monday_open():
    assert evaluate_window(policy(), ist(1, 11), ist(1, 11, 30), datetime(2026, 10, 30, 9, tzinfo=IST))['passed']
    assert evaluate_window(policy(), ist(2, 11), ist(2, 11, 30), ist(1, 9))['passed']


def test_evaluate_window_hours_edges():
    assert evaluate_window(policy(), ist(4, 16, 30), ist(4, 17, 0), ist(1, 9))['passed']
    assert evaluate_window(policy(), ist(4, 16, 45), ist(4, 17, 15), ist(1, 9))['reasons'] == ['outside_hours']
    assert evaluate_window(policy(), ist(4, 9, 45), ist(4, 10, 15), ist(1, 9))['reasons'] == ['outside_hours']


def test_evaluate_window_notice_boundary_utc():
    start = datetime(2026, 11, 5, 5, 30, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    assert evaluate_window(policy(), start, end, start - timedelta(hours=16))['passed']
    assert evaluate_window(policy(), start, end, start - timedelta(hours=16) + timedelta(seconds=1))['reasons'] == ['short_notice']


def test_evaluate_window_duration_range():
    assert evaluate_window(policy(), ist(4, 11), ist(4, 11, 10), ist(1, 9))['reasons'] == ['bad_duration']
    assert evaluate_window(policy(), ist(4, 11), ist(4, 13, 30), ist(1, 9), 150)['reasons'] == ['bad_duration']
    assert evaluate_window(policy(), ist(4, 11), ist(4, 12), ist(1, 9), 60)['passed']


def test_normalize_window_floors_to_grid():
    start = datetime(2026, 11, 4, 5, 37, 42, tzinfo=timezone.utc)
    floored, end = normalize_window(start, 30)
    assert (floored.minute, floored.second, floored.microsecond) == (35, 0, 0)
    assert end - floored == timedelta(minutes=30)
    exact = datetime(2026, 11, 4, 5, 30, tzinfo=timezone.utc)
    assert normalize_window(exact, 30) == (exact, exact + timedelta(minutes=30))


def test_governing_prefers_owner_then_confirmed():
    row = {'owner_approved_start': 1, 'owner_approved_end': 2, 'owner_approved_resource_id': 3,
           'customer_confirmed_start': 4, 'customer_confirmed_end': 5, 'customer_confirmed_resource_id': 6}
    assert governing(row) == (1, 2, 3)
    row.update(owner_approved_start=None, owner_approved_end=None, owner_approved_resource_id=None)
    assert governing(row) == (4, 5, 6)
    row.update(customer_confirmed_start=None, customer_confirmed_end=None, customer_confirmed_resource_id=None)
    assert governing(row) is None


def test_request_hash_stable_and_sensitive():
    kwargs = dict(lead_id=uuid4(), property_id=uuid4(), start=ist(4, 11), end=ist(4, 11, 30),
                  resource_id=OWNER_RESOURCE_ID, duration_min=30, policy_version=1, reasons=['short_notice'])
    first, second = request_hash_for(**kwargs), request_hash_for(**kwargs)
    assert first == second and len(first) == 64
    assert request_hash_for(**{**kwargs, 'reasons': []}) != first
    assert request_hash_for(**{**kwargs, 'policy_version': 2}) != first


def test_message_category_sets():
    assert len(ROUTINE_CATEGORIES) == 10 and len(SENSITIVE_CATEGORIES) == 6
    assert ROUTINE_CATEGORIES.isdisjoint(SENSITIVE_CATEGORIES)
    assert MESSAGE_CATEGORIES == ROUTINE_CATEGORIES | SENSITIVE_CATEGORIES
    validate_categories(['welcome', 'negotiation'])
    with pytest.raises(CRMError):
        validate_categories(['nope'])
    assert DAY_CAP_DEFAULT == 25


def test_optout_keywords_exact():
    assert match_optout_keyword('STOP') and match_optout_keyword('  unsubscribe ')
    assert not match_optout_keyword('stop please') and not match_optout_keyword('') and not match_optout_keyword(None)


def test_cooldown_counts_open_and_acked():
    assert cooldown_ok(0, 0) and not cooldown_ok(1, 0) and not cooldown_ok(0, 1) and not cooldown_ok(2, 3)


def _slot_args(**overrides):
    base = dict(request_key='k', starts_at=datetime(2026, 11, 5, 5, 30, tzinfo=timezone.utc),
                ends_at=datetime(2026, 11, 5, 6, 0, tzinfo=timezone.utc))
    base.update(overrides)
    return s.SlotCreate(**base)


def test_select_identity_v1_when_d1_absent():
    version, digest, legacy = select_identity('slot:create', _slot_args())
    assert version == 1 and legacy is None
    again = select_identity('slot:create', _slot_args())[1]
    assert digest == again


def test_select_identity_v1_for_explicit_null_and_defaults():
    request = _slot_args(resource_id=None)
    assert 'resource_id' in request.model_fields_set
    version, digest, _ = select_identity('slot:create', request)
    assert version == 1
    assert digest == select_identity('slot:create', _slot_args())[1]


def test_select_identity_v2_exclusive_for_explicit_resource():
    request = _slot_args(resource_id=uuid4())
    version, digest, legacy = select_identity('slot:create', request)
    assert version == 2 and legacy is None
    assert digest != select_identity('slot:create', _slot_args())[1]


def test_select_identity_v2_only_namespace_and_prefix_keys():
    from app.journey import JourneyUpdate
    update = JourneyUpdate(request_key='k', expected_version=1, reason='r', whatsapp_permission='opted_in')
    version, _, _ = select_identity('lead_journey:' + str(uuid4()), update)
    assert version == 2
    plain = JourneyUpdate(request_key='k', expected_version=1, reason='r', journey_stage='qualifying')
    assert select_identity('lead_journey:' + str(uuid4()), plain)[0] == 1


def test_select_identity_legacy_lead_update_rules_preserved():
    lead = s.LeadUpdate(request_key='k', expected_version=1, name='N', email='e@example.invalid',
                        preferred_localities=['X'], budget_min=1, budget_max=2, bedrooms=1, status='new')
    version, _, legacy = select_identity('leads:' + str(uuid4()), lead)
    assert version == 1 and legacy is not None
    changed = lead.model_copy(update={'name': 'Other'})
    assert select_identity('leads:' + str(uuid4()), changed)[1] != select_identity('leads:' + str(uuid4()), lead)[1]


def _booking_parts():
    return dict(lead_id=uuid4(), recipient='a@example.invalid', property_id=uuid4(), property_title='T',
                price='100.00', slot_id=uuid4(), starts_at='2026-11-05T05:30:00+00:00',
                ends_at='2026-11-05T06:00:00+00:00')


def test_booking_snapshot_v1_v2_shapes():
    v1 = build_booking_v1(**_booking_parts())
    assert read_booking_snapshot(v1)[0] == 1 and 'snapshot_version' not in v1
    v2 = build_booking_v2(resource_id=OWNER_RESOURCE_ID, buffer_min=0, **_booking_parts())
    assert read_booking_snapshot(v2)[0] == 2
    assert v2['resource_id'] == str(OWNER_RESOURCE_ID) and v2['buffer_min'] == 0
    assert read_booking_snapshot(v1) == (1, v1)
    assert read_booking_snapshot(v2) == (2, v2)
    assert read_booking_snapshot(v1)[1] != read_booking_snapshot(v2)[1]
    with pytest.raises(CRMError):
        read_booking_snapshot({**v1, 'channel': 'email'})
    with pytest.raises(CRMError):
        read_booking_snapshot({**v2, 'resource_id': 'x', 'extra': 1})


def test_followup_snapshot_v1_v2_shapes():
    v1 = build_followup_v1(draft_id=uuid4(), lead_id=uuid4(), version=1, channel='email',
                           recipient='a@example.invalid', subject='s', body='b')
    assert read_followup_snapshot(v1)[0] == 1
    v2 = build_followup_v2(draft_id=uuid4(), lead_id=uuid4(), version=1, channel='email',
                           verified_destination='a@example.invalid', identity_link_id=None,
                           destination_basis='link', subject='s', body='b', category='reminder',
                           policy_version=1, consent={'permission': 'opted_in', 'source': 'owner', 'time': 't'})
    assert read_followup_snapshot(v2)[0] == 2
    assert read_followup_snapshot(v1) == (1, v1)
    assert read_followup_snapshot(v2) == (2, v2)
    assert read_followup_snapshot(v1)[1] != read_followup_snapshot(v2)[1]
    with pytest.raises(CRMError):
        read_followup_snapshot({**v2, 'resource_id': str(OWNER_RESOURCE_ID)})
    with pytest.raises(CRMError):
        read_followup_snapshot({'snapshot_version': 2})


def test_booking_decide_schema_window_rules():
    with pytest.raises(ValidationError):
        s.BookingDecide(request_key='k', expected_version=1, decision='approved',
                        approved_start=datetime.now(timezone.utc), reason='r')


def _prepare_args(**overrides):
    base = dict(request_key='k', lead_id=uuid4(), property_id=uuid4(), slot_id=uuid4())
    base.update(overrides)
    return s.AppointmentRequest(**base)


def test_snp_explicit_resource_selects_v2_exclusively():
    version, digest, legacy = select_identity('appointment:prepare', _prepare_args(resource_id=uuid4()))
    assert version == 2 and legacy is None
    assert len(digest) == 64


def test_snp_omitted_or_null_resource_stays_v1():
    assert select_identity('appointment:prepare', _prepare_args())[0] == 1
    assert select_identity('appointment:prepare', _prepare_args(resource_id=None))[0] == 1
    slot_bare = s.SlotCreate(request_key='k', starts_at=ist(4, 11), ends_at=ist(4, 11, 30))
    assert select_identity('slot:create', slot_bare)[0] == 1
    slot_d1 = s.SlotCreate(request_key='k', starts_at=ist(4, 11), ends_at=ist(4, 11, 30), resource_id=uuid4())
    assert select_identity('slot:create', slot_d1)[0] == 2


def test_snp_booking_request_actions_are_v2_only():
    request = s.BookingRequestCreate(request_key='k', lead_id=uuid4(), property_id=uuid4(),
                                     requested_start=ist(4, 11), requested_end=ist(4, 11, 30))
    assert select_identity('booking-request:create', request)[0] == 2


def test_snp_journey_d1_keys_select_v2():
    from app.journey import JourneyUpdate
    bare = JourneyUpdate(request_key='k', expected_version=1, journey_stage='qualified', reason='r')
    assert select_identity('lead_journey:update', bare)[0] == 1
    d1 = JourneyUpdate(request_key='k', expected_version=1, phone='+15550001111', reason='r')
    assert select_identity('lead_journey:update', d1)[0] == 2


def test_snp_explicit_null_matches_omitted_fingerprint():
    ids = dict(lead_id=uuid4(), property_id=uuid4(), slot_id=uuid4())
    omitted = s.AppointmentRequest(request_key='k', **ids)
    nulled = s.AppointmentRequest(request_key='k', resource_id=None, **ids)
    explicit = s.AppointmentRequest(request_key='k', resource_id=uuid4(), **ids)
    assert select_identity('appointment:prepare', omitted)[1] == select_identity('appointment:prepare', nulled)[1]
    assert select_identity('appointment:prepare', explicit)[1] != select_identity('appointment:prepare', omitted)[1]


def test_pol04_cooldown_requires_quiet_window():
    assert cooldown_ok(0, 0) is True
    assert cooldown_ok(1, 0) is False
    assert cooldown_ok(0, 1) is False
    assert cooldown_ok(2, 3) is False
