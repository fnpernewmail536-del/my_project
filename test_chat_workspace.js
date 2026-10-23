'use strict';
// Run with NODE_PATH pointing to jsdom. No game/network requests are sent.
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const crypto=require('node:crypto');
const html=fs.readFileSync('local_chat/static/index.html','utf8');
const js=fs.readFileSync('local_chat/static/app.js','utf8');
const theme=fs.readFileSync('local_chat/static/theme.js','utf8');
const settle=()=>new Promise(resolve=>setImmediate(resolve));

async function run(){
 const dom=new JSDOM(html,{url:'https://example.test/chat/',runScripts:'outside-only'});
 const w=dom.window,doc=w.document,requests=[];
 w.setInterval=()=>0;w.setTimeout=()=>0;
 Object.defineProperty(w.crypto,'randomUUID',{value:crypto.randomUUID});
 let settings={mode:'daiko',account_type:'new',count:1},creds={transfer_code:false,auth_code:false};
 const draft=()=>({kind:'draft',settings,credentials:creds,labels:[{key:'amount',label:'猫缶45000'}],missing:[],blockers:[],unresolved:[],inferences:[],ready:true,confirmation_id:'a'.repeat(64),message:'確認',interpretation:{review_items:1,needs_review:0,rewrites:0}});
 w.fetch=async(url,opts={})=>{
   const data=opts.body?JSON.parse(opts.body):null;requests.push({url,data});
   let value={};
   if(url.endsWith('/bootstrap'))value={csrf:'test',engine:{ready:true},jobs:[],settings,draft:draft()};
   if(url.endsWith('/settings')){settings=data;creds={transfer_code:false,auth_code:false};value=draft();}
   if(url.endsWith('/credentials')){creds={transfer_code:true,auth_code:true};value=draft();}
   if(url.endsWith('/chat'))value=draft();
   if(url.endsWith('/confirm'))value={kind:'started',job_id:'test-job',operation_id:'op',labels:[],message:'開始'};
   if(url.endsWith('/menu'))value={max_defaults:{character:{base_level:60,plus_level:90},items:{}},items:[]};
   return {ok:true,json:async()=>value};
 };
 const $=id=>doc.getElementById(id);
 w.eval(theme);doc.dispatchEvent(new w.Event('DOMContentLoaded'));
 assert.equal(doc.documentElement.dataset.theme,'white');
 $('themeSelect').value='black';$('themeSelect').dispatchEvent(new w.Event('change'));
 assert.equal(doc.documentElement.dataset.theme,'black');assert.equal(w.localStorage.getItem('cps-chat-theme'),'black');
 w.eval(theme);doc.dispatchEvent(new w.Event('DOMContentLoaded'));
 assert.equal(doc.documentElement.dataset.theme,'black');
 assert.deepEqual([...$('themeSelect').options].map(x=>x.value),['white','black']);
 w.eval(js+'\nwindow.chatTest={confirmDraft,sendText,resultBlock,showPlan};');await settle();
 $('transferCode').value='abcdef123';$('authCode').value='0007';$('transferCode').dispatchEvent(new w.Event('input'));
 assert.equal($('runDraft').hidden,true);
 await w.chatTest.confirmDraft();assert.equal(requests.some(x=>x.url.endsWith('/confirm')),false);
 $('credentialsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
 assert.equal($('transferCode').value,'');assert.equal($('authCode').value,'');
 assert.equal(doc.body.textContent.includes('abcdef123'),false);assert.equal(doc.body.textContent.includes('0007'),false);
 assert.match($('credentialsStatus').textContent,/登録済み/);
 $('requestMode').value='create';$('requestMode').dispatchEvent(new w.Event('change'));
 assert.equal($('creationFields').hidden,false);assert.equal($('credentialsCard').hidden,true);
 assert.equal($('runDraft').hidden,true);
 await w.chatTest.sendText('猫缶1');assert.equal(requests.some(x=>x.url.endsWith('/chat')),false);
 $('accountType').value='beginner';$('createCount').value='2';
 $('settingsForm').dispatchEvent(new w.Event('submit',{cancelable:true}));await settle();
 assert.deepEqual(settings,{mode:'create',account_type:'beginner',count:2});
 assert.equal($('runDraft').hidden,false);
 await w.chatTest.confirmDraft();assert.equal(requests.filter(x=>x.url.endsWith('/confirm')).length,1);
 const result=w.chatTest.resultBlock({status:'done',accounts:[{tc:'test-one',ac:'0001'},{tc:'test-two',ac:'0002'}],operation_id:'op'});
 assert.match(result.textContent,/test-one/);assert.match(result.textContent,/test-two/);assert.match(result.textContent,/アカウント 2/);
 const partial=w.chatTest.resultBlock({status:'error',accounts:[{tc:'test-one',ac:'0001'}],operation_id:'op'});
 assert.match(partial.textContent,/作成済み/);assert.match(partial.textContent,/test-one/);
 w.chatTest.showPlan({...draft(),ready:false,unresolved:[{input:'キャツアイ120',suggestions:['キャッツアイ：120']}]});
 assert.match($('plan').textContent,/候補：キャッツアイ：120/);
 console.log('Workspace checks passed: theme persistence, credentials, stale review, modes, create count, all results, suggestions.');
 dom.window.close();
}
run().catch(e=>{console.error(e);process.exitCode=1;});
