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

test('D2 outbox: unknown rows resolve, resend with duplicate warning, dead rows requeue',async({page})=>{
 test.setTimeout(600_000);
 const data=JSON.parse(python('outbox_fixture','plan')) as {prefix:string;senders:string[];lead_ids:string[];link_ids:string[];convo_ids:string[];message_ids:string[];outbox_ids:string[];op_keys:string[]};
 const errors:string[]=[];page.on('pageerror',err=>errors.push(err.message));
 try{
  await signIn(page);
  await page.goto('/outbox');
  await page.getByLabel('Status filter').selectOption('unknown');
  const firstShort=data.outbox_ids[0].slice(0,8);
  const secondShort=data.outbox_ids[1].slice(0,8);
  await expect(page.getByRole('button',{name:new RegExp(firstShort)})).toBeVisible();
  await page.getByRole('button',{name:new RegExp(firstShort)}).click();
  await expect(page.getByText('OUTBOX · UNKNOWN')).toBeVisible();
  await expect(page.getByText('Got it. Let me look into that for you.')).toBeVisible();
  await page.getByLabel('Resolution').selectOption('mark_resolved_sent');
  await expect(page.getByRole('button',{name:'Record resolution',exact:true})).toBeDisabled();
  await page.getByLabel('Reason').fill('Fictional e2e confirm delivery');
  await page.getByLabel('Delivery evidence').fill('Fictional e2e provider dashboard shows delivery');
  const sent=page.waitForResponse(r=>r.url().endsWith(`/api/owner/crm/outbox/${data.outbox_ids[0]}/resolve`)&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Record resolution',exact:true}).click();
  expect(((await (await sent).json()).record.status)).toBe('sent');
  await expect(page.getByRole('button',{name:new RegExp(firstShort)})).toHaveCount(0);
  await page.getByRole('button',{name:new RegExp(secondShort)}).click();
  await page.getByLabel('Reason').fill('Fictional e2e resend');
  const resent=page.waitForResponse(r=>r.url().endsWith(`/api/owner/crm/outbox/${data.outbox_ids[1]}/resolve`)&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Record resolution',exact:true}).click();
  expect(((await (await resent).json()).record.status)).toBe('staged');
  await expect(page.getByText('The original attempt may have submitted')).toBeVisible();
  await page.getByLabel('Status filter').selectOption('dead');
  const thirdShort=data.outbox_ids[2].slice(0,8);
  await expect(page.getByRole('button',{name:new RegExp(thirdShort)})).toBeVisible();
  await page.getByRole('button',{name:new RegExp(thirdShort)}).click();
  const requeued=page.waitForResponse(r=>r.url().endsWith(`/api/owner/crm/outbox/${data.outbox_ids[2]}/requeue`)&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Requeue message',exact:true}).click();
  expect(((await (await requeued).json()).record.status)).toBe('staged');
  expect(errors).toEqual([]);
 }finally{
  try{await page.request.delete('/api/session',{headers:{origin},timeout:10000}).catch(()=>undefined);}finally{const cleaned=JSON.parse(python('outbox_fixture','cleanup',JSON.stringify(data))) as {conflicts?:string[]};expect(cleaned.conflicts??[]).toEqual([]);}
 }
});
