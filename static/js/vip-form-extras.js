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
  return {quantities:{},levels:Object.fromEntries(VIP_FORM_RARITIES.map((_,i)=>[i,{mode:'none',base:[30,30],plus:[0,0],collab:false,collab_plus:[10,15]}])),excluded:'',plus_supported_only:false,orb_rank:'all',conditional_enabled:false,conditional_rows:[],conditional_no_decrease:false};
}
const vipFormExtrasState = {daiko:blankVipFormExtras(),create:blankVipFormExtras()};
function vipFormExtrasCopy(tab){return JSON.parse(JSON.stringify(vipFormExtrasState[tab]));}
function vipFormExtrasSelectionCount(tab){const saved=vipFormExtrasState[tab];return Object.values(saved.quantities).filter(item=>item.enabled).length+Object.values(saved.levels).filter(item=>item.mode!=='none').length+(saved.excluded.trim()?1:0)+(saved.conditional_enabled?saved.conditional_rows.length:0);}
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
    fresh.conditional_enabled=value.conditional_enabled===true;fresh.conditional_no_decrease=value.conditional_no_decrease===true;
    if(Array.isArray(value.conditional_rows))fresh.conditional_rows=value.conditional_rows.slice(0,200).filter(row=>row&&typeof row==='object'&&VIP_CONDITION_TARGETS.some(([key])=>key===row.target)).map(row=>({...newVipCondition(row.target),...JSON.parse(JSON.stringify(row))}));
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
  const conditional=collectVipConditions(tab);
  if(conditional&&(Object.keys(quantities).length||Object.keys(levels).length||excluded_ids.length))throw new Error('条件付き指定と通常の範囲・レア度別指定は別々に実行してください。');
  return {quantities,levels,excluded_ids,plus_supported_only:saved.plus_supported_only,orb_rank:saved.orb_rank,...(conditional?{conditional}:{})};
}
function vipFormExtrasSummary(tab){
  const data=collectVipFormExtras(tab),lines=[];
  for(const [key,label] of VIP_FORM_RANGE_DEFS)if(data.quantities[key])lines.push(`${label}：${data.quantities[key].join('〜')}${key==='custom_talent_orbs'&&data.orb_rank==='S'?'（Sのみ）':''}`);
  for(const [rarity,rule] of Object.entries(data.levels))lines.push(`${VIP_FORM_RARITIES[rarity]}：基本Lv ${rule.base?rule.base.join('〜'):'保持'}／＋値 ${rule.plus?rule.plus.join('〜'):'保持'}${rule.collab_plus?'／コラボ＋ '+rule.collab_plus.join('〜'):''}`);
  if(data.excluded_ids.length)lines.push('基本Lv・＋値を保持するキャラ：No.'+data.excluded_ids.join(', '));
  if(data.plus_supported_only&&Object.keys(data.levels).length)lines.push('＋値非対応キャラの＋値は0（定義が取得できない場合は開始しません）');
  if(data.conditional){lines.push('条件付き指定（変更前の値で判定）');lines.push(...vipFormExtrasState[tab].conditional_rows.map(vipConditionSummary));if(data.conditional.no_decrease)lines.push('全指定で現在値を減らさない');}
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
  card.innerHTML=`<summary style="font-weight:700;">範囲指定・レア度別レベル・除外（追加）</summary><div style="margin-top:14px;"><p class="char-note">チェックした数量とレベル指定だけを変更します。最小＝最大なら固定値。配列は選択した種類ごと、レベルはキャラごとに抽選します。既存の個別選択がない数量範囲は全種類が対象です。</p><details class="detail-surface"><summary>数量の範囲指定</summary><div class="custom-inputs-grid">${quantities}</div><label>本能玉の対象 <select onchange="vipFormExtrasState['${tab}'].orb_rank=this.value"><option value="all" ${saved.orb_rank==='all'?'selected':''}>全ランク（既存の種類選択を優先）</option><option value="S" ${saved.orb_rank==='S'?'selected':''}>Sランクのみ（他ランクは保持）</option></select></label></details><details class="detail-surface"><summary>レア度別のレベル・コラボ例外</summary><p class="char-note">指定しない値と除外キャラは、一括レベル変更前の値を保持します。キャラの解放・形態設定は別の項目で選択してください。</p>${levels}<button type="button" class="count-btn" onclick="applyVipFormAllRarities('${tab}')">基本キャラの設定を全レア度に適用</button><p><label><input type="checkbox" style="width:auto;min-width:0;display:inline-block;" ${saved.plus_supported_only?'checked':''} onchange="vipFormExtrasState['${tab}'].plus_supported_only=this.checked">＋値に対応するキャラのみ（対応定義を確認）</label></p></details><details class="detail-surface"><summary>レベルを変更しないキャラ</summary><div class="field"><label>除外キャラのNo.番号（カンマ区切り）</label><input id="vip-form-excluded-${tab}" type="text" value="${escChar(saved.excluded)}" placeholder="例: 1, 29, 143" oninput="vipFormExtrasState['${tab}'].excluded=this.value"></div><div class="field"><label>名前から除外キャラを追加</label><input type="search" placeholder="キャラ名・No.番号" oninput="searchVipFormExclusions('${tab}',this.value)"></div><div id="vip-form-exclusion-results-${tab}"></div></details>${vipConditionsHTML(tab)}<button type="button" class="count-btn" onclick="showVipFormExtrasSummary('${tab}')">追加設定の内容を確認</button> <button type="button" class="count-btn" onclick="resetVipFormExtras('${tab}')">追加設定だけ解除</button><div class="char-note" style="white-space:pre-wrap;margin-top:10px;" id="vip-form-summary-${tab}" role="status"></div></div>`;
  ensureCharacterMetadata().catch(()=>{});
  return card;
}

