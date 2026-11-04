"""WhatsApp Cloud API outbound sender. No credentials in logs or errors."""
import httpx

class SenderNotConfigured(Exception):
    pass


class WhatsAppSender:
    """Synchronous text sender; dispatch drives it after the submitting
    transition commits. Raises SubmitFailed (definitive) or SubmitUnknown
    (may-have-submitted) from app.dispatch."""

    def __init__(self, *, api_base, phone_number_id, access_token, timeout_seconds=30, http_client=None):
        if not phone_number_id or not access_token:
            raise SenderNotConfigured('WhatsApp sender credentials are absent.')
        self._url = '%s/%s/messages' % (api_base.rstrip('/'), phone_number_id)
        self._headers = {'Authorization': 'Bearer ' + access_token}
        self._timeout = timeout_seconds
        self._client = http_client

    def send(self, recipient, text):
        from app.dispatch import SubmitFailed, SubmitUnknown
        if not recipient or not isinstance(text, str) or not text.strip() or len(text) > 4096:
            raise SubmitFailed('message outside provider bounds')
        body = {'messaging_product': 'whatsapp', 'recipient_type': 'individual',
                'to': recipient, 'type': 'text', 'text': {'preview_url': False, 'body': text}}
        try:
            if self._client is not None:
                response = self._client.post(self._url, headers=self._headers, json=body)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(self._url, headers=self._headers, json=body)
        except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError):
            raise SubmitUnknown('provider unreachable') from None
        if response.status_code == 200:
            try:
                message_id = response.json()['messages'][0]['id']
            except (ValueError, KeyError, IndexError, TypeError):
                raise SubmitUnknown('provider response unreadable') from None
            if not message_id:
                raise SubmitUnknown('provider response unreadable')
            return message_id
        if response.status_code in (400, 401, 403, 404):
            raise SubmitFailed('provider rejected the message (http %d)' % response.status_code)
        raise SubmitUnknown('provider returned http %d' % response.status_code)


def build_sender(config):
    """Real sender from server settings, or None when unconfigured.

    Returning None is the fail-closed path: staged rows wait for an
    operator to configure credentials and re-drive them. No mock mode
    exists on this path; tests inject stub callables instead.
    """
    token = config.whatsapp_access_token.get_secret_value() if config.whatsapp_access_token else None
    phone_id = config.whatsapp_phone_number_id
    if not token or not phone_id:
        return None
    return WhatsAppSender(api_base=config.whatsapp_api_base, phone_number_id=phone_id, access_token=token)
