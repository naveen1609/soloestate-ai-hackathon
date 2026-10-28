"""Browser-test fixture only. Creates no model response, message or booking."""
import json
import sys
from uuid import UUID, uuid4
from contextlib import contextmanager
from app.config import Settings
from app.database import connect
from app.tools.service import BusinessTools
from app.tools.schemas import ToolContext

if sys.argv[1] == "create":
    run, lead = uuid4(), uuid4()
    with connect(Settings()) as conn:
        conn.execute("INSERT INTO soloestate.runs(id,request_key,goal,status,plan) VALUES (%s,%s,'Phase8 isolated UI approval fixture','pending','{\"policy\":{\"mode\":\"draft_first\"}}')", (run,str(uuid4())))
        conn.execute("INSERT INTO soloestate.leads(id,name,email,preferred_localities,budget_min,budget_max,bedrooms) VALUES (%s,'Phase8 fictional browser lead',%s,ARRAY['Fictional'],100,200,2)", (lead,f'{lead}@example.invalid'))
        @contextmanager
        def factory():
            with conn.transaction():
                yield conn
        tools=BusinessTools(factory)
        draft=tools.execute('create_followup_draft',dict(lead_id=str(lead),subject='Isolated UI review',body='Fictional browser test. Never send.',idempotency_key='draft'),ToolContext(run_id=run))
        assert draft.status == 'succeeded', draft
        approval=tools.execute('request_approval',dict(kind='followup',draft_id=draft.data['draft']['id'],idempotency_key='review'),ToolContext(run_id=run))
        assert approval.status == 'approval_required', approval
    print(json.dumps(dict(run=str(run),lead=str(lead),approval=approval.data['approval']['id'])))
elif sys.argv[1] == 'cleanup':
    run,lead=map(UUID,sys.argv[2:4])
    with connect(Settings()) as conn:
        # Guard exact UUID deletion with the fixture's distinctive goal.
        assert conn.execute("SELECT goal FROM soloestate.runs WHERE id=%s",(run,)).fetchone()[0] == 'Phase8 isolated UI approval fixture'
        conn.execute('DELETE FROM soloestate.approval_decisions WHERE approval_id IN (SELECT id FROM soloestate.approvals WHERE run_id=%s)',(run,))
        for table in ('approvals','followup_drafts','run_steps'):
            from psycopg import sql
            conn.execute(sql.SQL('DELETE FROM soloestate.{} WHERE run_id=%s').format(sql.Identifier(table)),(run,))
        conn.execute('DELETE FROM soloestate.runs WHERE id=%s',(run,))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s',(lead,))
