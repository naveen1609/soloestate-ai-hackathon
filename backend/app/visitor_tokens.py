"""Stateless HMAC visitor tokens: exactly one conversation, 24h TTL, fail-closed config."""
import base64
import hashlib
import hmac
import json
import time
from uuid import UUID

from app.crm import CRMError

VERSION = 'v1'
TTL_SECONDS = 86400
MAX_TOKEN_LENGTH = 512
MIN_SECRET_LENGTH = 32
FUTURE_SKEW_SECONDS = 60

_B64URL = frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_=')
_INVALID = 'Chat token is invalid or expired.'


def _secret(settings=None):
    from app.config import Settings
    value = (settings or Settings()).visitor_token_secret
    secret = value.get_secret_value() if value is not None else ''
    if len(secret) < MIN_SECRET_LENGTH:
        raise CRMError('Chat is not configured.', 503)
    return secret


def mint(conversation_id, settings=None, now=None):
    secret = _secret(settings)
    issued = int(now if now is not None else time.time())
    payload = {'cid': str(UUID(str(conversation_id))), 'iat': issued, 'exp': issued + TTL_SECONDS}
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(',', ':')).encode()).decode()
    mac = base64.urlsafe_b64encode(hmac.new(secret.encode(), f'{VERSION}.{body}'.encode(), hashlib.sha256).digest()).decode()
    return f'{VERSION}.{body}.{mac}'


def verify(token, settings=None, now=None):
    secret = _secret(settings)
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise CRMError(_INVALID, 401)
    parts = token.split('.')
    if len(parts) != 3 or parts[0] != VERSION:
        raise CRMError(_INVALID, 401)
    _, body, mac = parts
    if not body or not mac or any(char not in _B64URL for char in body + mac):
        raise CRMError(_INVALID, 401)
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except ValueError:
        raise CRMError(_INVALID, 401)
    if not isinstance(payload, dict) or set(payload) != {'cid', 'iat', 'exp'}:
        raise CRMError(_INVALID, 401)
    cid, iat, exp = payload['cid'], payload['iat'], payload['exp']
    if not isinstance(cid, str):
        raise CRMError(_INVALID, 401)
    try:
        conversation_id = UUID(cid)
    except ValueError:
        raise CRMError(_INVALID, 401)
    if str(conversation_id) != cid or type(iat) is not int or type(exp) is not int:
        raise CRMError(_INVALID, 401)
    if not 0 < exp - iat <= TTL_SECONDS:
        raise CRMError(_INVALID, 401)
    current = int(now if now is not None else time.time())
    if iat > current + FUTURE_SKEW_SECONDS:
        raise CRMError(_INVALID, 401)
    try:
        given = base64.urlsafe_b64decode(mac + '=' * (-len(mac) % 4))
    except ValueError:
        raise CRMError(_INVALID, 401)
    expected = hmac.new(secret.encode(), f'{VERSION}.{body}'.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(given, expected):
        raise CRMError(_INVALID, 401)
    if exp <= current:
        raise CRMError(_INVALID, 401)
    return conversation_id
