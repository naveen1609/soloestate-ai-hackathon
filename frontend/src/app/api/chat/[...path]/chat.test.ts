import {expect,it,vi} from 'vitest';
import {NextRequest} from 'next/server';
import {GET,POST,permitted,VISITOR_COOKIE} from './route';
const ID='80000000-0000-4000-8000-000000000001';
const TOKEN='v1.eyJjaWQiOiI4MDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMDAwMDAwMDEifQ.MAC';
function upstream(payload:unknown,status=200){
 return vi.fn(async (input:string|URL|Request,init?:RequestInit):Promise<Response>=>{void input;void init;return Response.json(payload,{status});});
}
it.each([['POST','chat/start'],['GET','chat/conversations/'+ID],['POST','chat/conversations/'+ID+'/send'],['POST','chat/conversations/'+ID+'/requirements'],['POST','chat/conversations/'+ID+'/context'],['GET','chat/conversations/'+ID+'/matches'],['GET','chat/conversations/'+ID+'/slots'],['POST','chat/conversations/'+ID+'/handoff']])('permits chat paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(true));
it.each([['GET','chat/start'],['PATCH','chat/conversations/'+ID+'/send'],['DELETE','chat/conversations/'+ID],['POST','chat/conversations/'+ID],['GET','chat/conversations/'+ID+'/send'],['GET','chat/conversations/'+ID+'/context'],['POST','chat/conversations/'+ID+'/draft'],['POST','chat/conversations/'+ID+'/review'],['POST','chat/conversations/'+ID+'/resolve'],['POST','chat/conversations/'+ID+'/approve'],['POST','chat/conversations/'+ID+'/book'],['POST','chat/start_all'],['GET','crm/leads'],['POST','crm/conversations']])('rejects unregistered chat paths %s %s',(method,path)=>expect(permitted(method,path)).toBe(false));
function request(path:string,method:string,cookie='soloestate_visitor='+TOKEN,origin='http://127.0.0.1:3000'){
 return new NextRequest('http://127.0.0.1:3000/api/chat/'+path,{method,headers:{host:'127.0.0.1:3000',origin,cookie,'content-type':'application/json'},body:method==='GET'?undefined:JSON.stringify({request_key:'test',expected_version:1})});
}
const context=(path:string[])=>({params:Promise.resolve({path})});
it('starts chats without a cookie and stores the issued token outside the body',async()=>{
 const fetch=upstream({lead:{id:ID},conversation:{id:ID},messages:[],token:TOKEN});vi.stubGlobal('fetch',fetch);
 const response=await POST(request('chat/start','POST',''),context(['chat','start']));
 expect(response.status).toBe(200);
 expect(response.cookies.get(VISITOR_COOKIE)?.value).toBe(TOKEN);
 expect(await response.json()).toEqual({lead:{id:ID},conversation:{id:ID},messages:[]});
 expect(fetch.mock.calls[0][1]).toMatchObject({method:'POST'});
 expect('Authorization' in (fetch.mock.calls[0][1]?.headers as Record<string,string>)).toBe(false);
});
it('rejects invalid upstream chat tokens without setting a cookie',async()=>{
 const fetch=upstream({token:'not-a-token'});vi.stubGlobal('fetch',fetch);
 const response=await POST(request('chat/start','POST',''),context(['chat','start']));
 expect(response.status).toBe(502);
 expect(response.cookies.get(VISITOR_COOKIE)).toBeUndefined();
});
it('requires the visitor cookie on every other chat path and ignores owner sessions',async()=>{
 const fetch=upstream({});vi.stubGlobal('fetch',fetch);
 expect((await GET(request('chat/conversations/'+ID,'GET',''),context(['chat','conversations',ID]))).status).toBe(401);
 expect((await GET(request('chat/conversations/'+ID,'GET','soloestate_session=sess_owner'),context(['chat','conversations',ID]))).status).toBe(401);
 expect((await POST(request('chat/conversations/'+ID+'/send','POST','soloestate_session=sess_owner'),context(['chat','conversations',ID,'send']))).status).toBe(401);
 expect(fetch).not.toHaveBeenCalled();
 const response=await GET(request('chat/conversations/'+ID,'GET'),context(['chat','conversations',ID]));
 expect(response.status).toBe(200);
 expect(fetch.mock.calls[0][1]).toMatchObject({headers:{Authorization:'Bearer '+TOKEN}});
});
it('rejects cross-origin chat writes before reaching FastAPI',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 expect((await POST(request('chat/conversations/'+ID+'/send','POST','soloestate_visitor='+TOKEN,'https://other.invalid'),context(['chat','conversations',ID,'send']))).status).toBe(403);
 expect(fetch).not.toHaveBeenCalled();
});
it('allows only explicit matches and slots queries',async()=>{
 const fetch=vi.fn(async()=>Response.json({}));vi.stubGlobal('fetch',fetch);
 const matches=context(['chat','conversations',ID,'matches']);const slots=context(['chat','conversations',ID,'slots']);const thread=context(['chat','conversations',ID]);
 expect((await GET(request('chat/conversations/'+ID+'/matches?limit=5','GET'),matches)).status).toBe(200);
 expect((await GET(request('chat/conversations/'+ID+'/matches?sql=DROP','GET'),matches)).status).toBe(400);
 expect((await GET(request('chat/conversations/'+ID+'/slots?since=2034-01-01T00:00:00Z&until=2034-01-08T00:00:00Z','GET'),slots)).status).toBe(200);
 expect((await GET(request('chat/conversations/'+ID+'/slots?debug=1','GET'),slots)).status).toBe(400);
 expect((await GET(request('chat/conversations/'+ID+'?debug=1','GET'),thread)).status).toBe(400);
 expect(fetch).toHaveBeenCalledTimes(2);
});
