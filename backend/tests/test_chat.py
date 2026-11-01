"""Database-free Phase C visitor-token checks: strict validation and fail-closed config."""
import base64
import json
import secrets
import time
from uuid import uuid4

import pytest

from app.config import Settings
from app.crm import CRMError
from app.visitor_tokens import MAX_TOKEN_LENGTH, TTL_SECONDS, mint, verify


@pytest.fixture
def secret():
    return secrets.token_urlsafe(32)


@pytest.fixture
def settings(secret):
    return Settings(visitor_token_secret=secret)


def test_mint_verify_roundtrip_returns_conversation_id(settings):
    cid = uuid4()
    assert verify(mint(cid, settings), settings) == cid


def test_tokens_are_short_and_versioned(settings):
    token = mint(uuid4(), settings)
    assert token.startswith('v1.') and len(token) < MAX_TOKEN_LENGTH


def test_tampered_payload_or_mac_rejected(settings):
    token = mint(uuid4(), settings)
    version, body, mac = token.split('.')
    flip = 'A' if body[0] != 'A' else 'B'
    with pytest.raises(CRMError) as exc:
        verify(f'{version}.{flip}{body[1:]}.{mac}', settings)
    assert exc.value.status == 401
    with pytest.raises(CRMError) as exc:
        verify(f'{version}.{body}.{flip}{mac[1:]}', settings)
    assert exc.value.status == 401


def test_wrong_secret_rejected(settings):
    token = mint(uuid4(), settings)
    other = Settings(visitor_token_secret=secrets.token_urlsafe(32))
    with pytest.raises(CRMError) as exc:
        verify(token, other)
    assert exc.value.status == 401


def forged(settings, payload):
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
    return f'v1.{body}.{"A" * 43}='


@pytest.mark.parametrize('token', ['', 'v1', 'v1.abc', 'v1.a.b.c', 'v2.a.b', 'x' * (MAX_TOKEN_LENGTH + 1), 'v1.***.AAA'])
def test_malformed_shape_rejected(settings, token):
    with pytest.raises(CRMError) as exc:
        verify(token, settings)
    assert exc.value.status == 401


def test_payload_shape_strictly_validated(settings):
    cid = str(uuid4())
    bad = [{'cid': cid, 'iat': 1}, {'cid': cid, 'iat': 1, 'exp': 2, 'lead': 'x'},
           {'cid': 'not-a-uuid', 'iat': 1, 'exp': 2}, {'cid': cid.upper(), 'iat': 1, 'exp': 2},
           {'cid': cid, 'iat': True, 'exp': 2}, {'cid': cid, 'iat': 1, 'exp': '2'},
           {'cid': cid, 'iat': 1, 'exp': 1 + TTL_SECONDS + 1}, {'cid': cid, 'iat': 5, 'exp': 5},
           ['cid'], 'cid', 42, None]
    for payload in bad:
        with pytest.raises(CRMError) as exc:
            verify(forged(settings, payload), settings)
        assert exc.value.status == 401


def test_expiry_and_lifetime_bounds(settings):
    cid = uuid4()
    now = int(time.time())
    assert verify(mint(cid, settings, now=now - TTL_SECONDS + 1), settings, now=now) == cid
    with pytest.raises(CRMError) as exc:
        verify(mint(cid, settings, now=now - TTL_SECONDS), settings, now=now)
    assert exc.value.status == 401
    with pytest.raises(CRMError) as exc:
        verify(mint(cid, settings, now=now + 3600), settings, now=now)
    assert exc.value.status == 401


@pytest.mark.parametrize('value', [None, '', 'short'])
def test_misconfiguration_fails_closed(value):
    settings = Settings(visitor_token_secret=value)
    cid = uuid4()
    with pytest.raises(CRMError) as exc:
        mint(cid, settings)
    assert exc.value.status == 503
    with pytest.raises(CRMError) as exc:
        verify('v1.invalid.invalid', settings)
    assert exc.value.status == 503


def test_errors_never_reveal_secret(settings, secret):
    with pytest.raises(CRMError) as exc:
        verify('v1.invalid.invalid', settings)
    assert secret not in str(exc.value)
