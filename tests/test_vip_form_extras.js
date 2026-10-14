// DOM integration for the actual VIP template and both production scripts.
// Test dependency only: jsdom. No game, account or production API is contacted.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {JSDOM, ResourceLoader, VirtualConsole} = require('jsdom');
const root = path.resolve(__dirname, '..');
const rendered = spawnSync(process.env.CODEX_PRIMARY_RUNTIME_PYTHON || 'python', ['-c', `
from flask import Flask,render_template
app=Flask(__name__,template_folder=${JSON.stringify(path.join(root,'HTML'))})
with app.test_request_context('/vip'):
    print(render_template('vip.html',user={'username':'test-user','is_trial_vip':False,'is_paid_vip':True},
          theme_layout='classic',ui_language='ja',theme_video_url='',discord_user=None))
`], {encoding:'utf8'});
assert.equal(rendered.status, 0, rendered.stderr);
const metadata = {data_version:'15.7.1',rarity_names:['基本','EX','レア','激レア','超激レア','伝説レア'],
  characters:[{id:1,names:['fixture-neko'],rarity:0,form_count:1,selectable:true},
              {id:2,names:['fixture-collab'],rarity:2,form_count:1,selectable:true}],groups:[]};
const legend = {legend:{maps:[]},true_legend:{maps:[]},zero_legend:{maps:[]},zombie:{chapters:[]},
  main_story:{chapters:[]},aku:{stages:[]},facilities:[],talent_orbs:[],event_stage_families:{},vip_item_groups:{},event_ticket_groups:[]};
const requests = [], errors = [];
class Resources extends ResourceLoader {
  fetch(url){
    const name=new URL(url).pathname;
    if(name==='/static/js/vip-app.js'||name==='/static/js/vip-form-extras.js')return Promise.resolve(fs.readFileSync(path.join(root,name.slice(1))));
    if(name.startsWith('/static/css/') && fs.existsSync(path.join(root,name.slice(1))))return Promise.resolve(fs.readFileSync(path.join(root,name.slice(1))));
    return Promise.resolve(Buffer.from(''));
  }
}
const consoleProxy = new VirtualConsole();
consoleProxy.on('jsdomError',error=>errors.push(error.message));
const dom = new JSDOM(rendered.stdout,{url:'https://test.example/vip',runScripts:'dangerously',resources:new Resources(),
  pretendToBeVisual:true,virtualConsole:consoleProxy,beforeParse(window){
    window.fetch=async url=>{requests.push(String(url));return {ok:true,status:200,json:async()=>String(url).includes('character_metadata')?metadata:legend};};
    window.alert=()=>{};window.confirm=()=>false;
    window.scrollTo=()=>{};window.HTMLElement.prototype.scrollIntoView=function(){};
  }});
