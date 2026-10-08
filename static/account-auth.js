'use strict';

const config=window.ACCOUNT_AUTH_CONFIG||{};
const form=document.getElementById('account-form');
const submit=document.getElementById('submit');
const errorEl=document.getElementById('error');

function fallbackHash(value){
  const seeds=[0x811c9dc5,0x9e3779b9,0x85ebca6b,0xc2b2ae35,0x27d4eb2f,0x165667b1,0xd3a2646c,0xfd7046c5];
  return seeds.map(seed=>{let h=seed>>>0;for(let i=0;i<value.length;i++){h^=value.charCodeAt(i);h=Math.imul(h,16777619)>>>0;}return h.toString(16).padStart(8,'0');}).join('');
}

async function browserFingerprint(){
  let installId='';
  try{
    installId=localStorage.getItem('autocat_install_id_v1')||'';
    if(!/^[0-9a-f]{32}$/.test(installId)){
      const bytes=new Uint8Array(16);crypto.getRandomValues(bytes);
      installId=[...bytes].map(v=>v.toString(16).padStart(2,'0')).join('');
      localStorage.setItem('autocat_install_id_v1',installId);
    }
  }catch{installId='storage-unavailable';}
  let canvas='';
  try{const c=document.createElement('canvas');c.width=220;c.height=45;const x=c.getContext('2d');x.font='15px Arial';x.fillStyle='#38bdf8';x.fillText('AUTOCAT JP ACCOUNT',4,20);canvas=c.toDataURL();}catch{}
  const signals=[installId,navigator.userAgent,navigator.platform,navigator.language,(navigator.languages||[]).join(','),navigator.hardwareConcurrency||0,navigator.deviceMemory||0,navigator.maxTouchPoints||0,screen.width||0,screen.height||0,screen.colorDepth||0,Intl.DateTimeFormat().resolvedOptions().timeZone||'',canvas].join('||');
  if(crypto.subtle&&window.TextEncoder){const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(signals));return [...new Uint8Array(digest)].map(v=>v.toString(16).padStart(2,'0')).join('');}
  return fallbackHash(signals);
}

const remember=document.getElementById('remember');
try { if(localStorage.getItem('catps_remember_login')==='0') remember.checked=false; } catch {}
let submitting=false;
form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(submitting)return;
  errorEl.textContent='';
  const password=document.getElementById('password').value;
  if(config.mode==='register'&&password!==document.getElementById('password-confirm').value){errorEl.textContent='確認用パスワードが一致しません。';return;}
  submitting=true;submit.disabled=true;submit.textContent=config.mode==='register'?'作成しています...':'確認しています...';
  try {
    document.getElementById('fingerprint').value=await browserFingerprint();
    try { localStorage.setItem('catps_remember_login',remember.checked?'1':'0'); } catch {}
    // A normal POST and redirect lets the browser recognize a successful login.
    // Passwords are never written to localStorage, cookies, or application logs.
    HTMLFormElement.prototype.submit.call(form);
  } catch(error) {
    submitting=false;errorEl.textContent=error.message;submit.disabled=false;
    submit.textContent=config.mode==='register'?'アカウントを作成':'ログイン';
  }
});
window.addEventListener('pageshow',()=>{submitting=false;submit.disabled=false;submit.textContent=config.mode==='register'?'アカウントを作成':'ログイン';});
