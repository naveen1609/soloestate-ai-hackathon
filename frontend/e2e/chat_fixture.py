"""Fictional website-chat e2e fixture: unique plan values and exact-ID cleanup (no resets)."""
import json
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.config import Settings
from app.database import connect


def local(value):
    return value.astimezone().strftime('%Y-%m-%dT%H:%M')


def plan():
    prefix = 'BrowserChat-' + uuid4().hex[:8]
    ist = timezone(timedelta(hours=5, minutes=30))
    with connect(Settings()) as conn:
        base = conn.execute("SELECT greatest(coalesce(max(ends_at),clock_timestamp()),clock_timestamp())+interval '7 days' FROM soloestate.visit_slots").fetchone()[0].astimezone(ist)
    if base.weekday() == 1:
        base += timedelta(days=1)
    start = base.replace(hour=10, minute=30, second=0, microsecond=0)
    end = start + timedelta(minutes=30)
    print(json.dumps(dict(prefix=prefix, email=prefix.lower() + '@example.invalid',
                          starts=[start.isoformat(), end.isoformat()], start_local=local(start), end_local=local(end),
                          slot_ids=[], faq_ids=[], property_ids=[])))


def cleanup(payload):
    data = json.loads(payload)
    with connect(Settings()) as conn:
        leads = [row[0] for row in conn.execute('SELECT id FROM soloestate.leads WHERE name=%s', (data['prefix'],)).fetchall()]
        convos = [row[0] for row in conn.execute('SELECT id FROM soloestate.conversations WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchall()] if leads else []
        messages = [row[0] for row in conn.execute('SELECT id FROM soloestate.conversation_messages WHERE conversation_id=ANY(%s::uuid[])', (convos,)).fetchall()] if convos else []
        requests = [row[0] for row in conn.execute('SELECT id FROM soloestate.booking_requests WHERE conversation_id=ANY(%s::uuid[])', (convos,)).fetchall()] if convos else []
        booked = conn.execute('SELECT count(*) FROM soloestate.bookings WHERE lead_id=ANY(%s::uuid[])', (leads,)).fetchone()[0] if leads else 0
        if booked:
            raise RuntimeError('chat journey must not commit bookings; found %d' % booked)
        conn.execute('DELETE FROM soloestate.conversation_summaries WHERE conversation_id=ANY(%s::uuid[])', (convos,))
        conn.execute('DELETE FROM soloestate.conversation_messages WHERE id=ANY(%s::uuid[])', (messages,))
        conn.execute('DELETE FROM soloestate.conversations WHERE id=ANY(%s::uuid[])', (convos,))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=ANY(%s::uuid[])', (leads,))
        ents = [str(i) for i in leads + convos + messages + requests] + data['slot_ids'] + data['faq_ids'] + data['property_ids']
        conn.execute('DELETE FROM soloestate.crm_operations WHERE entity_id=ANY(%s::uuid[])', (ents,))
        conn.execute('DELETE FROM soloestate.booking_requests WHERE id=ANY(%s::uuid[])', (requests,))
        conn.execute('DELETE FROM soloestate.faq_versions WHERE faq_id=ANY(%s::uuid[])', (data['faq_ids'],))
        conn.execute('DELETE FROM soloestate.faqs WHERE id=ANY(%s::uuid[])', (data['faq_ids'],))
        conn.execute('DELETE FROM soloestate.properties WHERE id=ANY(%s::uuid[])', (data['property_ids'],))
        conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])', (data['slot_ids'],))
        conn.execute('DELETE FROM soloestate.leads WHERE id=ANY(%s::uuid[])', (leads,))
        remaining = conn.execute('SELECT count(*) FROM soloestate.leads WHERE name=%s', (data['prefix'],)).fetchone()[0]
        print(json.dumps(dict(remaining_leads=remaining)))


if __name__ == '__main__':
    if sys.argv[1] == 'plan':
        plan()
    else:
        cleanup(sys.argv[2])
