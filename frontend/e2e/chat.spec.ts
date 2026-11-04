import {test,expect,Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';

test.use({timezoneId:'Asia/Kolkata'});
const backend=resolve(process.cwd(),'../backend');
const origin=process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3000';
function python(script:string,...args:string[]){return execFileSync(resolve(backend,'.venv/Scripts/python.exe'),['-c',`import runpy; runpy.run_path('../frontend/e2e/${script}.py',run_name='__main__')`,...args],{cwd:backend,encoding:'utf-8',windowsHide:true});}
async function signIn(page:Page){
 const value=JSON.parse(execFileSync(resolve(backend,'.venv/Scripts/python.exe'),['-m','scripts.dashboard_login'],{cwd:backend,encoding:'utf-8',windowsHide:true})) as {url:string};
 const url=new URL(value.url),target=new URL(origin);url.host=target.host;url.protocol=target.protocol;
 await page.goto(url.toString());await expect(page.getByRole('heading',{name:'Your real estate workspace'})).toBeVisible();
}
async function sendChat(page:Page,text:string){
 await page.getByLabel('Message',{exact:true}).fill(text);
 await page.getByRole('button',{name:'Send',exact:true}).click();
 for(let attempt=0;attempt<3;attempt++){
  try{await expect(page.getByText(text,{exact:false})).toBeVisible({timeout:120_000});return;}
  catch(_){const retry=page.getByRole('button',{name:'Retry saved message',exact:true});if(await retry.count()){await retry.click();continue;}throw _;}
 }
 throw new Error('chat send did not settle after designed retries');
}

test('visitor website chat captures a lead, answers grounded questions and requests owner follow-up',async({page})=>{
 test.setTimeout(600_000);
 const data=JSON.parse(python('chat_fixture','plan')) as {prefix:string;email:string;starts:string[];start_local:string;end_local:string;slot_ids:string[];faq_ids:string[];property_ids:string[]};
 const errors:string[]=[];page.on('pageerror',err=>errors.push(err.message));
 try{
  await signIn(page);
  await page.goto('/properties/new');await page.getByLabel('Title',{exact:true}).fill(data.prefix);await page.getByLabel('Locality',{exact:true}).fill(data.prefix);await page.getByLabel('Price (INR)').fill('150');await page.getByLabel('Bedrooms',{exact:true}).fill('2');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page).toHaveURL(/\/properties\/[0-9a-f-]{36}$/);data.property_ids.push(page.url().split('/').at(-1)!);
  await page.goto('/faqs');await page.getByLabel('FAQ question',{exact:true}).fill(data.prefix+' buyer fee policy');await page.getByLabel('FAQ answer',{exact:true}).fill('No buyer fee applies for '+data.prefix+' listings.');await page.getByRole('button',{name:'Save FAQ',exact:true}).click();await expect(page.getByRole('link',{name:data.prefix+' buyer fee policy · business · v1 · active',exact:true})).toBeVisible();
  await page.getByRole('link',{name:data.prefix+' buyer fee policy · business · v1 · active',exact:true}).click();await expect(page).toHaveURL(/\/faqs\/[0-9a-f-]{36}$/);data.faq_ids.push(page.url().split('/').at(-1)!);
  const slot=await page.request.post('/api/owner/crm/visit-slots',{headers:{origin},data:{request_key:data.prefix+'-slot',starts_at:data.starts[0],ends_at:data.starts[1]}});expect(slot.ok()).toBe(true);data.slot_ids.push((await slot.json()).record.id);
  await page.goto('/chat');
  await page.getByLabel('Name',{exact:true}).fill(data.prefix);await page.getByLabel('Email',{exact:true}).fill(data.email);await page.getByRole('button',{name:'Start chat',exact:true}).click();
  await expect(page.getByText('Status: open',{exact:false})).toBeVisible();
  await page.getByLabel('Localities',{exact:true}).fill(data.prefix);await page.getByLabel('Minimum budget',{exact:true}).fill('100');await page.getByLabel('Maximum budget',{exact:true}).fill('200');await page.getByLabel('Bedrooms',{exact:true}).fill('2');await page.getByRole('button',{name:'Save requirements',exact:true}).click();
  await expect(page.getByText(new RegExp(data.prefix)).first()).toBeVisible();
  await page.getByRole('button',{name:'Show matching properties',exact:true}).click();await expect(page.locator('li',{hasText:'score'}).first()).toBeVisible();
  const pfaq=await page.request.post('/api/owner/crm/faqs',{headers:{origin},data:{request_key:data.prefix+'-pfaq',scope:'property',property_id:data.property_ids[0],question:'What are the parking terms for '+data.prefix+'?',answer:'Parking is included for '+data.prefix+' homes.'}});expect(pfaq.ok()).toBe(true);data.faq_ids.push((await pfaq.json()).record.id);
  await page.getByRole('button',{name:'Ask about this property',exact:true}).click();await expect(page.getByText(/Focus: [0-9a-f]{8}/)).toBeVisible();
  await sendChat(page,'What are the parking terms for '+data.prefix+'?');
  await expect(page.getByText(/Parking is included|No verified answer is available/).first()).toBeVisible();
  if(await page.getByText('Status: pending_owner',{exact:false}).count()){const lookup=(await (await page.request.get('/api/owner/crm/leads?q='+encodeURIComponent(data.prefix))).json()).items[0].id as string;const thread=await (await page.request.get('/api/owner/crm/conversations?lead_id='+lookup)).json();const ack=await page.request.post('/api/owner/crm/conversations/'+thread.items[0].id+'/review',{headers:{origin},data:{request_key:data.prefix+'-ack',expected_version:thread.items[0].version,action:'acknowledge',reason:'Fictional e2e reopen'}});expect(ack.ok()).toBe(true);await page.getByRole('button',{name:'Refresh',exact:true}).click();await expect(page.getByText('Status: open',{exact:false})).toBeVisible();}
  await page.getByLabel('From',{exact:true}).fill(data.start_local);await page.getByLabel('Until',{exact:true}).fill(data.end_local);await page.getByRole('button',{name:'Show availability',exact:true}).click();await expect(page.getByText('Provisional only')).toBeVisible();
  await sendChat(page,'I would like to visit. Is there a buyer fee on '+data.prefix+' listings?');
  await expect(page.getByText(/FAQ v\d|No verified answer is available/).first()).toBeVisible();
  await page.getByLabel('Handoff reason',{exact:true}).fill('Fictional chat follow-up');await page.getByRole('button',{name:'Request owner review',exact:true}).click();await expect(page.getByText('Follow-up: requested',{exact:false})).toBeVisible();
  const leadId=(await (await page.request.get('/api/owner/crm/leads?q='+encodeURIComponent(data.prefix))).json()).items[0].id as string;
  await page.goto('/leads/'+leadId);await page.getByRole('button',{name:/· requested ·/}).click();await expect(page.getByText('I would like to visit.',{exact:false})).toBeVisible();
  const visits=await (await page.request.get('/api/owner/crm/appointments?lead_id='+leadId)).json();expect(visits.total).toBe(0);
  expect(errors).toEqual([]);
  console.log('C: visitor chat journey with owner follow-up persisted');
 }finally{
  const cleaned=JSON.parse(python('chat_fixture','cleanup',JSON.stringify(data))) as {remaining_leads:number};
  expect(cleaned.remaining_leads).toBe(0);
  await page.request.delete('/api/session',{headers:{origin},data:{}}).catch(()=>null);
 }
});
