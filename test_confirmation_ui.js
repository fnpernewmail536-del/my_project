'use strict';
// Exercise the actual chat client offline. This does not test browser layout.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {randomUUID} = require('node:crypto');

class Element {
  constructor(){this.children=[];this.value='';this.textContent='';this.dataset={};this.classList={toggle(){}};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  get firstChild(){return this.children[0];}
  closest(){return this;}
  focus(){}
}
const draft = token => ({kind:'draft',labels:[{key:'catfood',label:'猫缶：10,000'}],
  ready:true,missing:[],blockers:[],unresolved:[],confirmation_id:token,message:'確認してください。'});
const started = {kind:'started',job_id:'offline-job',labels:[],message:'開始しました。'};
const source=fs.readFileSync(path.join(__dirname,'local_chat/static/app.js'),'utf8');

async function app(responder, initial=draft('a'.repeat(64))){
  const nodes=new Map(),requests=[];
  const node=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const context=vm.createContext({
    document:{getElementById:node,createElement:()=>new Element(),createTextNode:text=>({textContent:text}),querySelectorAll:()=>[]},
    crypto:{randomUUID},setInterval(){},setTimeout(){},navigator:{},console,
    fetch:async(url, options={})=>{
      url=url.replace(/^\/chat(?=\/)/,'');
      const request={url,body:options.body?JSON.parse(options.body):null};requests.push(request);
      const result=await responder(request);
      if(result instanceof Error)throw result;
      const payload=result?.payload??(url==='/api/bootstrap'?{csrf:'csrf',engine:{ready:true},jobs:[],draft:initial}:
        url==='/api/menu'?{max_defaults:{character:{base_level:60,plus_level:90},items:{}},items:[]}:
        {status:'pending',log:'待機中'});
      return {ok:!result?.status||result.status<400,json:async()=>payload};
    },
  });
  vm.runInContext(source,context);
  await new Promise(resolve=>setImmediate(resolve));
  return {requests,context,nodes,send:text=>vm.runInContext('sendText('+JSON.stringify(text)+')',context),
    click:()=>nodes.get('runDraft').onclick(),value:code=>vm.runInContext(code,context)};
}

async function run(){
  let client=await app(request=>request.url==='/api/chat'?{payload:started}:null);
  await client.send('「確認して実行」');
  let sent=client.requests.find(request=>request.url==='/api/chat');
  assert.equal(sent.body.message,'「確認して実行」');
  assert.equal(sent.body.confirmation_id,'a'.repeat(64));
  assert.equal(client.value('activeJobs.size'),1);

  let count=0;
  client=await app(request=>request.url==='/api/chat'?(++count===1?new Error('offline response loss'):{payload:started}):null);
  await client.send('確認して実行');await client.send('確認して実行');
  let attempts=client.requests.filter(request=>request.url==='/api/chat');
  assert.equal(attempts.length,2);assert.equal(attempts[0].body.request_id,attempts[1].body.request_id);
  assert.equal(attempts[0].body.confirmation_id,attempts[1].body.confirmation_id);

  count=0;
  client=await app(request=>request.url==='/api/confirm'?{payload:++count===1?draft('b'.repeat(64)):started}:null);
  await client.click();
  assert.equal(client.value('activeJobs.size'),0);
  assert.equal(client.value('confirmationId'),'b'.repeat(64));
  assert.equal(client.nodes.get('runDraft').hidden,false);
  await client.click();
  attempts=client.requests.filter(request=>request.url==='/api/confirm');
  assert.equal(attempts[1].body.confirmation_id,'b'.repeat(64));
  assert.notEqual(attempts[0].body.request_id,attempts[1].body.request_id);
  assert.equal(client.value('activeJobs.size'),1);

  let bootstraps=0, chats=0;
  client=await app(request=>{
    if(request.url==='/api/bootstrap'&&++bootstraps>1)
      return {payload:{csrf:'csrf',engine:{ready:true},jobs:[],draft:draft('c'.repeat(64))}};
    if(request.url==='/api/chat')return ++chats===1?
      {status:409,payload:{error:'確認対象が変わりました。'}}:{payload:started};
    return null;
  });
  await client.send('「確認して実行」');
  assert.equal(client.value('activeJobs.size'),0);
  assert.equal(client.value('confirmationId'),'c'.repeat(64));
  await client.send('はい');
  attempts=client.requests.filter(request=>request.url==='/api/chat');
  assert.equal(attempts[1].body.confirmation_id,'c'.repeat(64));
  assert.notEqual(attempts[0].body.request_id,attempts[1].body.request_id);
  const pricedDraft={...draft('d'.repeat(64)),labels:[{key:'catfood',label:'猫缶：58,999'}],
    quoted_prices:[{name:'猫缶指定',price_jpy:50}]};
  client=await app(()=>null,pricedDraft);
  const textOf=node=>[node.textContent,...(node.children||[]).map(textOf)].join('\n');
  assert.match(textOf(client.nodes.get('plan')),/猫缶：58,999/);
  assert.match(textOf(client.nodes.get('plan')),/猫缶指定：50円/);
  const review=client.value('reviewBlock('+JSON.stringify(pricedDraft)+')');
  assert.match(textOf(review),/変更数量とは別/);
  assert.match(textOf(review),/猫缶指定：50円/);
  console.log('5 client flow checks passed (offline; browser layout not tested).');
}
run().catch(error=>{console.error(error);process.exitCode=1;});