(async()=>{
  const w=dom.window;
  await new Promise(resolve=>w.addEventListener('load',resolve,{once:true}));
  await new Promise(resolve=>w.setTimeout(resolve,50));
  assert.equal(errors.length,0,errors.join('\n'));
  for(const tab of ['daiko','create'])assert.ok(w.document.getElementById('vip-form-extras-'+tab));
  const card=w.document.getElementById('vip-form-extras-daiko');card.open=true;
  const sections=card.querySelectorAll('details');sections[1].open=true;sections[4].open=true;
  const select=sections[4].querySelector('select');select.value='plus';select.dispatchEvent(new w.Event('change',{bubbles:true}));
  const updated=w.document.getElementById('vip-form-extras-daiko');
  assert.equal(updated.querySelectorAll('details')[1].open,true);
  assert.equal(updated.querySelectorAll('details')[4].open,true);
  w.eval("vipFormExtrasState.daiko.levels[2].plus=[10,15];vipFormExtrasState.daiko.levels[2].collab=true;vipFormExtrasState.daiko.levels[2].collab_plus=[1,1];vipFormExtrasState.daiko.quantities.custom_catfood={enabled:true,bounds:[42000,58000]}");
  w.searchVipFormExclusions('daiko','fixture-neko');
  const result=w.document.querySelector('#vip-form-exclusion-results-daiko button');assert.ok(result);result.click();
  const payload=w.collectPayload('daiko');
  assert.deepEqual(JSON.parse(JSON.stringify(payload.vip_form_extras)),{
    quantities:{custom_catfood:[42000,58000]},levels:{2:{plus:[10,15],collab_plus:[1,1]}},excluded_ids:[1],plus_supported_only:false,orb_rank:'all'});
  const preset=w.capturePreset('daiko');
  w.openPresetModal('daiko');w.registerCurrentPreset();
  assert.equal(w.loadPresets().length,1,'Advanced settings alone must be registerable');
  const registered=w.loadPresets()[0];
  w.resetVipFormExtras('daiko');
  w.selectPreset(registered.id);await w.loadSelectedPreset();
  assert.deepEqual(JSON.parse(JSON.stringify(w.collectPayload('daiko').vip_form_extras)),JSON.parse(JSON.stringify(payload.vip_form_extras)));
  w.eval("vipFormExtrasState.daiko.quantities.custom_catfood.bounds=[58000,42000]");
  assert.match(w.validateBeforeSubmit('daiko',{requireCodes:false}),/最小値/);
  w.openPresetModal('daiko');w.registerCurrentPreset();assert.equal(w.loadPresets().length,1,'Invalid ranges must not be stored');
  assert.deepEqual(JSON.parse(JSON.stringify(w.collectVipFormExtras('create'))),{quantities:{},levels:{},excluded_ids:[],plus_supported_only:false,orb_rank:'all'});
  w.eval("vipFormExtrasState.create.quantities.custom_catfood={enabled:true,bounds:[1,1]}");
  await w.submitCreate();
  assert.equal(requests.some(url=>url.includes('/api/get_key')),false,'Cancelling confirmation must not request an execution key');
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: both forms, real controls, open state, payload, preset roundtrip, invalid ranges, tab isolation, confirmation cancellation');
  w.resetVipFormExtras('daiko');w.resetVipFormExtras('create');
  const example=w.document.querySelector('#vip-conditions-daiko button[onclick*=\"uber\"]');assert.ok(example);example.click();
  const conditionCard=w.document.getElementById('vip-conditions-daiko');conditionCard.open=true;
  const comparisons=conditionCard.querySelectorAll('input[aria-label="比較する数値"]');assert.equal(comparisons.length,1);comparisons[0].value='29';comparisons[0].dispatchEvent(new w.Event('input',{bubbles:true}));
  let conditional=w.collectPayload('daiko').vip_form_extras.conditional;
  assert.deepEqual(JSON.parse(JSON.stringify(conditional.rules[0])),{domain:'cat',field:'base',expr:{op:'set',value:50},no_decrease:false,when:[{field:'base',op:'lt',value:29}],selector:{rarity:4,collab:false}});
  const saveConditional=w.capturePreset('daiko');w.resetVipFormExtras('daiko');w.restorePreset('daiko',saveConditional);
  assert.deepEqual(JSON.parse(JSON.stringify(w.collectPayload('daiko').vip_form_extras.conditional)),JSON.parse(JSON.stringify(conditional)));
  w.addVipConditionExample('daiko','treasure');
  const rules=w.collectVipConditions('daiko').rules;
  assert.deepEqual(JSON.parse(JSON.stringify(rules[1])),{domain:'story',field:'treasure',expr:{op:'set',value:3},no_decrease:false,when:[{field:'treasure',op:'ne',value:2}],chapters:[0,1,2]});
  w.eval("state.daiko.s1.add('catfood')");assert.match(w.validateBeforeSubmit('daiko',{requireCodes:false}),/解除/);w.eval("state.daiko.s1.clear()");
  w.eval("vipFormExtrasState.daiko.conditional_rows[0].value='50.5'");assert.match(w.validateBeforeSubmit('daiko',{requireCodes:false}),/整数/);
  w.eval("vipFormExtrasState.daiko.conditional_rows[0].value='50'");
  assert.ok(w.vipFormExtrasSummary('daiko').join('\n').includes('コラボを除く'));
  w.addVipConditionExample('create','rare');await w.submitCreate();
  assert.equal(requests.some(url=>url.includes('/api/get_key')),false,'Cancelling conditional confirmation must not acquire a key');
  assert.equal(errors.length,0,errors.join('\n'));
  console.log('PASS: conditional controls, payload, preset, keep rules, invalid input, mixed-selection rejection and cancellation');
  w.close();
})().catch(error=>{console.error(error);dom.window.close();process.exitCode=1;});
