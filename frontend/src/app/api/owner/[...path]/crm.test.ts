import {expect,it,vi} from 'vitest';
import {NextRequest} from 'next/server';
import {GET,POST,PATCH,permitted} from './route';
const ID='80000000-0000-4000-8000-000000000001';
it.each([['GET','crm/summary'],['GET','crm/leads'],['PATCH','crm/leads/'+ID],['POST','crm/properties'],['POST','crm/appointments/'+ID+'/cancel'],['POST','crm/appointment-requests/'+ID+'/commit'],['POST','crm/visit-slots']])('permits only explicit CRM method/path pairs: %s %s',(method,path)=>{expect(permitted(method,path)).toBe(true);});
it.each([['DELETE','crm/leads/'+ID],['POST','crm/execute_sql'],['PATCH','crm/appointments/'+ID],['GET','crm/../runs'],['POST','crm/appointments']])('rejects unauthorized CRM path shapes: %s %s',(method,path)=>{expect(permitted(method,path)).toBe(false);});
function request(path:string,method:string,origin='http://127.0.0.1:3000'){
 return new NextRequest('http://127.0.0.1:3000/api/owner/'+path,{method,headers:{host:'127.0.0.1:3000',origin,cookie:'soloestate_session=sess_test','content-type':'application/json'},body:method==='GET'?undefined:JSON.stringify({request_key:'test',expected_version:1})});
}
it('forwards a validated PATCH body using only the server-held session',async()=>{
 const fetch=vi.fn<(input:RequestInfo|URL,init?:RequestInit)=>Promise<Response>>(async()=>Response.json({record:{id:ID}}));vi.stubGlobal('fetch',fetch);
 const response=await PATCH(request('crm/leads/'+ID,'PATCH'),{params:Promise.resolve({path:['crm','leads',ID]})});
 expect(response.status).toBe(200);expect(fetch.mock.calls[0][1]).toMatchObject({method:'PATCH',headers:{Authorization:'Bearer sess_test'}});
});
it('rejects cross-origin CRM writes before reaching FastAPI',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);expect((await POST(request('crm/leads','POST','https://other.invalid'),{params:Promise.resolve({path:['crm','leads']})})).status).toBe(403);expect(fetch).not.toHaveBeenCalled();
});
it('rejects arbitrary query fields rather than creating a generic proxy',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);expect((await GET(request('crm/leads?sql=DROP','GET'),{params:Promise.resolve({path:['crm','leads']})})).status).toBe(400);expect(fetch).not.toHaveBeenCalled();
});

it.each([['GET','crm/leads/'+ID+'/activities'],['POST','crm/leads/'+ID+'/journey']])('permits B1 owner paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(true));
it.each([['PATCH','crm/leads/'+ID+'/journey'],['POST','crm/leads/'+ID+'/activities'],['GET','crm/leads/'+ID+'/consent']])('rejects unregistered B1 paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(false));


it.each([['GET','crm/leads/'+ID+'/matches'],['POST','crm/leads/'+ID+'/qualify']])('permits B2 owner paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(true));
it.each([['PATCH','crm/leads/'+ID+'/qualify'],['POST','crm/leads/'+ID+'/matches'],['POST','crm/qualify_all']])('rejects unregistered B2 paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(false));


it.each([['GET','crm/faqs'],['GET','crm/faqs/'+ID],['POST','crm/faqs'],['POST','crm/faqs/'+ID+'/versions'],['POST','crm/faqs/'+ID+'/archive'],['GET','crm/conversations'],['GET','crm/conversations/'+ID],['POST','crm/conversations'],['POST','crm/conversations/'+ID+'/messages'],['POST','crm/conversations/'+ID+'/draft'],['POST','crm/conversations/'+ID+'/handoff'],['POST','crm/conversations/'+ID+'/review']])('permits B3 owner paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(true));
it.each([['PATCH','crm/faqs/'+ID],['DELETE','crm/faqs/'+ID],['POST','crm/faqs/'+ID],['GET','crm/faqs/'+ID+'/versions'],['PATCH','crm/conversations/'+ID+'/messages'],['DELETE','crm/conversations/'+ID],['POST','crm/conversations/'+ID],['GET','crm/conversations/'+ID+'/draft'],['POST','crm/answer_all']])('rejects unregistered B3 paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(false));
it('allows only explicit faq and conversation queries',async()=>{
 const fetch=vi.fn(async()=>Response.json({items:[],total:0}));vi.stubGlobal('fetch',fetch);
 const faqs={params:Promise.resolve({path:['crm','faqs']})};const convos={params:Promise.resolve({path:['crm','conversations']})};
 expect((await GET(request('crm/faqs?scope=business&status=active','GET'),faqs)).status).toBe(200);
 expect((await GET(request('crm/faqs?sql=DROP','GET'),faqs)).status).toBe(400);
 expect((await GET(request('crm/conversations?lead_id='+ID,'GET'),convos)).status).toBe(200);
 expect((await GET(request('crm/conversations?debug=1','GET'),convos)).status).toBe(400);
 expect(fetch).toHaveBeenCalledTimes(2);
});
it('allows only the explicit match limit query',async()=>{
 const fetch=vi.fn(async()=>Response.json({matches:[]}));vi.stubGlobal('fetch',fetch);
 const context={params:Promise.resolve({path:['crm','leads',ID,'matches']})};
 expect((await GET(request('crm/leads/'+ID+'/matches?limit=5','GET'),context)).status).toBe(200);
 expect((await GET(request('crm/leads/'+ID+'/matches?override_budget=1','GET'),context)).status).toBe(400);
});
