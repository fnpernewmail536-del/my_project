(function () {
  'use strict';
  const states = {daiko: [], create: []}, hosts = {}, errors = {daiko: '', create: ''};
  let metadata = null, pending = null;
  const clone = value => JSON.parse(JSON.stringify(value));
  const el = (tag, text, cls) => {const node = document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};
  function groups() {
    if (!metadata) return [];
    const list=[];
    for(const family of ['main_story','zombie'])for(let section=0;section<3;section++){
      const maps=(metadata[family]?.chapters||[]).filter(m=>Math.floor(m.id/3)===section).map(m=>({...m,crowns:1}));
      if(maps.length)list.push({key:`${family}-${section}`,family,label:(family==='zombie'?'ゾンビ・':'')+['日本編','未来編','宇宙編'][section],maps});
    }
    for(const family of ['aku','aku_ex']){
      const maps=(metadata[family]?.chapters||[]).map(m=>({...m,crowns:1}));
      if(maps.length)list.push({key:family,family,label:metadata[family]?.label||family,maps});
    }
    for(const [key,spec] of Object.entries(metadata.event_stage_families||{}))if(spec.maps?.length)
      list.push({key:`event:${key}`,family:`event:${key}`,label:spec.label||key,maps:spec.maps});
    return list;
  }
  async function load() {
    if(metadata)return metadata;
    if(!pending)pending=fetch('/api/legend_metadata').then(r=>{if(!r.ok)throw Error('ステージ定義を読み込めませんでした。');return r.json();}).then(data=>{metadata=data;return data;}).finally(()=>{pending=null;});
    return pending;
  }
  function changed(tab) {renderList(tab);document.dispatchEvent(new CustomEvent('cps-stage-progress-change',{detail:{tab}}));}
  function renderList(tab) {
    const host=hosts[tab];if(!host)return;
    const list=host.querySelector('.stage-progress-list');list.replaceChildren();
    const rows=states[tab];
    host.querySelector('.stage-progress-count').textContent=`指定 ${rows.length}件`;
    if(errors[tab])list.append(el('p',errors[tab]));
    if(!rows.length){list.append(el('p','進行の変更は未指定です。','legend-crown-help'));return;}
    const catalog=groups();
    for(const row of (host.dataset.showAll==='1'?rows:rows.slice(0,100))){
      const group=catalog.find(g=>g.family===row.family&&g.maps.some(m=>m.id===row.map));
      const map=group?.maps.find(m=>m.id===row.map);
      const line=el('div',null,'stage-progress-pending');
      line.append(el('span',map?`${group.label}／${map.name}${map.crowns>1?` ★${row.star+1}`:''}：${row.progress}/${map.stages.length}まで`:`定義確認待ち：${row.family} #${row.map} ★${row.star+1} → ${row.progress}`));
      const remove=el('button','削除');remove.type='button';remove.onclick=()=>{states[tab]=states[tab].filter(r=>r!==row);changed(tab);};line.append(remove);list.append(line);
    }
    if(rows.length>100&&host.dataset.showAll!=='1'){const show=el('button',`全${rows.length}件の対象を表示`);show.type='button';show.onclick=()=>{host.dataset.showAll='1';renderList(tab);};list.append(show);}
  }
  function mount(tab) {
    const host=el('details',null,'card stage-progress-settings');host.id=`stage-progress-${tab}`;hosts[tab]=host;
    const summary=el('summary','ステージ進行を揃える（日本・未来・宇宙・イベント系）');summary.append(el('span','指定 0件','stage-progress-count'));host.append(summary);
    host.append(el('p','指定した章・マップの進行を戻せます。この一覧の指定を最後に適用します。指定数よりステージが少ないマップは全クリア扱いです。以降のステージは未クリアになり、取得済みのお宝・スコア・キャラ・報酬は保持します。旧・真・零の星指定は従来の欄を使います。','legend-crown-help'));
    const editor=el('div',null,'stage-progress-editor');host.append(editor);
    const clear=el('button','このモードの進行指定を全解除');clear.type='button';clear.onclick=()=>{states[tab]=[];errors[tab]='';changed(tab);};host.append(clear,el('div',null,'stage-progress-list'));
    let built=false,loading=false;
    async function initialise(){
      if(built||loading)return;loading=true;editor.textContent='ステージ定義を読み込み中…';
      try{await load();if(hosts[tab]!==host)return;buildEditor(tab,editor);built=true;renderList(tab);}
      catch{editor.replaceChildren(el('span','ステージ定義を読み込めません。'));
        const retry=el('button','再読込');retry.type='button';retry.onclick=initialise;editor.append(retry);}finally{loading=false;}
    }
    host.addEventListener('toggle',()=>{if(host.open)initialise();});
    renderList(tab);
    if(states[tab].length){host.open=true;initialise();}
    return host;
  }
  function buildEditor(tab,editor) {
    editor.replaceChildren();const catalog=groups();
    if(!catalog.length){editor.append(el('p','対応するステージ定義がありません。'));return;}
    const category=el('select'),mapPicker=el('select'),crowns=el('select'),mode=el('select'),number=el('input');
    const fields=[['系統',category,'category'],['対象章・マップ',mapPicker,'map'],['クリアする星',crowns,'crowns'],['進行',mode,'mode'],['クリアするステージ数',number,'count']];
    for(const [label,input,key] of fields){input.id=`stage-progress-${tab}-${key}`;const row=el('label',label,'stage-progress-field');row.htmlFor=input.id;row.append(input);editor.append(row);}
    catalog.forEach(g=>category.add(new Option(g.label,g.key)));
    for(const [value,label] of [['clear','全クリア'],['unclear','全て未クリア'],['prefix','指定ステージまでクリア']])mode.add(new Option(label,value));
    number.type='number';number.min='0';number.value='1';number.step='1';
    const selectedMaps=()=>{const g=catalog.find(g=>g.key===category.value);return mapPicker.value==='all'?g.maps:g.maps.filter(m=>String(m.id)===mapPicker.value);};
    function syncMaps(){const g=catalog.find(g=>g.key===category.value);mapPicker.replaceChildren(new Option('この系統の全章・全マップ','all'));g.maps.forEach(m=>mapPicker.add(new Option(m.name,String(m.id))));syncBounds();}
    function syncBounds(){const previous=crowns.value;const maps=selectedMaps();const max=Math.max(...maps.map(m=>m.crowns));crowns.replaceChildren(new Option('実装済みの全ての星','4'));for(let n=1;n<max;n++)crowns.add(new Option(`★${n}まで（上位は未クリア）`,String(n)));if([...crowns.options].some(o=>o.value===previous))crowns.value=previous;crowns.disabled=max===1;number.max=String(Math.max(...maps.map(m=>m.stages.length)));number.disabled=mode.value!=='prefix';}
    category.onchange=syncMaps;mapPicker.onchange=syncBounds;mode.onchange=syncBounds;syncMaps();
    const status=el('p','', 'legend-crown-help'),add=el('button','この進行指定を追加');add.type='button';
    add.onclick=()=>{
      const n=Number(number.value),maps=selectedMaps(),g=catalog.find(g=>g.key===category.value);
      if(mode.value==='prefix'&&(!Number.isInteger(n)||n<0||number.value.trim()===''||n>Number(number.max))){status.textContent=`0〜${number.max}の整数を入力してください。`;return;}
      const rows=[];for(const map of maps)for(let star=0;star<map.crowns;star++)rows.push({family:g.family,map:map.id,star,progress:mode.value==='unclear'||star>=Number(crowns.value)?0:mode.value==='clear'?map.stages.length:Math.min(n,map.stages.length)});
      const byKey=new Map(states[tab].map(r=>[`${r.family}:${r.map}:${r.star}`,r]));rows.forEach(r=>byKey.set(`${r.family}:${r.map}:${r.star}`,r));
      states[tab]=[...byKey.values()];errors[tab]='';status.textContent=`${rows.length}件を追加しました。実行前に下の対象一覧を確認してください。`;changed(tab);
    };editor.append(add,status);
  }
  function restore(tab,raw) {errors[tab]='';if(raw===undefined)states[tab]=[];else if(Array.isArray(raw)&&raw.length<=10000&&raw.every(r=>r&&typeof r.family==='string'&&['map','star','progress'].every(k=>Number.isInteger(r[k])&&r[k]>=0)&&r.map<1000&&r.star<4&&r.progress<=256))states[tab]=clone(raw);else{states[tab]=[];errors[tab]='プリセットのステージ進行指定が不正です。再指定してください。';}}
  function error(tab){
    if(errors[tab])return errors[tab];if(!states[tab].length)return '';
    if(!metadata)return 'ステージ定義の読み込み完了を待ってから実行してください。';
    const catalog=groups();
    for(const row of states[tab]){const map=catalog.find(g=>g.family===row.family&&g.maps.some(m=>m.id===row.map))?.maps.find(m=>m.id===row.map);
      if(!map||row.star>=map.crowns||row.progress>map.stages.length)return '指定したステージの定義・進行範囲を確認できません。対象一覧を確認して再指定してください。';}
    return '';
  }
  window.CPSStageProgress={mount,collect:tab=>clone(states[tab]),restore,reset:tab=>{states[tab]=[];errors[tab]='';},error,count:tab=>states[tab].length};
}());
