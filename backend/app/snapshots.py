"""Versioned approval snapshots (v1 legacy / v2 D1) and request fingerprint selection.

Snapshot readers/validators/serializers are pure over payload dicts. select_identity
implements the presence-first v1/v2 algorithm over the actual mutate() inputs.
"""
BOOKING_V1_KEYS = frozenset({
    'lead_id', 'recipient', 'property_id', 'property_title', 'price',
    'slot_id', 'starts_at', 'ends_at'})
BOOKING_V2_KEYS = BOOKING_V1_KEYS | frozenset({'snapshot_version', 'resource_id', 'buffer_min'})
FOLLOWUP_V1_KEYS = frozenset({
    'draft_id', 'lead_id', 'version', 'channel', 'recipient', 'subject', 'body'})
FOLLOWUP_V2_KEYS = frozenset({
    'snapshot_version', 'draft_id', 'lead_id', 'version', 'channel',
    'verified_destination', 'identity_link_id', 'destination_basis', 'subject',
    'body', 'category', 'policy_version', 'consent'})

D1_KEYS = {'slot:create': frozenset({'resource_id'}),
           'appointment:prepare': frozenset({'resource_id'})}
D1_PREFIXES = {'lead_journey:': frozenset({'phone', 'whatsapp_permission'})}
V2_ONLY_PREFIXES = ('policy:', 'slot:generate', 'resource:', 'booking-request:',
                    'identity-link:', 'suppression:', 'automation-controls:',
                    'automation-policy:')


def _version(payload, v1_keys, v2_keys, kind):
    from app.crm import CRMError
    version = payload.get('snapshot_version', 1)
    if version == 1 and set(payload) == v1_keys:
        return 1, dict(payload)
    if version == 2 and set(payload) == v2_keys:
        return 2, dict(payload)
    raise CRMError(f'Unsupported {kind} approval snapshot.', 422)


def read_booking_snapshot(payload):
    return _version(payload, BOOKING_V1_KEYS, BOOKING_V2_KEYS, 'booking')


def read_followup_snapshot(payload):
    return _version(payload, FOLLOWUP_V1_KEYS, FOLLOWUP_V2_KEYS, 'followup')


def build_booking_v1(*, lead_id, recipient, property_id, property_title, price, slot_id, starts_at, ends_at):
    from app.crm import encode
    return encode({'lead_id': lead_id, 'recipient': recipient, 'property_id': property_id,
                   'property_title': property_title, 'price': price, 'slot_id': slot_id,
                   'starts_at': starts_at, 'ends_at': ends_at})


def build_booking_v2(*, lead_id, recipient, property_id, property_title, price, slot_id,
                     starts_at, ends_at, resource_id, buffer_min):
    from app.crm import encode
    payload = build_booking_v1(lead_id=lead_id, recipient=recipient, property_id=property_id,
                               property_title=property_title, price=price, slot_id=slot_id,
                               starts_at=starts_at, ends_at=ends_at)
    payload.update({'snapshot_version': 2, 'resource_id': encode(resource_id), 'buffer_min': buffer_min})
    return payload


def build_followup_v1(*, draft_id, lead_id, version, channel, recipient, subject, body):
    from app.crm import encode
    return encode({'draft_id': draft_id, 'lead_id': lead_id, 'version': version, 'channel': channel,
                   'recipient': recipient, 'subject': subject, 'body': body})


def build_followup_v2(*, draft_id, lead_id, version, channel, verified_destination, identity_link_id,
                      destination_basis, subject, body, category, policy_version, consent):
    from app.crm import encode
    return encode({'snapshot_version': 2, 'draft_id': draft_id, 'lead_id': lead_id, 'version': version,
                   'channel': channel, 'verified_destination': verified_destination,
                   'identity_link_id': identity_link_id, 'destination_basis': destination_basis,
                   'subject': subject, 'body': body, 'category': category,
                   'policy_version': policy_version, 'consent': consent})


def _d1_keys(action):
    if action in D1_KEYS:
        return D1_KEYS[action]
    for prefix, keys in D1_PREFIXES.items():
        if action.startswith(prefix):
            return keys
    return frozenset()


def select_identity(action, request):
    """Presence-first fingerprint selection. Returns (version, digest, legacy_or_None).

    Explicit non-null D1 keys select v2 exclusively (never a stripped-v1 replay);
    anything else takes the v1 projection with all legacy rules preserved.
    """
    from app.crm import payload_hash
    from app.crm_schemas import LeadCreate, LeadUpdate, OutcomeUpdate, TaskUpdate
    fields = request.model_fields_set
    if action.startswith(V2_ONLY_PREFIXES):
        identity = {'action': action, 'arguments': request.model_dump(mode='json'),
                    'provided_fields': sorted(fields), 'schema_version': 2}
        return 2, payload_hash(identity), None
    d1 = _d1_keys(action)
    if any(key in fields and getattr(request, key) is not None for key in d1):
        identity = {'action': action, 'arguments': request.model_dump(mode='json'),
                    'provided_fields': sorted(fields), 'schema_version': 2}
        return 2, payload_hash(identity), None
    data = {key: value for key, value in request.model_dump(mode='json').items() if key not in d1}
    provided = sorted(set(fields) - d1)
    legacy_digest = None
    if isinstance(request, LeadCreate) and 'purchase_timeframe' not in fields:
        data.pop('purchase_timeframe', None)
    identity = {'action': action, 'arguments': data}
    if isinstance(request, LeadUpdate):
        if 'purchase_timeframe' not in fields:
            legacy_digest = payload_hash(identity)
        identity['provided_fields'] = provided
    if isinstance(request, (TaskUpdate, OutcomeUpdate)):
        identity['provided_fields'] = provided
    return 1, payload_hash(identity), legacy_digest
