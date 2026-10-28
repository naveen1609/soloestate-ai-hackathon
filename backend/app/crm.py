"""Owner CRM boundary. Fixed SQL, shared consent tools, atomic operations and audit."""
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
import json
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from fastapi.encoders import jsonable_encoder

from app.config import Settings
from app.database import connect
from app.owner import OwnerService, DecisionRequest, payload_hash
from app.agent.store import RunStore
from app.tools.schemas import ToolContext
from app.tools.service import BusinessTools


class CRMError(Exception):
    def __init__(self,message,status=409):
        self.status=status
        super().__init__(message)


def encode(value):
    return jsonable_encoder(value,custom_encoder={Decimal:str})


def found(row):
    if row is None: raise CRMError('Record not found.',404)
    return row


SORTS={
 'leads':{'created_desc':'e.created_at DESC','name_asc':'e.name ASC','name_desc':'e.name DESC','budget_desc':'e.budget_max DESC'},
 'properties':{'created_desc':'e.created_at DESC','title_asc':'e.title ASC','price_asc':'e.price ASC','price_desc':'e.price DESC'},
 'appointments':{'starts_asc':'s.starts_at ASC','starts_desc':'s.starts_at DESC','created_desc':'e.created_at DESC'},
}
TABLES={'leads':'leads','properties':'properties','appointments':'bookings'}
APPOINTMENT_SELECT="e.*,l.name AS lead_name,l.email,p.title AS property_title,s.starts_at,s.ends_at,r.origin"
APPOINTMENT_FROM="soloestate.bookings e JOIN soloestate.leads l ON l.id=e.lead_id JOIN soloestate.properties p ON p.id=e.property_id JOIN soloestate.visit_slots s ON s.id=e.slot_id JOIN soloestate.runs r ON r.id=e.run_id"


