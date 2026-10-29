// @vitest-environment jsdom
import {beforeEach,it,expect,vi} from 'vitest';
import {render,screen,waitFor} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import FaqManager from './faq-manager';
const ID='80000000-0000-4000-8000-000000000001';
const faq={id:ID,scope:'business',property_id:null,status:'active',current_version:1,version:1,question:'Do you charge a fee?',answer:'No fee for buyers.'};
beforeEach(()=>{sessionStorage.clear();vi.unstubAllGlobals();});
function stub(routes:Record<string,unknown>){
 return vi.fn<(input:unknown,init?:RequestInit)=>Promise<Response>>(async(input)=>{const url=String(input);
  if(url==='/api/session')return Response.json({authenticated:true});
  for(const [key,value] of Object.entries(routes))if(url.includes(key))return Response.json(value);
  return Response.json({items:[],total:0,page:1,page_size:20});});
}

it('lists FAQs with scope and version',async()=>{
 vi.stubGlobal('fetch',stub({'/api/owner/crm/faqs?':{items:[faq],total:1,page:1,page_size:20},'/api/owner/crm/properties':{items:[],total:0,page:1,page_size:100}}));
 render(<FaqManager/>);
 // Budget covers owner-session init plus the 250ms list debounce under parallel workers; same role/name/href assertion.
 const link=await screen.findByRole('link',{name:'Do you charge a fee? · business · v1 · active'},{timeout:4000});
 expect(link).toHaveAttribute('href','/faqs/'+ID);
});

it('creates a business FAQ with a null property',async()=>{
 const user=userEvent.setup();
 const fetch=stub({'/api/owner/crm/faqs?':{items:[],total:0,page:1,page_size:20},'/api/owner/crm/properties':{items:[],total:0,page:1,page_size:100},'/api/owner/crm/faqs':{record:faq}});
 vi.stubGlobal('fetch',fetch);
 render(<FaqManager/>);
 await user.type(await screen.findByLabelText('FAQ question'),'Do you charge a fee?');
 await user.type(screen.getByLabelText('FAQ answer'),'No fee for buyers.');
 await user.click(screen.getByRole('button',{name:'Save FAQ'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({scope:'business',property_id:null,question:'Do you charge a fee?'});
});

it('requires a property for property scope',async()=>{
 const user=userEvent.setup();
 const prop={id:ID,title:'Fictional apartment'};
 vi.stubGlobal('fetch',stub({'/api/owner/crm/faqs?':{items:[],total:0,page:1,page_size:20},'/api/owner/crm/properties':{items:[prop],total:1,page:1,page_size:100}}));
 render(<FaqManager/>);
 await user.selectOptions(await screen.findByLabelText('FAQ scope'),'property');
 expect(screen.getByRole('button',{name:'Save FAQ'})).toBeDisabled();
 await user.selectOptions(screen.getByLabelText('FAQ property'),ID);
 expect(screen.getByRole('button',{name:'Save FAQ'})).toBeEnabled();
});

it('shows version history and archives with the committed version',async()=>{
 const user=userEvent.setup();
 const versions=[{faq_id:ID,version:1,question:'Old?',answer:'Old.',created_at:'2026-10-29T10:00:00Z'},{faq_id:ID,version:2,question:'New?',answer:'New.',created_at:'2026-10-29T11:00:00Z'}];
 const fetch=stub({'/api/owner/crm/faqs/':{record:{...faq,current_version:2,version:2,question:'New?',answer:'New.'},versions}});
 vi.stubGlobal('fetch',fetch);
 render(<FaqManager id={ID}/>);
 expect(await screen.findByText(/v2 · current/)).toBeVisible();
 await user.click(screen.getByRole('button',{name:'Archive FAQ'}));
 await waitFor(()=>expect(fetch.mock.calls.some(([,init])=>init?.method==='POST')).toBe(true));
 const post=fetch.mock.calls.find(([,init])=>init?.method==='POST')!;
 expect(String(post[0])).toContain('/archive');
 expect(JSON.parse(post[1]!.body as string)).toMatchObject({expected_version:2,archived:true});
});

it('keeps failed saves visible without claiming success',async()=>{
 const user=userEvent.setup();
 vi.stubGlobal('fetch',vi.fn(async(input:unknown,init?:RequestInit)=>String(input)==='/api/session'
  ?Response.json({authenticated:true})
  :init?.method==='POST'
  ?Response.json({message:'Property FAQs require an active property.'},{status:422})
  :Response.json({items:[],total:0,page:1,page_size:20})));
 render(<FaqManager/>);
 await user.type(await screen.findByLabelText('FAQ question'),'Q?');
 await user.type(screen.getByLabelText('FAQ answer'),'A.');
 await user.click(screen.getByRole('button',{name:'Save FAQ'}));
 expect(await screen.findByRole('alert')).toHaveTextContent('active property');
});
