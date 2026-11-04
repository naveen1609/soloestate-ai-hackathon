"""Public WhatsApp Cloud API webhook endpoints (signature-authenticated, no owner token)."""
import hmac
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response

from app.config import Settings
from app.crm import CRM, CRMError
from app.crm_api import service

router = APIRouter(prefix='/whatsapp')


def whatsapp_settings() -> Settings:
    return Settings()


def _fail_closed():
    raise CRMError('WhatsApp channel is not configured.', 403)


@router.get('/webhook')
def verify_webhook(request: Request, config: Settings = Depends(whatsapp_settings)):
    params = request.query_params
    token = config.whatsapp_verify_token.get_secret_value() if config.whatsapp_verify_token else None
    if not token:
        _fail_closed()
    if params.get('hub.mode') != 'subscribe' or not hmac.compare_digest(
            params.get('hub.verify_token', ''), token):
        raise CRMError('Webhook verification failed.', 403)
    return Response(content=params.get('hub.challenge', ''), media_type='text/plain')


@router.post('/webhook')
async def receive_webhook(request: Request, crm: CRM = Depends(service),
                           config: Settings = Depends(whatsapp_settings)):
    import asyncio
    from app import whatsapp as wa
    raw = await request.body()
    if len(raw) > wa.MAX_BODY_BYTES or not raw:
        raise CRMError('Webhook payload too large or empty.', 413)
    secret = config.whatsapp_app_secret.get_secret_value() if config.whatsapp_app_secret else None
    if not wa.verify_signature(raw, request.headers.get('x-hub-signature-256'), secret):
        raise CRMError('Webhook signature invalid.', 403)
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise CRMError('Webhook payload is not valid JSON.', 400)
    _check_identity(payload, config)
    events = wa.extract_events(payload)
    now = datetime.now(timezone.utc)
    from psycopg.rows import dict_row
    with crm.connect() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            outcomes = wa.ingest_batch(cur, events, _raw_events(payload, events), now)
    # Sync durable processing runs in a worker thread: the turn may drive
    # inference via asyncio.run, which cannot run on this event loop.
    from app.services.whatsapp import build_sender
    sender = build_sender(config)
    await asyncio.to_thread(wa.process_now, crm.connect, model=None,
                            submit=(sender.send if sender is not None else None))
    stored = sum(1 for o in outcomes if o['outcome'] == 'stored')
    return {'status': 'received', 'events': len(events), 'stored': stored,
            'outcomes': [o['outcome'] for o in outcomes]}


def _check_identity(payload, config: Settings):
    expected_account = config.whatsapp_business_account_id
    expected_phone = config.whatsapp_phone_number_id
    if not expected_account or not expected_phone:
        _fail_closed()
    try:
        entries = payload['entry']
    except (KeyError, TypeError):
        raise CRMError('Webhook payload has no entries.', 400)
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get('id') != expected_account:
            raise CRMError('Webhook account identity mismatch.', 403)
        changes = entry.get('changes') or []
        for change in changes if isinstance(changes, list) else []:
            value = (change or {}).get('value') or {}
            metadata = value.get('metadata') or {}
            if metadata.get('phone_number_id') != expected_phone:
                raise CRMError('Webhook phone identity mismatch.', 403)


def _raw_events(payload, events):
    raws = []
    for entry in payload.get('entry', []):
        for change in (entry.get('changes') or []):
            value = change.get('value') or {}
            for message in value.get('messages') or []:
                raws.append({'messages': [message]})
            for status in value.get('statuses') or []:
                raws.append({'statuses': [status]})
    while len(raws) < len(events):
        raws.append({})
    return raws[:len(events)]