class CRM:
    def __init__(self,connection_factory=None):
        self.connect=connection_factory or (lambda:connect(Settings()))

    def operation(self,key):
        with self.connect() as conn:
            return found(conn.execute('SELECT response FROM soloestate.crm_operations WHERE request_key=%s',(key,)).fetchone())[0]

    def mutate(self,action,request,work):
        data=request.model_dump(mode='json')
        digest=payload_hash({'action':action,'arguments':data})
        try:
            with self.connect() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('crm:'+request.request_key,))
                prior=conn.execute('SELECT fingerprint,response FROM soloestate.crm_operations WHERE request_key=%s',(request.request_key,)).fetchone()
                if prior:
                    if prior[0]!=digest: raise CRMError('Request key already belongs to a different operation.')
                    response=prior[1]
                else:
                    response=encode(work(conn))
                    conn.execute("INSERT INTO soloestate.crm_operations(request_key,action,fingerprint,actor,entity_id,response) VALUES (%s,%s,%s,'owner',%s,%s)",
                        (request.request_key,action,digest,response['record']['id'],Jsonb(response)))
            # Never acknowledge a mutation before the outer connection commits.
            return response
        except psycopg.IntegrityError:
            raise CRMError('Conflicting record, duplicate email, overlapping slot or stale booking details. No new operation confirmed.') from None
        except (psycopg.errors.LockNotAvailable,psycopg.errors.DeadlockDetected,psycopg.errors.SerializationFailure):
            raise CRMError('Another operation is executing. Retry the same saved request; no new result is confirmed.',503) from None

    def listing(self,entity,*,q='',sort=None,page=1,page_size=20,status=None,archived=False,active=None,locality=None,bedrooms=None,min_price=None,max_price=None,period=None,lead_id=None,property_id=None,since=None,until=None):
        if entity not in TABLES: raise CRMError('Unknown entity.',404)
        order=sort or next(iter(SORTS[entity]))
        if order not in SORTS[entity]: raise CRMError('Sort is not permitted.',422)
        conditions,values=[],[]
        def where(clause,value=None):
            conditions.append(clause)
            if value is not None: values.append(value)
        if entity=='leads':
            base,selection='soloestate.leads e','e.*'
            where('e.archived_at IS '+('NOT NULL' if archived else 'NULL'))
            if status:
                if status not in ('new','contacted','qualified','closed'): raise CRMError('Invalid lead status.',422)
                where('e.status=%s',status)
            searchable="(e.name ILIKE %s OR e.email ILIKE %s)"
        elif entity=='properties':
            base,selection='soloestate.properties e','e.*'
            if active is not None: where('e.active=%s',active)
            if locality: where('e.locality ILIKE %s',self.pattern(locality))
            if bedrooms: where('e.bedrooms=%s',bedrooms)
            if min_price is not None: where('e.price>=%s',min_price)
            if max_price is not None: where('e.price<=%s',max_price)
            searchable="(e.title ILIKE %s OR e.locality ILIKE %s)"
        else:
            base,selection=APPOINTMENT_FROM,APPOINTMENT_SELECT
            if status:
                if status not in ('confirmed','cancelled'): raise CRMError('Invalid appointment status.',422)
                where('e.status=%s',status)
            if period=='upcoming': where('s.starts_at>clock_timestamp()')
            elif period=='past': where('s.ends_at<=clock_timestamp()')
            if lead_id: where('e.lead_id=%s',lead_id)
            if property_id: where('e.property_id=%s',property_id)
            if since: where('s.starts_at>=%s',since)
            if until: where('s.starts_at<%s',until)
            searchable="(l.name ILIKE %s OR p.title ILIKE %s)"
        if q:
            conditions.append(searchable);values.extend([self.pattern(q)]*2)
        clause=' WHERE '+' AND '.join(conditions) if conditions else ''
        # All SQL fragments above are repository constants. User values remain parameters.
        with self.connect() as conn:
            total=conn.execute(f'SELECT count(*) FROM {base}{clause}',values).fetchone()[0]
            with conn.cursor(row_factory=dict_row) as cur:
                rows=cur.execute(f'SELECT {selection} FROM {base}{clause} ORDER BY {SORTS[entity][order]},e.id LIMIT %s OFFSET %s',[*values,page_size,(page-1)*page_size]).fetchall()
        return encode(dict(items=rows,total=total,page=page,page_size=page_size))

    @staticmethod
    def pattern(value):
        return '%'+value.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'

    def detail(self,entity,identifier):
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                if entity=='appointments':
                    row=cur.execute(f'SELECT {APPOINTMENT_SELECT} FROM {APPOINTMENT_FROM} WHERE e.id=%s',(identifier,)).fetchone()
                else:
                    row=cur.execute(sql.SQL('SELECT * FROM soloestate.{} WHERE id=%s').format(sql.Identifier(TABLES[entity])),(identifier,)).fetchone()
                result={'record':found(row)}
                if entity in ('leads','properties'):
                    column='lead_id' if entity=='leads' else 'property_id'
                    result['appointments']=cur.execute(f'SELECT {APPOINTMENT_SELECT} FROM {APPOINTMENT_FROM} WHERE e.{column}=%s ORDER BY s.starts_at DESC LIMIT 20',(identifier,)).fetchall()
                if entity=='leads':
                    result['followups']=cur.execute('SELECT id,subject,status,version,created_at FROM soloestate.followup_drafts WHERE lead_id=%s ORDER BY created_at DESC LIMIT 20',(identifier,)).fetchall()
        return encode(result)

    def save(self,entity,request,identifier=None):
        columns={'leads':('name','email','preferred_localities','budget_min','budget_max','bedrooms','status','notes'),
                 'properties':('title','locality','price','bedrooms','description','active')}[entity]
        def work(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                if identifier:
                    current=found(cur.execute(sql.SQL('SELECT * FROM soloestate.{} WHERE id=%s FOR UPDATE').format(sql.Identifier(entity)),(identifier,)).fetchone())
                    if current['version']!=request.expected_version: raise CRMError('Record changed. Reload before editing.')
                if entity=='leads':
                    # Normalized comparison also protects pre-CRM mixed-case records.
                    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('email:'+request.email,))
                    duplicate=cur.execute('SELECT id FROM soloestate.leads WHERE lower(email)=%s AND id<>%s',(request.email,identifier or uuid4())).fetchone()
                    if duplicate: raise CRMError('A lead already uses this email.')
                values=[getattr(request,key) for key in columns]
                if identifier:
                    query=sql.SQL('UPDATE soloestate.{} SET {} WHERE id=%s RETURNING *').format(sql.Identifier(entity),sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(key)) for key in columns))
                    row=cur.execute(query,[*values,identifier]).fetchone()
                else:
                    query=sql.SQL('INSERT INTO soloestate.{} (id,{}) VALUES (%s,{}) RETURNING *').format(sql.Identifier(entity),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder() for _ in columns))
                    row=cur.execute(query,[uuid4(),*values]).fetchone()
                return {'record':row}
        return self.mutate(f'{entity}:{identifier or "create"}',request,work)

    def archive(self,identifier,request):
        def work(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                row=found(cur.execute('SELECT * FROM soloestate.leads WHERE id=%s FOR UPDATE',(identifier,)).fetchone())
                if row['version']!=request.expected_version: raise CRMError('Record changed. Reload before archiving.')
                row=cur.execute('UPDATE soloestate.leads SET archived_at=%s WHERE id=%s RETURNING *',(datetime.now(timezone.utc) if request.archived else None,identifier)).fetchone()
                return {'record':row}
        return self.mutate(f'archive:{identifier}',request,work)

    def slot(self,request):
        def work(conn):
            with conn.cursor(row_factory=dict_row) as cur:
                if request.starts_at<=datetime.now(timezone.utc): raise CRMError('Visit slot must start in the future.',422)
                return {'record':cur.execute('INSERT INTO soloestate.visit_slots(id,starts_at,ends_at) VALUES (%s,%s,%s) RETURNING *',(uuid4(),request.starts_at,request.ends_at)).fetchone()}
        return self.mutate('slot:create',request,work)

    def slots(self,since,until):
        from app.tools.schemas import GetSlotsInput
        args=GetSlotsInput(since=since,until=until,limit=100)
        result=BusinessTools(self.connect).execute('get_available_slots',args.model_dump(mode='json'),ToolContext(run_id=uuid4()))
        if result.status!='succeeded': raise CRMError('Availability could not be confirmed.',503)
        return {'items':result.data['slots'],'truncated':result.data['truncated'],'provisional':True}

    @staticmethod
    @contextmanager
    def nested(conn):
        with conn.transaction(): yield conn

    def request_detail(self,identifier,conn=None):
        if conn is None:
            with self.connect() as connection: return self.request_detail(identifier,connection)
        with conn.cursor(row_factory=dict_row) as cur:
            row=found(cur.execute("SELECT id,status,plan FROM soloestate.runs WHERE id=%s AND origin='owner'",(identifier,)).fetchone())
            approval=found(cur.execute('SELECT id,payload,status FROM soloestate.approvals WHERE run_id=%s',(identifier,)).fetchone())
            booking=cur.execute('SELECT id FROM soloestate.bookings WHERE run_id=%s',(identifier,)).fetchone()
        return encode({'record':{'id':row['id'],'status':row['status'],'approval_id':approval['id'],
            'approval_status':approval['status'],'payload':approval['payload'],'payload_hash':payload_hash(approval['payload']),
            'replacement':row['plan'].get('owner_operation'),'booking_id':booking['id'] if booking else None}})

    def prepare(self,request):
        def work(conn):
            if request.replaces_booking_id:
                old=self._booking(conn,request.replaces_booking_id,request.expected_version)
                if old['lead_id']!=request.lead_id: raise CRMError('Rescheduling cannot change the customer.',422)
                if old['slot_id']==request.slot_id: raise CRMError('Choose a different visit slot.',422)
            run=uuid4()
            intent=request.model_dump(mode='json',exclude={'request_key'})
            conn.execute("INSERT INTO soloestate.runs(id,request_key,goal,status,origin,plan) VALUES (%s,%s,'Owner appointment operation','pending','owner',%s)",(run,'owner:'+str(run),Jsonb({'policy':{'mode':'draft_first'},'owner_operation':intent})))
            tool=BusinessTools(lambda:self.nested(conn))
            result=tool.execute('request_approval',dict(kind='booking',lead_id=str(request.lead_id),property_id=str(request.property_id),slot_id=str(request.slot_id),idempotency_key='owner-request'),ToolContext(run_id=run))
            if result.status!='approval_required': raise CRMError(result.message or 'Appointment request failed.')
            RunStore(lambda:self.nested(conn)).finish(run,'awaiting_approval','owner_approval_required',[],[],connection=conn)
            return self.request_detail(run,conn)
        return self.mutate('appointment:prepare',request,work)

    @staticmethod
    def _booking(conn,identifier,version):
        with conn.cursor(row_factory=dict_row) as cur:
            row=found(cur.execute('SELECT * FROM soloestate.bookings WHERE id=%s FOR UPDATE',(identifier,)).fetchone())
            if row['version']!=version or row['status']!='confirmed': raise CRMError('Appointment changed or is no longer confirmed. Reload it.')
            active=conn.execute("SELECT status='running' AND lease_until>clock_timestamp() FROM soloestate.runs WHERE id=%s",(row['run_id'],)).fetchone()[0]
            if active: raise CRMError('The agent is still executing this appointment. Wait until it stops.')
        return row

    def commit(self,identifier,request):
        def work(conn):
            # Lock this workflow and (for replacements) its original booking only.
            # Shared tool slot locks and unique constraints arbitrate competing bookers.
            row=found(conn.execute("SELECT plan FROM soloestate.runs WHERE id=%s AND origin='owner' FOR UPDATE",(identifier,)).fetchone())
            details=self.request_detail(identifier,conn)['record']
            if details['payload_hash']!=request.expected_payload_hash: raise CRMError('Review snapshot changed.')
            if details['approval_status'] not in ('pending','approved'): raise CRMError('This request cannot be booked.')
            intent=row[0]['owner_operation']
            if intent['replaces_booking_id']:
                self._booking(conn,intent['replaces_booking_id'],intent['expected_version'])
            factory=lambda:self.nested(conn)
            if details['approval_status']=='pending':
                OwnerService(factory).decide(details['approval_id'],DecisionRequest(decision='approved',expected_payload_hash=request.expected_payload_hash,idempotency_key='crm-decision:'+str(identifier)))
            if intent['replaces_booking_id']:
                conn.execute("UPDATE soloestate.bookings SET status='cancelled',cancellation_reason='Rescheduled by owner' WHERE id=%s",(intent['replaces_booking_id'],))
            result=BusinessTools(factory).execute('book_site_visit',dict(approval_id=details['approval_id'],idempotency_key='owner-book'),ToolContext(run_id=identifier,replaces_booking_id=intent['replaces_booking_id']))
            if result.status!='succeeded': raise CRMError(result.message or 'Booking could not be committed.')
            booking=result.data['booking']['id']
            RunStore(factory).finish(identifier,'completed','owner_booking_committed',[],[],connection=conn)
            return {'record':self._appointment_row(conn,booking)}
        return self.mutate(f'appointment:commit:{identifier}',request,work)

    @staticmethod
    def _appointment_row(conn,identifier):
        with conn.cursor(row_factory=dict_row) as cur:
            return found(cur.execute(f'SELECT {APPOINTMENT_SELECT} FROM {APPOINTMENT_FROM} WHERE e.id=%s',(identifier,)).fetchone())

    def cancel(self,identifier,request):
        def work(conn):
            self._booking(conn,identifier,request.expected_version)
            conn.execute("UPDATE soloestate.bookings SET status='cancelled',cancellation_reason=%s WHERE id=%s",(request.reason,identifier))
            return {'record':self._appointment_row(conn,identifier)}
        return self.mutate(f'appointment:cancel:{identifier}',request,work)

    def reject(self,identifier,request):
        def work(conn):
            found(conn.execute("SELECT id FROM soloestate.runs WHERE id=%s AND origin='owner' FOR UPDATE",(identifier,)).fetchone())
            details=self.request_detail(identifier,conn)['record']
            OwnerService(lambda:self.nested(conn)).decide(details['approval_id'],DecisionRequest(decision='rejected',expected_payload_hash=request.expected_payload_hash,idempotency_key='crm-reject:'+str(identifier)))
            return self.request_detail(identifier,conn)
        return self.mutate(f'appointment:reject:{identifier}',request,work)

    def summary(self):
        with self.connect() as conn:
            with conn.cursor(row_factory=dict_row) as cur:
                metrics=cur.execute("""SELECT
                 (SELECT count(*) FROM soloestate.leads WHERE archived_at IS NULL) AS leads,
                 (SELECT count(*) FROM soloestate.leads WHERE archived_at IS NULL AND status='new') AS new_leads,
                 (SELECT count(*) FROM soloestate.properties WHERE active) AS properties,
                 (SELECT count(*) FROM soloestate.bookings b JOIN soloestate.visit_slots s ON s.id=b.slot_id WHERE b.status='confirmed' AND s.starts_at>clock_timestamp()) AS upcoming_visits,
                 (SELECT count(*) FROM soloestate.approvals WHERE status='pending') AS pending_approvals""").fetchone()
                leads=cur.execute('SELECT id,name,email,status FROM soloestate.leads WHERE archived_at IS NULL ORDER BY created_at DESC,id LIMIT 5').fetchall()
                appointments=cur.execute(f"SELECT {APPOINTMENT_SELECT} FROM {APPOINTMENT_FROM} WHERE e.status='confirmed' AND s.starts_at>clock_timestamp() ORDER BY s.starts_at,e.id LIMIT 10").fetchall()
                requests=cur.execute("SELECT id,status,created_at FROM soloestate.runs WHERE origin='owner' AND status IN ('pending','awaiting_approval') ORDER BY created_at DESC LIMIT 20").fetchall()
        return encode(dict(metrics=metrics,recent_leads=leads,appointments=appointments,requests=requests))
