import { z } from 'zod';
import { api, ApiError } from './api';
import { useRef, useState } from 'react';

export const RecordSchema=z.object({id:z.uuid()}).catchall(z.unknown());
export type Row=z.infer<typeof RecordSchema>;
export const QualificationSchema=z.object({matching_ready:z.boolean(),qualification_ready:z.boolean(),matching_missing_fields:z.array(z.string()),missing_fields:z.array(z.string()),invalid_fields:z.array(z.string()),qualification_current:z.boolean(),needs_review:z.boolean()});
export type Qualification=z.infer<typeof QualificationSchema>;
export const MatchesSchema=z.object({lead_id:z.uuid(),lead_version:z.number().int(),checked_at:z.string(),matching_ready:z.boolean(),missing_fields:z.array(z.string()),invalid_fields:z.array(z.string()),criteria_snapshot:z.record(z.string(),z.unknown()),truncated:z.boolean(),criteria:z.string(),matches:z.array(RecordSchema.extend({title:z.string(),price:z.union([z.string(),z.number()]),bedrooms:z.number().int(),score:z.number(),version:z.number().int(),checks:z.array(z.object({criterion:z.enum(['active','budget','locality','minimum_bedrooms']),requested:z.unknown(),actual:z.unknown(),passed:z.literal(true)}))}))});
export const FaqSchema=z.object({id:z.uuid(),scope:z.enum(['business','property']),property_id:z.uuid().nullable(),status:z.enum(['active','archived']),current_version:z.number().int(),version:z.number().int(),question:z.string(),answer:z.string()});
export const FaqVersionSchema=z.object({faq_id:z.uuid(),version:z.number().int(),question:z.string(),answer:z.string(),created_at:z.string()});
export const FaqDetailSchema=z.object({record:FaqSchema,versions:z.array(FaqVersionSchema)});
export const CitationSchema=z.object({faq_id:z.uuid(),version:z.number().int(),quote:z.string()});
export const MessageSchema=RecordSchema.extend({conversation_id:z.uuid(),role:z.enum(['customer','assistant','owner','system']),body:z.string(),grounding:z.enum(['grounded','unsupported','ungrounded']),unsupported_reason:z.string().nullable(),citations:z.array(CitationSchema),confidence:z.enum(['high','medium','low']).nullable(),model_name:z.string().nullable(),response_id:z.string().nullable(),citations_superseded:z.boolean().optional()});
export const ConversationSchema=RecordSchema.extend({lead_id:z.uuid(),property_id:z.uuid().nullable(),status:z.enum(['open','pending_owner','resolved']),handoff_state:z.enum(['none','requested','resolved']),version:z.number().int()});
export const ConversationDetailSchema=z.object({record:ConversationSchema,messages:z.array(MessageSchema),truncated:z.boolean(),drafting_blocked:z.string().nullable()});
export const TaskSchema=RecordSchema.extend({lead_id:z.uuid(),property_id:z.uuid().nullable(),title:z.string(),notes:z.string(),due_at:z.string().nullable(),assignee:z.string(),status:z.enum(['open','in_progress','in_review','done','cancelled']),outcome:z.string().nullable(),version:z.number().int(),overdue:z.boolean().optional()});
export const TaskDetailSchema=z.object({record:TaskSchema,history:z.array(RecordSchema)});
export const DetailSchema=z.object({record:RecordSchema,qualification:QualificationSchema.optional(),appointments:z.array(RecordSchema).optional(),followups:z.array(RecordSchema).optional()});
export const ListSchema=z.object({items:z.array(RecordSchema),total:z.number().int().nonnegative(),page:z.number(),page_size:z.number()});
export const SummarySchema=z.object({metrics:z.object({leads:z.number(),new_leads:z.number(),properties:z.number(),upcoming_visits:z.number(),pending_approvals:z.number()}),recent_leads:z.array(RecordSchema),appointments:z.array(RecordSchema),requests:z.array(RecordSchema)});
export const SlotsSchema=z.object({items:z.array(RecordSchema),truncated:z.boolean(),provisional:z.literal(true)});
export const crmApi=(path:string,init?:RequestInit)=>api('/api/owner/crm/'+path,init);
export const message=(error:unknown)=>error instanceof Error ? error.message : 'The backend result could not be confirmed.';

type Intent={path:string;method:string;body:Record<string,unknown>};
function saved(scope:string):Intent|null {
  try {
    const raw=JSON.parse(sessionStorage.getItem('crm:'+scope) ?? 'null');
    return raw && typeof raw.path==='string' && typeof raw.body?.request_key==='string' && ['POST','PATCH'].includes(raw.method) ? raw : null;
  } catch {return null;}
}
// Persist the exact action before sending, so a lost response/reload cannot cause a new duplicate write.
export function useMutation(scope:string) {
  const [intent,setIntent]=useState<Intent|null>(()=>typeof window==='undefined'?null:saved(scope));
  const ref=useRef(intent);const gate=useRef(false);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  function clear(){ref.current=null;setIntent(null);sessionStorage.removeItem('crm:'+scope);}
  async function execute(path:string,body:Record<string,unknown>,method='POST'):Promise<Row|null> {
    if(gate.current)return null;
    const action=ref.current ?? {path,method,body:{...body,request_key:crypto.randomUUID()}};
    ref.current=action;setIntent(action);sessionStorage.setItem('crm:'+scope,JSON.stringify(action));
    gate.current=true;setBusy(true);setError('');
    try {
      let result:unknown;
      try {result=await crmApi(action.path,{method:action.method,body:JSON.stringify(action.body)});}
      catch(err) {
        if(err instanceof ApiError && err.status>=400 && err.status<500){clear();throw err;}
        // Confirm a committed operation before retrying an ambiguous response.
        try {result=await crmApi('operations/'+encodeURIComponent(String(action.body.request_key)));}
        catch {throw err;}
      }
      const record=DetailSchema.parse(result).record;
      clear();return record;
    } catch(err){setError(message(err));return null;}
    finally{gate.current=false;setBusy(false);}
  }
  return {execute,busy,error,pending:!!intent};
}
