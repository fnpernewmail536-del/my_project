'use strict';
// Native CPS forms in jsdom. Game/network operations are replaced.
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const settle=()=>new Promise(resolve=>setImmediate(resolve));
const maps=[{id:0,name:'4 crowns',crowns:4,stages:['a','b']},{id:1,name:'1 crown',crowns:1,stages:['c']}];
const metadata=Object.fromEntries(['legend','true_legend','zero_legend'].map(key=>[key,{label:key,maps}]));
Object.assign(metadata,{vip_item_groups:{},facilities:[],talent_orbs:[],event_tickets:[],event_stage_families:{}});

async function load(page,fixtureMetadata=metadata,signedIn=false){
 const html=execFileSync(process.env.PYTHON||'python',['-c',`
import sys
from jinja2 import Environment,FileSystemLoader
env=Environment(loader=FileSystemLoader('HTML'))
print(env.get_template(sys.argv[1]+'.html').render(
 url_for=lambda name,**kw:'/static/'+kw['filename'] if name=='static' else '/'+name,
 theme_layout='simple',ui_language='ja',user={'username':'fixture','is_vip':True} if sys.argv[2]=='1' else None,discord_user=None,discord_is_admin=False,theme_video_url=''))
`,page,signedIn?'1':'0'],{encoding:'utf8'});
 const dom=new JSDOM(html,{url:'https://cps.test/',runScripts:'outside-only'});
 const w=dom.window;
 w.HTMLCanvasElement.prototype.getContext=()=>null;
 w.setInterval=()=>0;w.setTimeout=()=>0;w.confirm=()=>true;
 w.fetch=async url=>({ok:true,json:async()=>url.includes('legend_metadata')?fixtureMetadata:url.includes('character_metadata')?{characters:[],groups:[]}:url.includes('usage_count')?{count:0}:{logged_in:true,is_vip:true,remaining:10,success:true}});
 for(const script of ['legend-crowns','stage-progress','code-copy'])w.eval(fs.readFileSync(`static/js/${script}.js`,'utf8'));
 const extras=page==='vip'?fs.readFileSync('static/js/vip-form-extras.js','utf8')+'\n':'';
 w.eval(extras+fs.readFileSync(`static/js/${page}-app.js`,'utf8')+`\nwindow.nativeTest={startJob,renderHistory,setHistoryTab:tab=>{currentHistTab=tab;renderHistory();},state,collectPayload,resetAll,switchTab,legendCrownState,legendCrownModeState,${page==='index'?'captureFreePreset,loadFreePresetIntoCurrentTab,syncLegendCrownSettings':'legendSelectionState,setLegendSelection,refreshLegendChecks,createLegendCard,collectLegendStages,capturePreset,restorePreset'}};`);
 for(let i=0;i<10;i++)await settle();return dom;
}
function assertInteractive(el){
 assert.ok(el,'Control exists');
 assert.equal(el.matches(':disabled'),false,`${el.id} must remain enabled`);
 assert.equal(el.closest('.tab-panel')?.classList.contains('active'),true,`${el.id} belongs to the visible mode`);
 for(let parent=el;parent;parent=parent.parentElement)assert.notEqual(parent.inert,true,`${el.id} must not be inside an inert panel`);
}
function choose(w,tab,key,value){const select=w.document.getElementById(`legend-crown-picker-${tab}-${key}`);assertInteractive(select);assert.equal(select.querySelector(`[value="${value}"]`).disabled,false);select.value=String(value);select.dispatchEvent(new w.Event('change'));assertInteractive(select);}
function chooseMode(w,tab,key,value){const select=w.document.getElementById(`legend-crown-mode-${tab}-${key}`);assertInteractive(select);select.value=value;select.dispatchEvent(new w.Event('change'));assertInteractive(select);}
const plain=value=>JSON.parse(JSON.stringify(value));
function checkPickersInBothModes(dom){
 const w=dom.window,t=w.nativeTest;
 for(const tab of ['daiko','create']){
  t.switchTab(tab);t.resetAll(tab);
  w.document.querySelector(`#options-${tab} [data-v="all_stage_clear"]`).click();
  for(const key of ['legend','true_legend','zero_legend']){
   chooseMode(w,tab,key,'set');choose(w,tab,key,0);
   assert.equal(t.collectPayload(tab).legend_progress[key],0);
   choose(w,tab,key,1);assert.equal(t.collectPayload(tab).legend_progress[key],1);
   chooseMode(w,tab,key,'add');
   assert.equal(t.collectPayload(tab).legend_progress[key],undefined);
  }
 }
}

