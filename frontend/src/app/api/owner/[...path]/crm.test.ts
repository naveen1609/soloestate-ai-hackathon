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
