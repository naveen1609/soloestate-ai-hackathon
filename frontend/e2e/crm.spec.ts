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
 test.setTimeout(420_000);
 const data=JSON.parse(python('crm_fixture','plan')) as {prefix:string;email:string;starts:string[];start_local:string[];end_local:string[];slot_ids:string[]};
 data.slot_ids=[];
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
  await page.goto('/leads/new');await page.getByLabel('Name',{exact:true}).fill(data.prefix);await page.getByLabel('Email',{exact:true}).fill(data.email);await page.getByLabel('Preferred localities (comma separated)').fill(data.prefix);await page.getByLabel('Minimum budget (INR)').fill('100');await page.getByLabel('Maximum budget (INR)').fill('200');await page.getByLabel('Bedrooms',{exact:true}).fill('2');await page.getByRole('button',{name:'Save record',exact:true}).click();
  await expect(page).toHaveURL(/\/leads\/[0-9a-f-]{36}$/);const leadUrl=page.url(),leadId=leadUrl.split('/').at(-1)!;
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Notes',{exact:true}).fill('Owner edited fictional lead');await page.getByLabel('Lead status').selectOption('qualified');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByRole('heading',{name:'Edit lead',exact:true})).toHaveCount(0);await expect(page.getByText('Owner edited fictional lead',{exact:true})).toBeVisible();console.log('CRM: lead saved and edited');
  await page.goto('/leads');await page.getByLabel('Search',{exact:true}).fill(data.prefix);await page.getByRole('combobox',{name:'Filter',exact:true}).selectOption('qualified');await expect(page.locator('tbody tr')).toHaveCount(1);await expect(page.locator('tbody')).toContainText(data.email);
  mkdirSync('artifacts',{recursive:true});await page.screenshot({path:'artifacts/crm-desktop.png',fullPage:true});
  await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);await page.screenshot({path:'artifacts/crm-mobile.png',fullPage:true});await page.setViewportSize({width:1440,height:1000});
  await page.goto('/properties/new');await page.getByLabel('Title',{exact:true}).fill(data.prefix);await page.getByLabel('Locality',{exact:true}).fill(data.prefix);await page.getByLabel('Price (INR)').fill('150');await page.getByLabel('Bedrooms',{exact:true}).fill('2');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page).toHaveURL(/\/properties\/[0-9a-f-]{36}$/);const propertyUrl=page.url(),propertyId=propertyUrl.split('/').at(-1)!;
  await page.getByRole('button',{name:'Edit record',exact:true}).click();await page.getByLabel('Description',{exact:true}).fill('Owner edited fictional property');await page.getByRole('button',{name:'Save record',exact:true}).click();await expect(page.getByRole('heading',{name:'Edit property',exact:true})).toHaveCount(0);await expect(page.getByText('Owner edited fictional property',{exact:true})).toBeVisible();console.log('CRM: property saved and edited');
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
