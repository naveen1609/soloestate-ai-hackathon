"""Isolated browser-test scenario. Real tools/evidence; no model call or delivery."""
import json
import sys
from uuid import UUID, uuid4
from contextlib import contextmanager
from psycopg import sql
from app.config import Settings
from app.database import connect
from app.agent.schemas import Plan, RunRequest
from app.agent.store import RunStore
from app.tools.service import BusinessTools
from app.tools.schemas import ToolContext

GOAL = 'Isolated browser regression fixture'

if sys.argv[1] == 'create':
    lead, prop = uuid4(), uuid4()
    lead_name, property_title = f'Browser Lead {lead}', f'Browser Property {prop}'
    with connect(Settings()) as conn:
        @contextmanager
        def factory():
            with conn.transaction():
                yield conn
        store, tools = RunStore(factory), BusinessTools(factory)
        row, fresh = store.create(RunRequest(goal=GOAL, request_key=str(uuid4())))
        run = row['id']
        conn.execute("INSERT INTO soloestate.leads(id,name,email,preferred_localities,budget_min,budget_max,bedrooms,created_at) VALUES (%s,%s,%s,%s,100,200,2,'2034-01-02T00:00:00Z')", (lead,lead_name,f'{lead}@example.invalid',[str(prop)]))
        conn.execute("INSERT INTO soloestate.properties(id,title,locality,price,bedrooms) VALUES (%s,%s,%s,150,2)", (prop,property_title,str(prop)))
        names=['get_leads','get_property_matches','create_followup_draft','request_approval']
        store.checkpoint(run,[],[],plan=Plan(summary='Isolated browser plan with real tool results',owner_approval_required=True,
            steps=[dict(tool=name,purpose='Verify isolated browser evidence',required=True) for name in names]))
        context = ToolContext(run_id=run)
        def execute(name, args):
            sequence = conn.execute('SELECT count(*)+1 FROM soloestate.run_steps WHERE run_id=%s',(run,)).fetchone()[0]
            step = store.start_step(run,sequence,str(uuid4()),name,args)
            result = tools.execute(name,args,context,on_result=lambda c,r:store.complete_step(c,step,r))
            if result.status == 'error':
                store.failed_step(step,result)
            return result
        assert execute('get_leads',dict(since='2034-01-02T00:00:00Z',until='2034-01-03T00:00:00Z')).status == 'succeeded'
        assert execute('get_property_matches',dict(lead_id=str(uuid4()))).code == 'not_found'
        assert execute('get_property_matches',dict(lead_id=str(lead))).status == 'succeeded'
        draft=execute('create_followup_draft',dict(lead_id=str(lead),subject='Isolated UI review',body='Fictional browser test. Never send.',idempotency_key='draft'))
        assert draft.status == 'succeeded', draft
        approval=execute('request_approval',dict(kind='followup',draft_id=draft.data['draft']['id'],idempotency_key='review'))
        assert approval.status == 'approval_required', approval
        store.finish(run,'awaiting_approval','owner_approval_required',[],[])
    print(json.dumps(dict(run=str(run),lead=str(lead),property=str(prop),approval=approval.data['approval']['id'],lead_name=lead_name,property_title=property_title)))
elif sys.argv[1] == 'cleanup':
    run,lead,prop=map(UUID,sys.argv[2:5])
    with connect(Settings()) as conn:
        # Check every target before deleting exact fixture-owned UUIDs in FK order.
        assert conn.execute('SELECT goal FROM soloestate.runs WHERE id=%s',(run,)).fetchone()[0] == GOAL
        assert conn.execute('SELECT email FROM soloestate.leads WHERE id=%s',(lead,)).fetchone()[0] == f'{lead}@example.invalid'
        assert conn.execute('SELECT locality FROM soloestate.properties WHERE id=%s',(prop,)).fetchone()[0] == str(prop)
        conn.execute('DELETE FROM soloestate.approval_decisions WHERE approval_id IN (SELECT id FROM soloestate.approvals WHERE run_id=%s)',(run,))
        for table in ('approvals','followup_drafts','run_steps'):
            conn.execute(sql.SQL('DELETE FROM soloestate.{} WHERE run_id=%s').format(sql.Identifier(table)),(run,))
        conn.execute('DELETE FROM soloestate.runs WHERE id=%s',(run,))
        conn.execute('DELETE FROM soloestate.properties WHERE id=%s',(prop,))
        conn.execute('DELETE FROM soloestate.leads WHERE id=%s',(lead,))
