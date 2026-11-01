import { z } from 'zod';
import { api, ApiError } from './api';
import { useRef, useState } from 'react';
import { message, QualificationSchema } from './crm';

export const ChatCitationSchema=z.object({faq_id:z.uuid(),version:z.number().int(),quote:z.string()});
export const ChatMessageSchema=z.object({id:z.uuid(),role:z.string(),body:z.string(),grounding:z.string(),unsupported_reason:z.string().nullable(),citations:z.array(ChatCitationSchema),citations_superseded:z.boolean(),confidence:z.string().nullable(),supersedes_message_id:z.uuid().nullable(),created_at:z.string()});
export type ChatMessage=z.infer<typeof ChatMessageSchema>;
export const ChatLeadSchema=z.object({id:z.uuid(),version:z.number().int(),name:z.string(),preferred_localities:z.array(z.string()).nullable(),budget_min:z.union([z.string(),z.number()]).nullable(),budget_max:z.union([z.string(),z.number()]).nullable(),bedrooms:z.number().int().nullable(),purchase_timeframe:z.string().nullable(),readiness:QualificationSchema});
export const ChatConversationSchema=z.object({id:z.uuid(),lead_id:z.uuid(),status:z.string(),handoff_state:z.string(),property_id:z.uuid().nullable(),version:z.number().int(),created_at:z.string()});
export const ChatThreadSchema=z.object({lead:ChatLeadSchema,conversation:ChatConversationSchema,messages:z.array(ChatMessageSchema),truncated:z.boolean(),drafting_blocked:z.string().nullable()});
export type ChatThread=z.infer<typeof ChatThreadSchema>;
export const ChatStartSchema=z.object({lead:ChatLeadSchema,conversation:ChatConversationSchema,messages:z.array(ChatMessageSchema)});
export const ChatSendSchema=z.object({message:ChatMessageSchema,reply:ChatMessageSchema,conversation:ChatConversationSchema});
export const ChatLeadResultSchema=z.object({lead:ChatLeadSchema});
export const ChatConversationResultSchema=z.object({conversation:ChatConversationSchema});
export const chatApi=(path:string,init?:RequestInit)=>api('/api/chat/chat/'+path,init);
export {message};

type Intent={path:string;method:string;body:Record<string,unknown>};
function saved(scope:string):Intent|null {
  try {
    const raw=JSON.parse(sessionStorage.getItem('chat:'+scope) ?? 'null');
    return raw && typeof raw.path==='string' && typeof raw.body?.request_key==='string' && raw.method==='POST' ? raw : null;
  } catch {return null;}
}
// Persist the exact action before sending; visitors cannot query operations, so an ambiguous
// failure keeps the saved key and the retry replays it instead of writing twice.
export function useChatMutation(scope:string) {
  const [intent,setIntent]=useState<Intent|null>(()=>typeof window==='undefined'?null:saved(scope));
  const ref=useRef(intent);const gate=useRef(false);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  function clear(){ref.current=null;setIntent(null);sessionStorage.removeItem('chat:'+scope);}
  async function execute<S>(path:string,body:Record<string,unknown>,schema:z.ZodType<S>,method='POST'):Promise<S|null> {
    if(gate.current)return null;
    const action=ref.current ?? {path,method,body:{...body,request_key:crypto.randomUUID()}};
    ref.current=action;setIntent(action);sessionStorage.setItem('chat:'+scope,JSON.stringify(action));
    gate.current=true;setBusy(true);setError('');
    try {
      const result=await chatApi(action.path,{method:action.method,body:JSON.stringify(action.body)});
      const parsed=schema.parse(result);
      clear();return parsed;
    } catch(err){
      if(err instanceof ApiError && err.status>=400 && err.status<500)clear();
      setError(message(err));return null;
    }
    finally{gate.current=false;setBusy(false);}
  }
  return {execute,busy,error,pending:!!intent};
}
