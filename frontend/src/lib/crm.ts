import { z } from 'zod';
import { api, ApiError } from './api';
import { useRef, useState } from 'react';

export const RecordSchema=z.object({id:z.uuid()}).catchall(z.unknown());
export type Row=z.infer<typeof RecordSchema>;
export const DetailSchema=z.object({record:RecordSchema,appointments:z.array(RecordSchema).optional(),followups:z.array(RecordSchema).optional()});
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