// Structured conditions use the same VIP submission, confirmation and presets.
const VIP_CONDITION_TARGETS = [
  ['resource:catfood','猫缶'],['resource:xp','XP'],['resource:np','NP'],
  ['resource:normal_tickets','にゃんこチケット'],['resource:rare_tickets','レアチケット'],
  ['resource:platinum_tickets','プラチナチケット'],['resource:legend_tickets','レジェンドチケット'],['resource:leadership','リーダーシップ'],
  ['cat:base','キャラの基本Lv'],['cat:plus','キャラの＋値'],['cat:unlocked','キャラ解放'],['cat:forms','第1〜3形態の解放'],
  ['talent:talent','本能'],['items:battle_items','バトルアイテム'],['items:catseyes','キャッツアイ'],['items:catamins','ネコビタン'],['items:base_materials','城素材'],['items:catfruit','マタタビ・獣石'],
  ['orbs:amount','本能玉'],['story:treasure','メイン編のお宝'],['story:clear','メイン編のクリア'],
  ['legend:clear','レジェンドの冠・クリア'],['event:clear','イベント・魔界のクリア'],
  ['helpers:fill','既存隊員を残してレジェンド隊員で10体まで補充'],['helpers:legend','既存レジェンド隊員を残して全10体をレジェンドへ'],
  ['action:facilities','施設MAX'],['action:ototo','オトートMAX'],['action:shrine','にゃんこ神社MAX'],['action:gamatoto_max','ガマトトLv.MAX'],['action:remove_errors','エラーキャラ削除']
];
const VIP_CONDITION_OPS=[['set','固定値'],['keep','現在値を保持'],['max','上限まで'],['add','現在値に加算（負数で減算）'],['mul','現在値に倍率（切り捨て）'],['range','範囲からおまかせ'],['atleast','指定値未満だけ引き上げ']];
const VIP_CONDITION_COMPARE=[['lt','未満'],['le','以下'],['eq','と同じ'],['ne','以外'],['ge','以上'],['gt','より大きい'],['odd','奇数'],['even','偶数']];
const VIP_CONDITION_FIELDS={base:'基本Lv',plus:'＋値',unlocked:'解放状態（0＝未所持、1＝所持）',talent:'本能Lv',treasure:'お宝（0＝なし、1＝粗悪、2＝普通、3＝最高）',clear:'クリア回数',amount:'数量'};
const VIP_CONDITION_CHAPTERS=['日本編1章','日本編2章','日本編3章','未来編1章','未来編2章','未来編3章','宇宙編1章','宇宙編2章','宇宙編3章'];
function newVipCondition(target='resource:catfood'){
  const [domain,field]=target.split(':');
  return {target,op:['helpers','action','talent'].includes(domain)?'max':['legend','event'].includes(domain)?'atleast':'set',value:domain==='story'&&field==='treasure'?3:field==='unlocked'?1:field==='base'?50:1,max:1,
    rarity:'all',collab:'all',names:'',group:'',series:'',forms_min:'',when:[],no_decrease:false,item_names:'',rank:'all',chapters:[0,1,2],legend_series:'legend',stars:3,progressive:false,until_penultimate:false,family:'event',map_name:'',exclude_speed:false};
}
function vipConditionOptions(options,value){return options.map(([v,label])=>`<option value="${escChar(v)}" ${String(v)===String(value)?'selected':''}>${escChar(label)}</option>`).join('');}
function vipConditionSet(tab,index,key,value){vipFormExtrasState[tab].conditional_rows[index][key]=value;}
function vipConditionRerender(tab,index,key,value){vipConditionSet(tab,index,key,value);replaceVipFormExtrasCard(tab);}
function vipConditionTarget(tab,index,value){vipFormExtrasState[tab].conditional_rows[index]=newVipCondition(value);replaceVipFormExtrasCard(tab);}
function addVipCondition(tab,row){const s=vipFormExtrasState[tab];if(s.conditional_rows.length>=200){alert('条件付き指定は200件までです。');return;}s.conditional_enabled=true;s.conditional_rows.push(row||newVipCondition());replaceVipFormExtrasCard(tab);}
function removeVipCondition(tab,index){vipFormExtrasState[tab].conditional_rows.splice(index,1);replaceVipFormExtrasCard(tab);}
function addVipConditionPredicate(tab,index){const row=vipFormExtrasState[tab].conditional_rows[index];if(row.when.length>=8)return;row.when.push({field:vipConditionPredicateFields(row)[0],op:'lt',value:30});replaceVipFormExtrasCard(tab);}
function removeVipConditionPredicate(tab,index,position){vipFormExtrasState[tab].conditional_rows[index].when.splice(position,1);replaceVipFormExtrasCard(tab);}
function vipConditionPredicateFields(row){const [domain,field]=row.target.split(':');return domain==='cat'?['base','plus','unlocked']:domain==='talent'?['talent','unlocked']:domain==='story'?['treasure','clear']:[domain==='items'?'amount':field];}
function vipConditionNumber(value,min=0,max=2147483647){if(!/^-?\d+$/.test(String(value)))throw new Error('条件付き指定の数値は整数で入力してください。');const n=Number(value);if(!Number.isSafeInteger(n)||n<min||n>max)throw new Error(`条件付き指定の数値は${min}〜${max}にしてください。`);return n;}
function vipConditionNames(value){return String(value).split(/[,、\n]+/).map(v=>v.trim()).filter(Boolean);}
function collectVipConditions(tab){
  const s=vipFormExtrasState[tab];if(!s.conditional_enabled)return null;
  if(!s.conditional_rows.length)throw new Error('条件付き指定を1件以上追加してください。');
  if(Object.values(state[tab]).some(values=>values.size)||characterSelectionState[tab].size||document.getElementById(`char-input-${tab}`)?.value.trim())throw new Error('条件付き指定を使う場合は、通常の一括選択・個別キャラ選択を解除してください。固定値も条件付き欄に追加できます。');
  if(tab==='create'&&accountTypeState.create!=='new')throw new Error('条件付き作成では新規アカウントを選択してください。');
  const rules=s.conditional_rows.map(row=>{
    if(!VIP_CONDITION_TARGETS.some(([key])=>key===row.target))throw new Error('条件付きの変更対象が不正です。');
    const [domain,targetField]=row.target.split(':');const field=domain==='items'?'amount':targetField;
    if(!VIP_CONDITION_OPS.some(([key])=>key===row.op))throw new Error('条件付きの計算方法が不正です。');
    const expr={op:row.op};
    if(row.op==='range'){expr.bounds=[vipConditionNumber(row.value),vipConditionNumber(row.max)];if(expr.bounds[0]>expr.bounds[1])throw new Error('条件付き指定の最小値は最大値以下にしてください。');}
    else if(row.op==='mul'){const n=Number(row.value);if(String(row.value).trim()===''||!Number.isFinite(n)||n<0||n>1000)throw new Error('倍率は0〜1000にしてください。');expr.value=String(row.value);}
    else if(['set','add','atleast'].includes(row.op))expr.value=vipConditionNumber(row.value,row.op==='add'?-2147483647:0);
    const rule={domain,field,expr,no_decrease:row.no_decrease===true};
    if(['helpers','action'].includes(domain)&&row.op!=='max')throw new Error('隊員・補助操作はMAXを選択してください。');
    if(['legend','event'].includes(domain)&&!['keep','atleast'].includes(row.op))throw new Error('ステージは保持か、指定値未満だけ引き上げを選択してください。');
    if(['forms','unlocked'].includes(field)&&!['keep','set'].includes(row.op))throw new Error('解放状態・形態は固定値か保持を選択してください。');
    if(field==='unlocked'&&row.op==='set'&&expr.value!==1)throw new Error('キャラ解放の固定値は1です。未所持のままにする例外は「現在値を保持」で指定してください。');
    if(field==='forms'&&row.op==='set'&&![1,2,3].includes(expr.value))throw new Error('解放する形態は1〜3です。第4形態の自動条件判定には対応していません。');
    if(row.when.length){const fields=vipConditionPredicateFields(row);rule.when=row.when.map(c=>{if(!fields.includes(c.field)||!VIP_CONDITION_COMPARE.some(([op])=>op===c.op))throw new Error('現在値の判定条件が不正です。');return {field:c.field,op:c.op,...(!['odd','even'].includes(c.op)?{value:vipConditionNumber(c.value)}:{})};});}
    if(['cat','talent'].includes(domain)){
      const selector={};if(row.rarity!=='all')selector.rarity=vipConditionNumber(row.rarity,0,5);
      if(row.collab!=='all')selector.collab=row.collab==='yes';
      if(row.names.trim())selector.names=vipConditionNames(row.names);
      if(row.group.trim())selector.group=row.group.trim();if(row.series)selector.series=row.series;
      if(row.forms_min)selector.forms_min=vipConditionNumber(row.forms_min,1,3);
      rule.selector=selector;if(domain==='talent')rule.exclude_speed=row.exclude_speed===true;
    }
    if(domain==='items'){rule.group=targetField;if(row.item_names.trim())rule.names=vipConditionNames(row.item_names);}
    if(domain==='orbs'&&row.rank==='S')rule.rank='S';
    if(domain==='story'){rule.chapters=row.chapters.map(v=>vipConditionNumber(v,0,8));if(!rule.chapters.length)throw new Error('対象の章を選択してください。');}
    if(domain==='legend'){rule.series=row.legend_series;rule.stars=vipConditionNumber(row.stars,1,4);rule.progressive=row.progressive===true;rule.until_penultimate=row.until_penultimate===true;}
    if(domain==='event'){rule.family=row.family;if(row.map_name.trim()){if(row.family==='aku')throw new Error('魔界編のマップ名条件には未対応です。空欄にしてください。');rule.map_name=row.map_name.trim();}}
    return rule;
  });
  return {rules,no_decrease:s.conditional_no_decrease===true,cat_no_decrease:false};
}
function vipConditionSummary(row,index){
 const target=VIP_CONDITION_TARGETS.find(([key])=>key===row.target)?.[1]||row.target;
 const scope=[row.rarity!=='all'?VIP_FORM_RARITIES[row.rarity]:'',row.collab==='yes'?'コラボのみ':row.collab==='no'?'コラボを除く':'',row.names,row.group,row.series].filter(Boolean).join('／');
 const op=VIP_CONDITION_OPS.find(([key])=>key===row.op)?.[1]||row.op;
 const amount=['keep','max'].includes(row.op)?'':row.op==='range'?`${row.value}〜${row.max}`:row.value;
 const condition=row.when.map(c=>`${VIP_CONDITION_FIELDS[c.field]||c.field} ${['odd','even'].includes(c.op)?'':c.value}${VIP_CONDITION_COMPARE.find(([key])=>key===c.op)?.[1]}`).join(' かつ ');
 const [domain]=row.target.split(':');
 const details=[domain==='story'?row.chapters.map(p=>VIP_CONDITION_CHAPTERS[p]).join('・'):'',domain==='legend'?`${{legend:'レジェンド',true_legend:'真レジェンド',zero_legend:'零レジェンド'}[row.legend_series]}：${row.progressive?'現在の次の冠へ':row.stars+'冠まで'}${row.until_penultimate?'（最終ステージの1つ前まで）':''}`:'',domain==='event'?`${row.family}${row.map_name?'／'+row.map_name:''}`:'',domain==='orbs'&&row.rank==='S'?'Sのみ':'',domain==='items'?row.item_names:'',row.exclude_speed&&domain==='talent'?'移動速度本能は保持':'',row.no_decrease?'現在値より減らさない':'',row.forms_min?'第'+row.forms_min+'形態の定義があるキャラ':''].filter(Boolean).join('／');
 return `${index+1}. ${target}${scope?'【'+scope+'】':''}${condition?'（'+condition+'のとき）':''} → ${op} ${amount}${details?'／'+details:''}`;
}
function vipConditionChapter(tab,index,chapter,checked){const row=vipFormExtrasState[tab].conditional_rows[index];row.chapters=checked?[...new Set([...row.chapters,chapter])]:row.chapters.filter(v=>v!==chapter);}
function vipConditionsHTML(tab){
 const saved=vipFormExtrasState[tab];
 const rows=saved.conditional_rows.map((row,index)=>{
   const [domain]=row.target.split(':');
   const text=(label,key,type='text')=>`<label>${label}<input type="${type}" value="${escChar(row[key])}" oninput="vipConditionSet('${tab}',${index},'${key}',this.value)"></label>`;
   const select=(label,key,options)=>`<label>${label}<select onchange="vipConditionRerender('${tab}',${index},'${key}',this.value)">${vipConditionOptions(options,row[key])}</select></label>`;
   const check=(label,key)=>`<label><input type="checkbox" style="width:auto;min-width:0;display:inline-block" ${row[key]?'checked':''} onchange="vipConditionSet('${tab}',${index},'${key}',this.checked)"> ${label}</label>`;
   const allowedOps=['action','helpers'].includes(domain)?VIP_CONDITION_OPS.filter(([v])=>v==='max'):['legend','event'].includes(domain)?VIP_CONDITION_OPS.filter(([v])=>['keep','atleast'].includes(v)):['cat:forms','cat:unlocked'].includes(row.target)?VIP_CONDITION_OPS.filter(([v])=>['keep','set'].includes(v)):VIP_CONDITION_OPS;
   return `<details class="detail-surface" open><summary>指定${index+1}：${escChar(VIP_CONDITION_TARGETS.find(([k])=>k===row.target)?.[1])}</summary><div class="custom-inputs-grid"><label>変更対象<select onchange="vipConditionTarget('${tab}',${index},this.value)">${vipConditionOptions(VIP_CONDITION_TARGETS,row.target)}</select></label>${select('変更方法','op',allowedOps)}${!['keep','max'].includes(row.op)?text(row.op==='range'?'最小値':row.op==='mul'?'倍率':'値','value','number'):''}${row.op==='range'?text('最大値','max','number'):''}</div>${['cat','talent'].includes(domain)?`<div class="custom-inputs-grid">${select('レア度','rarity',[['all','全レア度'],...VIP_FORM_RARITIES.map((n,i)=>[i,n])])}${select('コラボ','collab',[['all','全キャラ'],['yes','コラボのみ'],['no','コラボを除く']])}${text('対象キャラ名・No.番号（カンマ区切り／空欄は全対象）','names')}${text('コラボ名（所属データで照合）','group')}${select('キャラ系列','series',[['','指定なし'],['ちび','ちび'],['大狂乱','大狂乱'],['特急','特急']])}${select('形態定義の条件','forms_min',[['','指定なし'],[2,'第2形態まである'],[3,'第3形態まである']])}</div>${domain==='talent'?check('移動速度本能は現在のまま保持','exclude_speed'):''}`:''}${domain==='items'?text('種類名（カンマ区切り／空欄は全種類）','item_names'):''}${domain==='orbs'?select('対象ランク','rank',[['all','全ランク'],['S','Sのみ（他ランクを保持）']]):''}${domain==='story'?`<div class="field"><label>対象の章</label>${VIP_CONDITION_CHAPTERS.map((n,p)=>`<label style="display:inline-block;margin:5px"><input type="checkbox" style="width:auto;min-width:0;display:inline-block" ${row.chapters.includes(p)?'checked':''} onchange="vipConditionChapter('${tab}',${index},${p},this.checked)"> ${n}</label>`).join('')}</div>`:''}${domain==='legend'?`<div class="custom-inputs-grid">${select('系列','legend_series',[['legend','レジェンド'],['true_legend','真レジェンド'],['zero_legend','零レジェンド']])}${select('クリアする冠','stars',[[1,'1冠まで'],[2,'2冠まで'],[3,'3冠まで（4冠保持）'],[4,'4冠まで']])}</div>${check('現在クリア済みの次の冠まで（章ごとに判定）','progressive')}${check('最終ステージの1つ前まで','until_penultimate')}`:''}${domain==='event'?select('系列','family',[['event','イベント'],['collab','コラボ'],['colosseum','異次元コロシアム'],['dojo_ranking','ランキングの間'],['aku','魔界']])+text('マップ名（一致する定義のみ／空欄は全対象）','map_name'):''}${!['action','helpers'].includes(domain)?`<p>${check('この指定では現在値を減らさない','no_decrease')}</p><div class="char-note">現在値の条件（複数条件はすべて満たす場合だけ実行）</div>${row.when.map((c,position)=>`<div style="display:flex;gap:6px;flex-wrap:wrap;margin:7px 0"><select aria-label="条件の値" onchange="vipFormExtrasState['${tab}'].conditional_rows[${index}].when[${position}].field=this.value">${vipConditionOptions(vipConditionPredicateFields(row).map(f=>[f,VIP_CONDITION_FIELDS[f]||f]),c.field)}</select><select aria-label="比較方法" onchange="vipFormExtrasState['${tab}'].conditional_rows[${index}].when[${position}].op=this.value">${vipConditionOptions(VIP_CONDITION_COMPARE,c.op)}</select><input type="number" aria-label="比較する数値" value="${escChar(c.value)}" oninput="vipFormExtrasState['${tab}'].conditional_rows[${index}].when[${position}].value=this.value"><button type="button" class="count-btn" onclick="removeVipConditionPredicate('${tab}',${index},${position})">条件を削除</button></div>`).join('')}<button type="button" class="count-btn" onclick="addVipConditionPredicate('${tab}',${index})">現在値の条件を追加</button>`:''}<p><button type="button" class="count-btn" onclick="removeVipCondition('${tab}',${index})">この指定を削除</button></p></details>`;
 }).join('');
 return `<details class="detail-surface" id="vip-conditions-${tab}"><summary>現在値で条件分岐・加算・倍率・保持（最新版）</summary><p class="char-note">変更前のセーブを基準に判定します。キャラ名による例外は全体指定より優先し、同じ対象では後の指定を優先します。編成・使用形態・お気に入り・並び順は保持します。通常の一括選択・範囲指定は解除して、この欄に固定値も追加してください。解放だけではレベルを変更しません。</p><label><input type="checkbox" style="width:auto;min-width:0;display:inline-block" ${saved.conditional_enabled?'checked':''} onchange="vipFormExtrasState['${tab}'].conditional_enabled=this.checked"> 条件付き指定を使用</label><p><label><input type="checkbox" style="width:auto;min-width:0;display:inline-block" ${saved.conditional_no_decrease?'checked':''} onchange="vipFormExtrasState['${tab}'].conditional_no_decrease=this.checked"> 全指定で現在値を減らさない</label></p>${rows}<button type="button" class="count-btn" onclick="addVipCondition('${tab}')">指定を追加</button> <button type="button" class="count-btn" onclick="addVipConditionExample('${tab}','uber')">例：30未満の通常超激だけLv50</button> <button type="button" class="count-btn" onclick="addVipConditionExample('${tab}','rare')">例：＋20未満のレアだけ＋30</button> <button type="button" class="count-btn" onclick="addVipConditionExample('${tab}','treasure')">例：普通のお宝は保持</button><p class="char-note">開催中コラボの自動判定・属性条件・第4形態の自動解放・降臨全体の条件付きクリアには未対応です。コラボ名・対象キャラ・マップを明示してください。</p></details>`;
}
function addVipConditionExample(tab,example){
 if(example==='uber'){const row=newVipCondition('cat:base');Object.assign(row,{rarity:'4',collab:'no',value:50,when:[{field:'base',op:'lt',value:30}]});addVipCondition(tab,row);}
 if(example==='rare'){const row=newVipCondition('cat:plus');Object.assign(row,{rarity:'2',value:30,when:[{field:'plus',op:'lt',value:20}]});addVipCondition(tab,row);}
 if(example==='treasure'){const row=newVipCondition('story:treasure');Object.assign(row,{value:3,chapters:[0,1,2],when:[{field:'treasure',op:'ne',value:2}]});addVipCondition(tab,row);}
}
