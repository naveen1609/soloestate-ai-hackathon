'use client';
import Link from 'next/link';
import { ReactNode,useEffect,useRef,useState } from 'react';
import { api } from '../lib/api';
import { message } from '../lib/crm';

export default function CrmShell({children}:{children:ReactNode}) {
 const [auth,setAuth]=useState<'loading'|'locked'|'owner'>('loading');
 const [error,setError]=useState('');const started=useRef(false);
 useEffect(()=>{
  if(started.current)return;started.current=true;
  async function login(){
   try{
    const url=new URL(window.location.href);const ticket=new URLSearchParams(url.hash.slice(1)).get('ticket');
    if(ticket){url.hash='';window.history.replaceState({},'',url.pathname+url.search);await api('/api/session',{method:'POST',body:JSON.stringify({ticket})});}
    const result=await api('/api/session') as {authenticated?:boolean};
    if(result.authenticated && url.pathname==='/' && url.searchParams.has('run')){window.location.replace('/agent?run='+encodeURIComponent(url.searchParams.get('run')!));return;}
    setAuth(result.authenticated?'owner':'locked');
   }catch(err){setError(message(err));setAuth('locked');}
  }void login();
 },[]);
 async function logout(){try{await api('/api/session',{method:'DELETE'});setAuth('locked');}catch(err){setError(message(err));}}
 return <div className="crm-shell">
  <header className="crm-header"><Link className="crm-brand" href="/">SoloEstate <span>AI</span></Link><nav aria-label="CRM navigation"><Link href="/">Overview</Link><Link href="/leads">Leads</Link><Link href="/properties">Properties</Link><Link href="/appointments">Appointments</Link><Link href="/faqs">FAQs</Link><Link href="/agent">AI execution</Link></nav>{auth==='owner'&&<button className="button button-light" onClick={()=>void logout()}>Sign out</button>}</header>
  <main className="crm-main">{error&&<p className="notice notice-error" role="alert">{error}</p>}{auth==='loading'?<p role="status">Checking owner session…</p>:auth==='locked'?<section className="panel crm-lock"><p className="eyebrow">PRIVATE OWNER WORKSPACE</p><h1>Owner sign-in required</h1><p>From backend, issue a private single-use link:</p><code>.\.venv\Scripts\python.exe -m scripts.dashboard_login</code><p>Open the link within two minutes. Keep the URL private.</p></section>:children}</main>
 </div>;
}
