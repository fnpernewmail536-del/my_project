'use strict';

const VIP_FORM_RANGE_DEFS = [
  ['custom_catfood','猫缶',58999], ['custom_xp','XP',99999999], ['custom_np','NP',9999],
  ['custom_normal_tickets','にゃんこチケット',999], ['custom_rare_tickets','レアチケット',999],
  ['custom_platinum_tickets','プラチナチケット',99], ['custom_legend_tickets','レジェンドチケット',99],
  ['custom_leadership','リーダーシップ',999], ['custom_battle_items','バトルアイテム',999],
  ['custom_catseyes','キャッツアイ',999], ['custom_catamins','ネコビタン',999],
  ['custom_base_materials','城素材',999], ['custom_matatabi','マタタビ・獣石',998],
  ['custom_talent_orbs','本能玉',998]
];
const VIP_FORM_RARITIES = ['基本キャラ','EX','レア','激レア','超激レア','伝説レア'];
function blankVipFormExtras(){
  return {quantities:{},levels:Object.fromEntries(VIP_FORM_RARITIES.map((_,i)=>[i,{mode:'none',base:[30,30],plus:[0,0],collab:false,collab_plus:[10,15]}])),excluded:'',plus_supported_only:false,orb_rank:'all'};
}
const vipFormExtrasState = {daiko:blankVipFormExtras(),create:blankVipFormExtras()};
function vipFormExtrasCopy(tab){return JSON.parse(JSON.stringify(vipFormExtrasState[tab]));}
function vipFormExtrasSelectionCount(tab){const saved=vipFormExtrasState[tab];return Object.values(saved.quantities).filter(item=>item.enabled).length+Object.values(saved.levels).filter(item=>item.mode!=='none').length+(saved.excluded.trim()?1:0);}
function restoreVipFormExtrasPreset(tab,value){
  const fresh=blankVipFormExtras();
  if(value && typeof value==='object' && !Array.isArray(value)){
    for(const [key] of VIP_FORM_RANGE_DEFS){
      const item=value.quantities?.[key];
      if(item && Array.isArray(item.bounds) && item.bounds.length===2)fresh.quantities[key]={enabled:item.enabled===true,bounds:item.bounds.map(v=>String(v).slice(0,12))};
    }
    for(let i=0;i<6;i++){
      const row=value.levels?.[i];
      if(!row || !['none','both','base','plus','collab'].includes(row.mode))continue;
      fresh.levels[i].mode=row.mode;fresh.levels[i].collab=row.collab===true;
      for(const key of ['base','plus','collab_plus'])if(Array.isArray(row[key])&&row[key].length===2)fresh.levels[i][key]=row[key].map(v=>String(v).slice(0,12));
    }
    fresh.excluded=String(value.excluded||'').slice(0,8000);
    fresh.plus_supported_only=value.plus_supported_only===true;
    fresh.orb_rank=value.orb_rank==='S'?'S':'all';
  }
  vipFormExtrasState[tab]=fresh;
}
function vipFormBounds(values,min,max,label){
  const nums=values.map(v=>typeof v==='number'?v:/^\d+$/.test(String(v))?Number(v):NaN);
  if(nums.length!==2 || nums.some(v=>!Number.isSafeInteger(v)) || nums[0]<min || nums[1]>max || nums[0]>nums[1])throw new Error(`${label}は${min}〜${max}の整数で、最小値≦最大値にしてください。`);
  return nums;
}
function collectVipFormExtras(tab){
  const saved=vipFormExtrasState[tab],quantities={},levels={};
  for(const [key,label,max] of VIP_FORM_RANGE_DEFS){const value=saved.quantities[key];if(value?.enabled)quantities[key]=vipFormBounds(value.bounds,0,max,label);}
  for(let i=0;i<6;i++){
    const row=saved.levels[i];if(row.mode==='none')continue;
    const rule={};
    if(['both','base'].includes(row.mode))rule.base=vipFormBounds(row.base,1,60,VIP_FORM_RARITIES[i]+' 基本Lv');
    if(['both','plus'].includes(row.mode))rule.plus=vipFormBounds(row.plus,0,90,VIP_FORM_RARITIES[i]+' ＋値');
    if(row.mode==='collab'||row.collab)rule.collab_plus=vipFormBounds(row.collab_plus,0,90,VIP_FORM_RARITIES[i]+' コラボ＋値');
    levels[i]=rule;
  }
  const tokens=String(saved.excluded).trim().split(/[,、\s]+/).filter(Boolean);
  if(tokens.some(v=>!/^\d+$/.test(v)))throw new Error('除外キャラは、名前から選ぶかNo.番号をカンマ区切りで入力してください。');
  const excluded_ids=[...new Set(tokens.map(Number))].sort((a,b)=>a-b);
  if(excluded_ids.length>1000 || excluded_ids.some(v=>!Number.isInteger(v)||v<1||v>9999))throw new Error('除外キャラのNo.番号が不正です。');
  if(Object.keys(levels).length || excluded_ids.length){
    if(typeof characterMetadata==='undefined'||!characterMetadata)throw new Error('キャラ名データを読み込み中です。読み込み後に実行してください。');
    const known=new Set(characterMetadata.filter(c=>c.selectable!==false).map(c=>Number(c.id)));
    if(excluded_ids.some(id=>!known.has(id)))throw new Error('除外キャラに、取得済みの一覧にないNo.番号があります。');
  }
  return {quantities,levels,excluded_ids,plus_supported_only:saved.plus_supported_only,orb_rank:saved.orb_rank};
}
function vipFormExtrasSummary(tab){
  const data=collectVipFormExtras(tab),lines=[];
  for(const [key,label] of VIP_FORM_RANGE_DEFS)if(data.quantities[key])lines.push(`${label}：${data.quantities[key].join('〜')}${key==='custom_talent_orbs'&&data.orb_rank==='S'?'（Sのみ）':''}`);
  for(const [rarity,rule] of Object.entries(data.levels))lines.push(`${VIP_FORM_RARITIES[rarity]}：基本Lv ${rule.base?rule.base.join('〜'):'保持'}／＋値 ${rule.plus?rule.plus.join('〜'):'保持'}${rule.collab_plus?'／コラボ＋ '+rule.collab_plus.join('〜'):''}`);
  if(data.excluded_ids.length)lines.push('基本Lv・＋値を保持するキャラ：No.'+data.excluded_ids.join(', '));
  if(data.plus_supported_only&&Object.keys(data.levels).length)lines.push('＋値非対応キャラの＋値は0（定義が取得できない場合は開始しません）');
  return lines;
}
function showVipFormExtrasSummary(tab){
  const output=document.getElementById(`vip-form-summary-${tab}`);
  try{output.textContent=vipFormExtrasSummary(tab).join('\n')||'追加設定はありません。';}catch(error){output.textContent=error.message;}
}
function confirmVipFormExtras(tab){
  const lines=vipFormExtrasSummary(tab);
  return !lines.length || confirm('追加の詳細設定\n\n'+lines.join('\n')+'\n\n配列は選択した種類ごと、レベルはキャラごとに独立抽選します。追加指定は一括指定より優先します。\nこの内容で実行しますか？');
}
function vipFormRangeInputs(tab,path,values,min,max){
  return values.map((value,index)=>`<label style="flex:1;min-width:80px;">${index?'最大':'最小'}<input type="number" min="${min}" max="${max}" step="1" value="${escChar(value)}" aria-label="${escChar(path)} ${index?'最大値':'最小値'}" oninput="setVipFormBound('${tab}','${path}',${index},this.value)"></label>`).join('');
}
function setVipFormBound(tab,path,index,value){
  const [kind,key,part]=path.split(':');
  if(kind==='quantity')vipFormExtrasState[tab].quantities[key].bounds[index]=value;
  else vipFormExtrasState[tab].levels[key][part][index]=value;
}
function setVipFormMode(tab,rarity,mode){vipFormExtrasState[tab].levels[rarity].mode=mode;replaceVipFormExtrasCard(tab);}
function replaceVipFormExtrasCard(tab){const old=document.getElementById(`vip-form-extras-${tab}`);if(!old)return;const opened=[...old.querySelectorAll('details')].map(element=>element.open);const next=createVipFormExtrasCard(tab);next.open=old.open;[...next.querySelectorAll('details')].forEach((element,index)=>{element.open=opened[index]||false;});old.replaceWith(next);}
function applyVipFormAllRarities(tab){for(let i=1;i<6;i++)vipFormExtrasState[tab].levels[i]=JSON.parse(JSON.stringify(vipFormExtrasState[tab].levels[0]));replaceVipFormExtrasCard(tab);}
function resetVipFormExtras(tab){vipFormExtrasState[tab]=blankVipFormExtras();replaceVipFormExtrasCard(tab);}
function addVipFormExclusion(tab,id){
  const old=String(vipFormExtrasState[tab].excluded).trim();
  const values=old.split(/[,、\s]+/).filter(Boolean);
  if(!values.includes(String(id)))values.push(String(id));
  vipFormExtrasState[tab].excluded=values.join(', ');
  const input=document.getElementById(`vip-form-excluded-${tab}`);if(input)input.value=vipFormExtrasState[tab].excluded;
  showVipFormExtrasSummary(tab);
}
function searchVipFormExclusions(tab,query){
  const output=document.getElementById(`vip-form-exclusion-results-${tab}`);if(!output)return;
  if(!query.trim()){output.innerHTML='';return;}
  if(typeof characterMetadata==='undefined'||!characterMetadata){output.textContent='キャラ名を読み込み中です。';return;}
  const q=query.normalize('NFKC').toLowerCase();
  const matches=characterMetadata.filter(c=>c.selectable!==false&&(String(c.id)===q||c.names.some(n=>n.normalize('NFKC').toLowerCase().includes(q)))).slice(0,30);
  output.innerHTML=matches.map(c=>`<button type="button" class="count-btn" style="margin:3px;" onclick="addVipFormExclusion('${tab}',${Number(c.id)})">No.${Number(c.id)} ${escChar(c.names[0])} を除外</button>`).join('')||'一致するキャラがありません。';
}
function createVipFormExtrasCard(tab){
  const saved=vipFormExtrasState[tab],card=document.createElement('details');card.className='card detail-surface';card.id=`vip-form-extras-${tab}`;
  const quantities=VIP_FORM_RANGE_DEFS.map(([key,label,max])=>{
    const item=saved.quantities[key]||{enabled:false,bounds:[0,max]};saved.quantities[key]=item;
    return `<div class="field"><label><input type="checkbox" style="width:auto;min-width:0;display:inline-block;" ${item.enabled?'checked':''} onchange="vipFormExtrasState['${tab}'].quantities['${key}'].enabled=this.checked"> ${label}の数量を範囲指定</label><div style="display:flex;gap:10px;flex-wrap:wrap;">${vipFormRangeInputs(tab,'quantity:'+key,item.bounds,0,max)}</div></div>`;
  }).join('');
  const levels=VIP_FORM_RARITIES.map((name,i)=>{
    const row=saved.levels[i];
    const modes=[['none','変更しない'],['both','基本Lvと＋値'],['base','基本Lvだけ（＋値保持）'],['plus','＋値だけ（基本Lv保持）'],['collab','コラボだけ＋値']];
    return `<details class="detail-surface" ${row.mode!=='none'?'open':''}><summary>${name}</summary><div class="field"><label>変更する値</label><select onchange="setVipFormMode('${tab}',${i},this.value)">${modes.map(([v,l])=>`<option value="${v}" ${row.mode===v?'selected':''}>${l}</option>`).join('')}</select></div>${['both','base'].includes(row.mode)?`<div class="field"><label>基本Lvの範囲</label><div style="display:flex;gap:10px;">${vipFormRangeInputs(tab,'level:'+i+':base',row.base,1,60)}</div></div>`:''}${['both','plus'].includes(row.mode)?`<div class="field"><label>＋値の範囲</label><div style="display:flex;gap:10px;">${vipFormRangeInputs(tab,'level:'+i+':plus',row.plus,0,90)}</div></div>`:''}${row.mode!=='none'?`<label><input type="checkbox" style="width:auto;min-width:0;display:inline-block;" ${row.collab||row.mode==='collab'?'checked':''} ${row.mode==='collab'?'disabled':''} onchange="vipFormExtrasState['${tab}'].levels[${i}].collab=this.checked;replaceVipFormExtrasCard('${tab}')">コラボの＋値を別指定</label>`:''}${row.mode==='collab'||row.mode!=='none'&&row.collab?`<div class="field"><label>コラボ＋値の範囲</label><div style="display:flex;gap:10px;">${vipFormRangeInputs(tab,'level:'+i+':collab_plus',row.collab_plus,0,90)}</div></div>`:''}</details>`;
  }).join('');
  card.innerHTML=`<summary style="font-weight:700;">範囲指定・レア度別レベル・除外（追加）</summary><div style="margin-top:14px;"><p class="char-note">チェックした数量とレベル指定だけを変更します。最小＝最大なら固定値。配列は選択した種類ごと、レベルはキャラごとに抽選します。既存の個別選択がない数量範囲は全種類が対象です。</p><details class="detail-surface"><summary>数量の範囲指定</summary><div class="custom-inputs-grid">${quantities}</div><label>本能玉の対象 <select onchange="vipFormExtrasState['${tab}'].orb_rank=this.value"><option value="all" ${saved.orb_rank==='all'?'selected':''}>全ランク（既存の種類選択を優先）</option><option value="S" ${saved.orb_rank==='S'?'selected':''}>Sランクのみ（他ランクは保持）</option></select></label></details><details class="detail-surface"><summary>レア度別のレベル・コラボ例外</summary><p class="char-note">指定しない値と除外キャラは、一括レベル変更前の値を保持します。キャラの解放・形態設定は別の項目で選択してください。</p>${levels}<button type="button" class="count-btn" onclick="applyVipFormAllRarities('${tab}')">基本キャラの設定を全レア度に適用</button><p><label><input type="checkbox" style="width:auto;min-width:0;display:inline-block;" ${saved.plus_supported_only?'checked':''} onchange="vipFormExtrasState['${tab}'].plus_supported_only=this.checked">＋値に対応するキャラのみ（対応定義を確認）</label></p></details><details class="detail-surface"><summary>レベルを変更しないキャラ</summary><div class="field"><label>除外キャラのNo.番号（カンマ区切り）</label><input id="vip-form-excluded-${tab}" type="text" value="${escChar(saved.excluded)}" placeholder="例: 1, 29, 143" oninput="vipFormExtrasState['${tab}'].excluded=this.value"></div><div class="field"><label>名前から除外キャラを追加</label><input type="search" placeholder="キャラ名・No.番号" oninput="searchVipFormExclusions('${tab}',this.value)"></div><div id="vip-form-exclusion-results-${tab}"></div></details><button type="button" class="count-btn" onclick="showVipFormExtrasSummary('${tab}')">追加設定の内容を確認</button> <button type="button" class="count-btn" onclick="resetVipFormExtras('${tab}')">追加設定だけ解除</button><div class="char-note" style="white-space:pre-wrap;margin-top:10px;" id="vip-form-summary-${tab}" role="status"></div></div>`;
  ensureCharacterMetadata().catch(()=>{});
  return card;
}
