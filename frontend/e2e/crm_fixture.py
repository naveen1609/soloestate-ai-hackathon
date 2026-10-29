"""Browser-test coordinates and exact cleanup. Does not seed production records."""
import json,sys
from datetime import timedelta,timezone
from uuid import uuid4
from app.config import Settings
from app.database import connect

if sys.argv[1]=='plan':
    with connect(Settings()) as conn:
        start=conn.execute("SELECT greatest(coalesce(max(ends_at),clock_timestamp()),clock_timestamp())+interval '7 days' FROM soloestate.visit_slots").fetchone()[0].replace(second=0,microsecond=0)
    local=lambda value:value.astimezone(timezone(timedelta(hours=5,minutes=30))).strftime('%Y-%m-%dT%H:%M')
    prefix='BrowserCRM-'+str(uuid4())
    print(json.dumps(dict(prefix=prefix,email=prefix.lower()+'@example.invalid',starts=[(start+timedelta(hours=i)).isoformat() for i in (0,2)],start_local=[local(start+timedelta(hours=i)) for i in (0,2)],end_local=[local(start+timedelta(hours=i,minutes=30)) for i in (0,2)])))
elif sys.argv[1]=='cleanup':
    data=json.loads(sys.argv[2]);prefix=data['prefix']
    assert prefix.startswith('BrowserCRM-') and len(prefix)==47
    with connect(Settings()) as conn:
        leads=[r[0] for r in conn.execute('SELECT id FROM soloestate.leads WHERE email=%s',(data['email'],)).fetchall()]
        props=[r[0] for r in conn.execute('SELECT id FROM soloestate.properties WHERE title=%s',(prefix,)).fetchall()]
        slots=[r[0] for r in conn.execute('SELECT id FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[]) AND starts_at=ANY(%s::timestamptz[])',(data.get('slot_ids',[]),data['starts'])).fetchall()]
        runs=[r[0] for r in conn.execute("SELECT DISTINCT run_id FROM soloestate.approvals WHERE lead_id=ANY(%s::uuid[])",(leads,)).fetchall()]
        bookings=[r[0] for r in conn.execute('SELECT id FROM soloestate.bookings WHERE lead_id=ANY(%s::uuid[])',(leads,)).fetchall()]
        ids=leads+props+slots+runs+bookings
        # Exact fixture ownership: replacement bookings before originals for the self-FK.
        conn.execute('DELETE FROM soloestate.bookings WHERE lead_id=ANY(%s::uuid[]) AND replaces_booking_id IS NOT NULL',(leads,))
        conn.execute('DELETE FROM soloestate.bookings WHERE lead_id=ANY(%s::uuid[])',(leads,))
        conn.execute('DELETE FROM soloestate.approval_decisions WHERE approval_id IN (SELECT id FROM soloestate.approvals WHERE run_id=ANY(%s::uuid[]))',(runs,))
        conn.execute('DELETE FROM soloestate.approvals WHERE run_id=ANY(%s::uuid[])',(runs,))
        conn.execute('DELETE FROM soloestate.run_steps WHERE run_id=ANY(%s::uuid[])',(runs,))
        conn.execute('DELETE FROM soloestate.runs WHERE id=ANY(%s::uuid[])',(runs,))
        conn.execute('DELETE FROM soloestate.lead_activities WHERE lead_id=ANY(%s::uuid[])',(leads,))
        conn.execute('DELETE FROM soloestate.crm_operations WHERE entity_id=ANY(%s::uuid[])',(ids,))
        conn.execute('DELETE FROM soloestate.visit_slots WHERE id=ANY(%s::uuid[])',(slots,))
        conn.execute('DELETE FROM soloestate.properties WHERE id=ANY(%s::uuid[])',(props,))
        conn.execute('DELETE FROM soloestate.leads WHERE id=ANY(%s::uuid[])',(leads,))