async function run(){
 const normal=await load('index'),w=normal.window,t=w.nativeTest;
 const picker=w.document.getElementById('legend-crown-picker-daiko-legend');
 assert.ok(picker);assert.equal(picker.value,'4');assert.equal(picker.disabled,true);
 w.document.querySelector('#options-daiko [data-v="legend_clear"]').click();
 assert.equal(picker.disabled,false);assert.equal(w.document.getElementById('legend-crown-settings-daiko').hidden,false);
 choose(w,'daiko','legend',2);
 assert.deepEqual(JSON.parse(JSON.stringify(t.collectPayload('daiko').legend_crowns)),{legend:2});
 assert.deepEqual(JSON.parse(JSON.stringify(t.collectPayload('create').legend_crowns)),{});
 t.state.daiko.s1.add('all_stage_clear');t.syncLegendCrownSettings('daiko');
 choose(w,'daiko','true_legend',3);choose(w,'daiko','zero_legend',1);
 assert.deepEqual(JSON.parse(JSON.stringify(t.collectPayload('daiko').legend_crowns)),{legend:2,true_legend:3,zero_legend:1});
 const preset=t.captureFreePreset('daiko');w.localStorage.setItem('autocat_presets_free_v1',JSON.stringify(preset));
 t.resetAll('daiko');assert.equal(w.document.getElementById('legend-crown-picker-daiko-legend').value,'4');
 t.loadFreePresetIntoCurrentTab();assert.equal(w.document.getElementById('legend-crown-picker-daiko-legend').value,'2');
 delete preset.legend_crowns;w.localStorage.setItem('autocat_presets_free_v1',JSON.stringify(preset));t.loadFreePresetIntoCurrentTab();
 assert.equal(w.document.getElementById('legend-crown-picker-daiko-legend').value,'4');
 chooseMode(w,'daiko','legend','set');choose(w,'daiko','legend',1);
 assert.deepEqual(plain(t.collectPayload('daiko').legend_progress),{legend:1});
 assert.equal(t.collectPayload('daiko').legend_crowns.legend,undefined);
 choose(w,'daiko','legend',0);
 assert.deepEqual(plain(t.collectPayload('daiko').legend_progress),{legend:0});
 assert.equal(w.document.querySelector('#legend-crown-picker-daiko-legend [value="0"]').disabled,false);
 assert.deepEqual(plain(t.collectPayload('create').legend_progress),{});
 const exactPreset=t.captureFreePreset('daiko');w.localStorage.setItem('autocat_presets_free_v1',JSON.stringify(exactPreset));
 t.resetAll('daiko');assert.equal(w.document.getElementById('legend-crown-mode-daiko-legend').value,'add');
 t.loadFreePresetIntoCurrentTab();
 assert.equal(w.document.getElementById('legend-crown-mode-daiko-legend').value,'set');
 assert.equal(w.document.getElementById('legend-crown-picker-daiko-legend').value,'0');
 assert.deepEqual(plain(t.collectPayload('daiko').legend_progress),{legend:0});
 chooseMode(w,'daiko','legend','add');
 assert.equal(w.document.getElementById('legend-crown-picker-daiko-legend').value,'1');
 assert.deepEqual(plain(t.collectPayload('daiko').legend_progress),{});
 assert.equal(w.document.querySelector('#legend-crown-picker-daiko-legend [value="0"]').disabled,true);
 delete exactPreset.legend_crowns;delete exactPreset.legend_crown_modes;
 w.localStorage.setItem('autocat_presets_free_v1',JSON.stringify(exactPreset));t.loadFreePresetIntoCurrentTab();
 assert.equal(w.document.getElementById('legend-crown-mode-daiko-legend').value,'add');
 assert.equal(w.document.getElementById('legend-crown-picker-daiko-legend').value,'4');

 const vip=await load('vip'),v=vip.window,x=v.nativeTest;
 v.document.querySelector('#options-daiko [data-v="all_stage_clear"]').click();
 choose(v,'daiko','legend',2);choose(v,'daiko','true_legend',3);choose(v,'daiko','zero_legend',1);
 const result=JSON.parse(JSON.stringify(x.collectLegendStages('daiko')));
 assert.deepEqual(Object.keys(result.legend[0]),['0','1']);
 assert.deepEqual(result.legend[1],{'0':[0]});
 assert.deepEqual(Object.keys(result.true_legend[0]),['0','1','2']);
 assert.deepEqual(Object.keys(result.zero_legend[0]),['0']);
 assert.deepEqual(JSON.parse(JSON.stringify(x.collectLegendStages('create'))),{});
 x.setLegendSelection('daiko','legend',(id,star,stage)=>id===0&&star===0&&stage===0,false);
 assert.equal(v.document.getElementById('legend-crown-picker-daiko-legend').value,'custom');
 assert.equal(x.collectLegendStages('daiko').legend[0][0].includes(0),false);
 const originalSet=[...x.legendSelectionState.daiko.legend];
 chooseMode(v,'daiko','legend','set');choose(v,'daiko','legend',1);
 assert.deepEqual(plain(x.collectPayload('daiko').legend_progress),{legend:1});
 assert.equal(x.collectLegendStages('daiko').legend,undefined);
 assert.equal(v.document.querySelector('[data-legend-tab="daiko"][data-legend-series="legend"]').disabled,true);
 x.setLegendSelection('daiko','legend',()=>true,true);
 assert.deepEqual([...x.legendSelectionState.daiko.legend],originalSet);
 choose(v,'daiko','legend',0);
 assert.deepEqual(plain(x.collectPayload('daiko').legend_progress),{legend:0});
 assert.deepEqual(plain(x.collectPayload('create').legend_progress),{});
 const vipPreset=x.capturePreset('daiko');x.resetAll('daiko');x.restorePreset('daiko',vipPreset);
 assert.equal(v.document.getElementById('legend-crown-mode-daiko-legend').value,'set');
 assert.equal(v.document.getElementById('legend-crown-picker-daiko-legend').value,'0');
 assert.deepEqual(plain(x.collectPayload('daiko').legend_progress),{legend:0});
 chooseMode(v,'daiko','legend','add');
 assert.equal(v.document.getElementById('legend-crown-picker-daiko-legend').value,'custom');
 assert.equal(v.document.querySelector('[data-legend-tab="daiko"][data-legend-series="legend"]').disabled,false);
 delete vipPreset.legend_crowns;delete vipPreset.legend_crown_modes;x.restorePreset('daiko',vipPreset);
 assert.equal(v.document.getElementById('legend-crown-mode-daiko-legend').value,'add');
 x.resetAll('daiko');assert.equal(x.legendSelectionState.daiko.legend.size,9);
 checkPickersInBothModes(normal);checkPickersInBothModes(vip);
 for(let i=0;i<10;i++)await settle();
 normal.window.close();vip.window.close();
 console.log('Native CPS crown UI passed: enabled pickers for both pages, both modes and all three series; additions, exact 0–4, presets, individual edits and reset.');
}
if(require.main===module)run().catch(error=>{console.error(error);process.exitCode=1;});
module.exports={load,settle};
