import {test,expect,Page} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {resolve} from 'node:path';
import {mkdirSync} from 'node:fs';

test.use({timezoneId:'Asia/Kolkata'});
const backend=resolve(process.cwd(),'../backend');
const origin=process.env.E2E_BASE_URL ?? 'http://127.0.0.1:3000';
function python(script:string,...args:string[]){return execFileSync(resolve(backend,'.venv/Scripts/python.exe'),['-c',`import runpy; runpy.run_path('../frontend/e2e/${script}.py',run_name='__main__')`,...args],{cwd:backend,encoding:'utf-8',windowsHide:true});}
async function signIn(page:Page){
 const value=JSON.parse(execFileSync(resolve(backend,'.venv/Scripts/python.exe'),['-m','scripts.dashboard_login'],{cwd:backend,encoding:'utf-8',windowsHide:true})) as {url:string};
 const url=new URL(value.url),target=new URL(origin);url.host=target.host;url.protocol=target.protocol;
 await page.goto(url.toString());await expect(page.getByRole('heading',{name:'Your real estate workspace'})).toBeVisible();
}

test('owner CRM creates, edits, searches, books, reschedules, cancels and archives real fictional records',async({page})=>{
 test.setTimeout(600_000);
 const data=JSON.parse(python('crm_fixture','plan')) as {prefix:string;email:string;starts:string[];start_local:string[];end_local:string[];slot_ids:string[];faq_ids:string[]};
 data.slot_ids=[];data.faq_ids=[];
 async function createSlot(index:number){
  await page.getByLabel('Slot start (local time)').fill(data.start_local[index]);await page.getByLabel('Slot end (local time)').fill(data.end_local[index]);
  const pending=page.waitForResponse(r=>r.url().endsWith('/api/owner/crm/visit-slots')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Create slot',exact:true}).click();const response=await pending;
  expect(response.ok()).toBe(true);data.slot_ids.push((await response.json()).record.id);
  await expect(page.getByRole('combobox',{name:'Visit slot',exact:true})).not.toHaveValue('');
 }
 const errors:string[]=[];page.on('pageerror',err=>errors.push(err.message));
 try{
  await signIn(page);
  await page.goto('/leads/new');await page.getByLabel('Name',{exact:true}).fill(data.prefix);await page.getByLabel('Email',{exact:true}).fill(data.email);await page.getByRole('button',{name:'Save record',exact:true}).click();
  await expect(page).toHaveURL(/\/leads\/[0-9a-f-]{36}$/);const leadUrl=page.url(),leadId=leadUrl.split('/').at(-1)!;
  await expect(page.getByText('Qualification is incomplete.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Check property matches',exact:true}).click();await expect(page.getByText(/^Matching is incomplete\./)).toBeVisible();
  await page.getByRole('button',{name:'Edit record',exact:true}).click();
  await page.getByLabel('Preferred localities (comma separated)').fill(data.prefix);await page.getByLabel('Minimum budget (INR)').fill('100');await page.getByLabel('Maximum budget (INR)').fill('200');await page.getByLabel('Bedrooms',{exact:true}).fill('2');
  await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByRole('heading',{name:'Edit lead',exact:true})).toHaveCount(0);
  await expect(page.getByText('Missing answers: purchase timeframe.',{exact:true})).toBeVisible();
  // B1 persists owner-managed states without changing the CRM status or sending anything.
  await page.getByLabel('journey stage',{exact:true}).selectOption('qualifying');
  await page.getByLabel('contact permission',{exact:true}).selectOption('opted_out');
  await page.getByLabel('handoff state',{exact:true}).selectOption('requested');
  await page.getByLabel('Reason / customer permission evidence').fill('Fictional B1 opt-out and handoff');
  await page.getByRole('button',{name:'Save journey controls',exact:true}).click();
  await expect(page.getByText('Fictional B1 opt-out and handoff',{exact:false})).toBeVisible();
  await page.reload();await expect(page.getByLabel('contact permission',{exact:true})).toHaveValue('opted_out');
  await page.getByLabel('contact permission',{exact:true}).selectOption('opted_in');
  await page.getByLabel('handoff state',{exact:true}).selectOption('resolved');
  await page.getByLabel('Reason / customer permission evidence').fill('Fictional explicit permission and human resolution');
  await page.getByRole('button',{name:'Save journey controls',exact:true}).click();
  await expect(page.getByText('3 recorded changes. Earlier activity is not backfilled.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Notes',{exact:true}).fill('Owner edited fictional lead');await page.getByLabel('Lead status').selectOption('qualified');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByRole('heading',{name:'Edit lead',exact:true})).toHaveCount(0);await expect(page.getByText('Owner edited fictional lead',{exact:true})).toBeVisible();console.log('CRM: lead saved and edited');
  await page.goto('/leads');await page.getByLabel('Search',{exact:true}).fill(data.prefix);await page.getByRole('combobox',{name:'Filter',exact:true}).selectOption('qualified');await expect(page.locator('tbody tr')).toHaveCount(1);await expect(page.locator('tbody')).toContainText(data.email);
  mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/crm-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.screenshot({path:'artifacts/crm-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});
  await page.goto('/properties/new');await page.getByLabel('Title',{exact:true}).fill(data.prefix);await page.getByLabel('Locality',{exact:true}).fill(data.prefix);await page.getByLabel('Price (INR)').fill('150');await page.getByLabel('Bedrooms',{exact:true}).fill('2');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page).toHaveURL(/\/properties\/[0-9a-f-]{36}$/);const propertyUrl=page.url(),propertyId=propertyUrl.split('/').at(-1)!;
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Description',{exact:true}).fill('Owner edited fictional property');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByRole('heading',{name:'Edit property',exact:true})).toHaveCount(0);await expect(page.getByText('Owner edited fictional property',{exact:true})).toBeVisible();console.log('CRM: property saved and edited');
  // B2 uses the same live CRM records, then invalidates and renews explicit owner review.
  await page.goto(leadUrl);await page.getByRole('button',{name:'Check property matches',exact:true}).click();await expect(page.getByRole('link',{name:data.prefix,exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Purchase timeframe').selectOption('within_3_months');await page.getByRole('button',{name:'Save record',exact:true}).click();
  await expect(page.getByText('Requirements complete; owner confirmation required.',{exact:true})).toBeVisible();
  await page.getByLabel('Qualification review reason',{exact:true}).fill('Fictional B2 owner review');await page.getByRole('button',{name:'Confirm qualification',exact:true}).click();await expect(page.getByText('Qualification is current.',{exact:true})).toBeVisible();
  await page.reload();await expect(page.getByText('Qualification is current.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Bedrooms',{exact:true}).fill('');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByText('Qualification needs owner review.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Check property matches',exact:true}).click();await expect(page.getByText('Matching is incomplete. Review: bedrooms.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Bedrooms',{exact:true}).fill('2');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByRole('heading',{name:'Edit lead',exact:true})).toHaveCount(0);
  await page.getByLabel('Qualification review reason',{exact:true}).fill('Fictional B2 renewed review');await page.getByRole('button',{name:'Confirm qualification',exact:true}).click();await expect(page.getByText('Qualification is current.',{exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Check property matches',exact:true}).click();await expect(page.getByRole('link',{name:data.prefix,exact:true})).toBeVisible();
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.setViewportSize({width:1440,height:1000});
  console.log('B2: partial answers, matching, explicit review and requalification persisted');
 // B3 authors versioned FAQs and simulates a lead-linked conversation.
 await page.goto('/faqs');await page.getByLabel('FAQ question',{exact:true}).fill(data.prefix+' buyer fee policy');await page.getByLabel('FAQ answer',{exact:true}).fill('No buyer fee applies for '+data.prefix+' listings.');await page.getByRole('button',{name:'Save FAQ',exact:true}).click();await expect(page.getByRole('link',{name:data.prefix+' buyer fee policy · business · v1 · active',exact:true})).toBeVisible();
 await page.getByRole('link',{name:data.prefix+' buyer fee policy · business · v1 · active',exact:true}).click();await expect(page).toHaveURL(/\/faqs\/[0-9a-f-]{36}$/);const faqId=page.url().split('/').at(-1)!;data.faq_ids.push(faqId);
 await expect(page.getByText(/v1 · current/)).toBeVisible();
 await page.getByLabel('FAQ answer',{exact:true}).fill('No buyer fee applies for '+data.prefix+' listings. Updated.');await page.getByRole('button',{name:'Save new version',exact:true}).click();await expect(page.getByText(/v2 · current/)).toBeVisible();
 await page.goto(leadUrl);await page.getByRole('button',{name:'Open conversation',exact:true}).click();await expect(page.getByText('Status: open · Handoff: none',{exact:true})).toBeVisible();
 await page.getByLabel('Simulated customer message',{exact:true}).fill('Is there a buyer fee on this listing?');await page.getByRole('button',{name:'Send simulated message',exact:true}).click();await expect(page.getByText('Is there a buyer fee on this listing?')).toBeVisible();
 await page.getByLabel('Handoff reason',{exact:true}).fill('Fictional B3 handoff');await page.getByRole('button',{name:'Request handoff',exact:true}).click();await expect(page.getByText('Drafting blocked: handoff requested.')).toBeVisible();await expect(page.getByRole('button',{name:'Draft answer',exact:true})).toBeDisabled();
 await page.getByLabel('Handoff reason',{exact:true}).fill('Fictional B3 resolution');await page.getByRole('button',{name:'Resolve handoff',exact:true}).click();await expect(page.getByText('Status: open · Handoff: resolved',{exact:true})).toBeVisible();
 await page.getByLabel('Handoff reason',{exact:true}).fill('Fictional B3 close');await page.getByRole('button',{name:'Resolve conversation',exact:true}).click();await expect(page.getByText('Status: resolved · Handoff: resolved',{exact:true})).toBeVisible();
 console.log('B3: versioned FAQs and simulated conversation persisted');
 // B4 manages an internal follow-up task lifecycle without delivering anything.
 await page.getByLabel('Task title',{exact:true}).fill(data.prefix+' rate sheet call');
 await page.getByLabel('Due date (UTC)',{exact:true}).fill('2034-06-01');
 await page.getByRole('button',{name:'Create task',exact:true}).click();
 await expect(page.getByRole('button',{name:/open · due 2034-06-01/})).toBeVisible();
 await expect(page.getByText('Status: open',{exact:true})).toBeVisible();
 await page.getByLabel('Change reason',{exact:true}).fill('Fictional B4 start');
 await page.getByRole('button',{name:'Move to in progress',exact:true}).click();
 await expect(page.getByText('Status: in_progress',{exact:true})).toBeVisible();
 await page.getByLabel('Change reason',{exact:true}).fill('Fictional B4 review');
 await page.getByRole('button',{name:'Move to in review',exact:true}).click();
 await expect(page.getByText('Status: in_review',{exact:true})).toBeVisible();
 await page.getByLabel('Completion outcome',{exact:true}).fill('Fictional B4 visit set');
 await page.getByLabel('Change reason',{exact:true}).fill('Fictional B4 completion');
 await page.getByRole('button',{name:'Move to done',exact:true}).click();
 await expect(page.getByText('Status: done · Outcome: Fictional B4 visit set',{exact:true})).toBeVisible();
 console.log('B4: follow-up task lifecycle persisted');
  await page.goto('/appointments/new?lead_id='+leadId);await page.getByRole('combobox',{name:'Property',exact:true}).selectOption(propertyId);
  await createSlot(0);
  await page.getByRole('button',{name:'Prepare owner review',exact:true}).click();await expect(page.getByRole('heading',{name:'Review appointment request'})).toBeVisible();
  // Preparing consent is not a booking.
  const before=await (await page.request.get('/api/owner/crm/appointments?lead_id='+leadId)).json();expect(before.total).toBe(0);
  await page.getByRole('button',{name:'Approve and book',exact:true}).click();await expect(page).toHaveURL(/\/appointments\/[0-9a-f-]{36}$/);const oldUrl=page.url(),oldId=oldUrl.split('/').at(-1)!;
  await expect(page.getByRole('link',{name:'Reschedule visit',exact:true})).toBeVisible();await page.getByRole('link',{name:'Reschedule visit',exact:true}).click();
  await createSlot(1);
  await page.getByRole('button',{name:'Prepare owner review',exact:true}).click();await page.getByRole('button',{name:'Approve and reschedule',exact:true}).click();await expect(page).toHaveURL(/\/appointments\/[0-9a-f-]{36}$/);const newId=page.url().split('/').at(-1)!;expect(newId).not.toBe(oldId);
  const original=await (await page.request.get('/api/owner/crm/appointments/'+oldId)).json();expect(original.record.status).toBe('cancelled');
  const replacement=await (await page.request.get('/api/owner/crm/appointments/'+newId)).json();expect(replacement.record.replaces_booking_id).toBe(oldId);expect(replacement.record.origin).toBe('owner');
  await page.getByLabel('Cancellation reason').fill('Fictional owner cancellation');await page.getByRole('button',{name:'Confirm cancellation',exact:true}).click();await expect(page.getByRole('link',{name:'Reschedule visit',exact:true})).toHaveCount(0);
  const cancelled=await (await page.request.get('/api/owner/crm/appointments/'+newId)).json();expect(cancelled.record.status).toBe('cancelled');
  await page.goto(propertyUrl);await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Inventory state').selectOption('false');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByText('Inactive',{exact:true})).toBeVisible();
  await page.goto(leadUrl);await page.getByRole('button',{name:'Archive lead',exact:true}).click();await expect(page.getByRole('button',{name:'Restore lead',exact:true})).toBeVisible();
  await page.goto('/leads');await page.getByLabel('Search',{exact:true}).fill(data.prefix);await expect(page.getByText('No matching records.',{exact:true})).toBeVisible();await page.getByLabel('Archived only').check();await expect(page.locator('tbody')).toContainText(data.prefix);
  expect(errors).toEqual([]);
 }finally{
  try{await page.request.delete('/api/session',{headers:{origin},timeout:10000}).catch(()=>undefined);}finally{python('crm_fixture','cleanup',JSON.stringify(data));}
 }
});
