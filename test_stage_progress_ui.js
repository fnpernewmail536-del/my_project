'use strict';
const assert=require('node:assert/strict');
const {load,settle}=require('./test_legend_crowns_ui.js');
const map={id:0,name:'fixture',crowns:4,stages:['one','two','three']};
const meta=Object.fromEntries(['legend','true_legend','zero_legend'].map(k=>[k,{label:k,maps:[map]}]));
for(const k of ['main_story','zombie'])meta[k]={label:k,chapters:Array.from({length:9},(_,id)=>({id,name:`chapter-${id}`,stages:Array.from({length:48},(_,i)=>`stage-${i}`)}))};
for(const k of ['aku','aku_ex'])meta[k]={label:k,chapters:[{id:0,name:k,stages:k==='aku'?['a','b','c']:['boss']}]};
const families=['event','collab','dojo_ranking','tower','catamin','legend_quest','gauntlets','enigma','collab_gauntlets','behemoth','labyrinth','colosseum','catclaw'];
meta.event_stage_families=Object.fromEntries(families.map(k=>[k,{label:k,maps:[{...map,crowns:k==='labyrinth'?1:4}]}]));
Object.assign(meta,{vip_item_groups:{},facilities:[],talent_orbs:[],event_tickets:[]});
const plain=x=>JSON.parse(JSON.stringify(x));
async function flush(){for(let i=0;i<10;i++)await settle();}
function set(w,id,value){const el=w.document.getElementById(id);assert.ok(el,id);assert.equal(el.disabled,false,id);el.value=String(value);el.dispatchEvent(new w.Event('change'));return el;}

async function progress(page){
 const dom=await load(page,meta),w=dom.window,t=w.nativeTest;
 for(const tab of ['daiko','create']){
  t.switchTab(tab);const details=w.document.getElementById(`stage-progress-${tab}`);details.open=true;details.dispatchEvent(new w.Event('toggle'));await flush();
  const prefix=`stage-progress-${tab}-`;
  set(w,prefix+'category','main_story-2');set(w,prefix+'map','6');set(w,prefix+'mode','prefix');set(w,prefix+'count',23);
  const add=[...details.querySelectorAll('button')].find(b=>b.textContent==='この進行指定を追加');add.click();
  assert.deepEqual(plain(t.collectPayload(tab).stage_progress),[{family:'main_story',map:6,star:0,progress:23}]);
  set(w,prefix+'category','main_story-0');set(w,prefix+'mode','unclear');add.click();
  assert.equal(t.collectPayload(tab).stage_progress.filter(r=>r.family==='main_story'&&r.progress===0).length,3);
  set(w,prefix+'category','event:event');set(w,prefix+'mode','clear');set(w,prefix+'crowns',1);add.click();
  assert.deepEqual(plain(t.collectPayload(tab).stage_progress.filter(r=>r.family==='event:event').map(r=>r.progress)),[3,0,0,0]);
  set(w,prefix+'mode','prefix');assert.equal(w.document.getElementById(prefix+'crowns').value,'1');set(w,prefix+'count',2);add.click();
  assert.deepEqual(plain(t.collectPayload(tab).stage_progress.filter(r=>r.family==='event:event').map(r=>r.progress)),[2,0,0,0]);
  for(const family of families)assert.ok([...w.document.getElementById(prefix+'category').options].some(o=>o.value==='event:'+family),family);
  assert.match(details.textContent,/23\/48/);
  const before=plain(t.collectPayload(tab).stage_progress);
  let snapshot;
  if(page==='index'){snapshot=t.captureFreePreset(tab);w.localStorage.setItem('autocat_presets_free_v1',JSON.stringify(snapshot));}
  else snapshot=t.capturePreset(tab);
  t.resetAll(tab);assert.deepEqual(plain(t.collectPayload(tab).stage_progress),[]);
  if(page==='index'){
   // Native free-preset target is chosen by its actual dialog entry point.
   w.eval(`openFreePreset('${tab}')`);t.loadFreePresetIntoCurrentTab();
  }else t.restorePreset(tab,snapshot);
  await flush();assert.deepEqual(plain(t.collectPayload(tab).stage_progress),before);
 }
 assert.equal(t.collectPayload('daiko').stage_progress[0].progress,23);
 dom.window.close();
}

async function copyCodes(page){
 const dom=await load(page,meta,true),w=dom.window,t=w.nativeTest,doc=w.document;
 const copied=[];Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async text=>{copied.push(text);}},configurable:true});
 const jobs={daiko:{status:'done',transfer_code:'abc123def',auth_code:'0007'},
             create:{status:'done',accounts:[{tc:'aaa111bbb',ac:'0012'},{tc:'ccc222ddd',ac:'0034'}]},
             clone:{status:'done',orig_transfer_code:'eee333fff',orig_auth_code:'0056',copies:[{tc:'123456abc',ac:'0078'},{tc:'234567abc',ac:'0090'}]}};
 for(const tab of ['daiko','create','clone']){
  const intervals=[];w.setInterval=fn=>{intervals.push(fn);return intervals.length;};
  const oldFetch=w.fetch;w.fetch=async(url,options)=>url.includes('/api/job/')?{ok:true,json:async()=>jobs[tab]}:oldFetch(url,options);
  await t.startJob(tab,'/unused',{}, {},'fake-job','fake-token');
  await intervals[0]();await flush();
  const box=doc.getElementById(`result-${tab}`),buttons=[...box.querySelectorAll('.code-pair-copy button')];
  assert.equal(buttons.length,tab==='daiko'?1:tab==='create'?2:3);
  for(const button of buttons){button.click();await flush();assert.match(copied.at(-1),/^引継ぎコード： [a-f\d]{9}\n認証番号： \d{4}$/);}
  w.fetch=oldFetch;
 }
 assert.ok(copied.includes('引継ぎコード： abc123def\n認証番号： 0007'));
 for(const tab of ['daiko','create','clone']){t.setHistoryTab(tab);await flush();assert.ok(doc.querySelector('#history-list .code-pair-copy button'));}
 const textarea=doc.querySelector('#history-list .code-pair-copy textarea'),button=textarea.nextElementSibling;
 Object.defineProperty(w.navigator,'clipboard',{value:{writeText:async()=>{throw Error('blocked');}},configurable:true});
 doc.execCommand=()=>true;button.click();await flush();assert.equal(button.textContent,'コピーしました');
 doc.execCommand=()=>false;button.click();await flush();assert.equal(button.textContent,'選択済み・手動でコピー');
 assert.equal(textarea.selectionEnd-textarea.selectionStart,textarea.value.length);
 dom.window.close();
}
async function run(){for(const page of ['index','vip']){await progress(page);await copyCodes(page);}console.log('Stage progress and paired copy passed: both native pages, both job modes, all event groups, presets, results, multi-account copies, history and clipboard fallback.');}
run().catch(error=>{console.error(error);process.exitCode=1;});
