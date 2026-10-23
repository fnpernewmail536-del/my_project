'use strict';
const $ = id => document.getElementById(id);
let csrf = '', sending = false, lastRequest = null, lastConfirmation = null, confirmationId = '', canConfirm = false, activeJobs = new Map(), engineErrorShown = false;
let settingsDirty=false,credentialsDirty=false;
const labels = {pending:'待機中',running:'処理中',done:'完了',error:'エラー',interrupted:'中断'};
function el(tag, cls, text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e;}
function message(text, kind='bot'){
  if(kind==='user')text=text.replace(/((?:引き継ぎコード|引継ぎコード|認証番号)\s*[:：=]?\s*)[a-z0-9]+/gi,'$1••••');
  const row=el('div','message '+kind), bubble=el('div','bubble');
  if(kind!=='user')row.append(el('span','bot-avatar','✧'));
  bubble.append(el('div','',text));bubble.append(el('div','time',new Date().toLocaleTimeString('ja-JP',{hour:'2-digit',minute:'2-digit'})));
  row.append(bubble);$('messages').append(row);$('messages').scrollTop=$('messages').scrollHeight;return bubble;
}
async function api(path, data){
  const response=await fetch("/chat"+path,{method:data?'POST':'GET',headers:data?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:data?JSON.stringify(data):undefined,cache:'no-store'});
  const payload=await response.json();if(!response.ok){const err=new Error(payload.error||'操作できませんでした。');err.isServer=true;throw err;}return payload;
}
function toast(text){$('toast').textContent=text;$('toast').hidden=false;setTimeout(()=>$('toast').hidden=true,2200);}
function codeRow(label,value){
  const row=el('div','code-row');row.append(el('span','',label),el('code','',value));
  const copy=el('button','copy','コピー');copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(value);toast('コピーしました');}catch{toast('コードを選択してコピーしてください');}};row.append(copy);return row;
}
function resultBlock(job){
  const block=el('div','codeblock');
  block.append(el('div','result-title',job.status==='done'?'代行が完了しました。':(job.error||job.log||labels[job.status]||'確認中')));
  const codes=el('div','result-codes');
  if(job.accounts?.length){
    if(job.status!=='done')block.append(el('div','','作成済みのアカウントです。重複実行せず、結果を確認してください。'));
    for(const [index,account] of job.accounts.entries()){
      codes.append(el('strong','','アカウント '+(index+1)),codeRow('引き継ぎコード',account.tc),codeRow('認証番号',account.ac));
    }
  }
  else if(job.status==='done'&&job.transfer_code&&job.auth_code){codes.append(codeRow('引き継ぎコード',job.transfer_code),codeRow('認証番号',job.auth_code));}
  else if(job.recovery_transfer_code&&job.recovery_auth_code){block.append(el('div','','作業前の復旧用コード'));codes.append(codeRow('復旧用コード',job.recovery_transfer_code),codeRow('認証番号',job.recovery_auth_code));}
  block.append(codes);
  if(job.applied?.length)block.append(el('div','history-labels',job.applied.join(' / ')));
  if(job.backup_before_saved)block.append(el('div','history-labels','64番：代行前バックアップを保存済み'));
  if(job.admin_recovery_required||job.status==='interrupted')block.append(el('div','','元サイトの管理パネルで受付IDを確認してください。同じコードで再実行しないでください。'));
  block.append(el('div','operation','受付ID：'+job.operation_id));return block;
}
function quotedPricesBlock(prices){
  const block=el('details'),summary=el('summary','','入力された料金（変更数量とは別）'),rows=el('ul');
  for(const item of prices)rows.append(el('li','',item.name+'：'+item.price_jpy.toLocaleString('ja-JP')+'円'));
  block.append(summary,rows);return block;
}
function reviewBlock(data){
  const block=el('div','codeblock');block.append(el('strong','','確認する内容'));
  const list=el('ul');for(const item of data.labels||[])list.append(el('li','',item.label));block.append(list);
  if(data.quoted_prices?.length)block.append(quotedPricesBlock(data.quoted_prices));
  if(data.inferences?.length){block.append(el('strong','','推測した表現'));for(const item of data.inferences)block.append(el('div','history-labels',item.input+' → '+item.suggested));}
  block.append(el('div','history-labels',data.ready?'合っていれば「確認して実行」、または「はい」と返信してください。':'不足・未解釈の項目を補ってから確認します。'));
  return block;
}
function showPlan(data){
  if(data.settings?.mode)showSettings(data.settings);
  showCredentialStatus(data.credentials);
  const interpretation=data.interpretation;
  $('understanding').textContent=interpretation?
    '読み取り '+interpretation.review_items+'項目 ／ 要確認 '+interpretation.needs_review+'件 ／ 表現の整理 '+interpretation.rewrites+'件':
    '変更内容と対象を確認してください。';
  $('plan').replaceChildren();
  for(const item of data.labels||[])$('plan').append(el('div','plan-item',item.label));
  if(data.quoted_prices?.length)$('plan').append(quotedPricesBlock(data.quoted_prices));
  for(const reason of data.blockers||[])$('plan').append(el('div','plan-item blocker',reason));
  if(data.inferences?.length){
    $('plan').append(el('strong','','推測した表現（現在の数量は上の一覧を確認）'));
    for(const item of data.inferences)$('plan').append(el('div','plan-item inference',item.input+' → '+item.suggested));
  }
  for(const [index,item] of (data.unresolved||[]).entries()){
    const row=el('div','plan-item blocker');row.append(el('div','','未解釈項目'+(index+1)+'：'+item.input));
    const edit=el('button','copy','訂正する');edit.type='button';edit.onclick=()=>{$('input').value='未解釈項目'+(index+1)+'=';$('input').focus();};
    const omit=el('button','copy','この項目は不要');omit.type='button';omit.onclick=()=>sendText('未解釈項目'+(index+1)+'不要');row.append(edit,omit);$('plan').append(row);
    for(const candidate of item.suggestions||[]){
      const suggest=el('button','copy','候補：'+candidate);suggest.type='button';
      suggest.onclick=()=>sendText('未解釈項目'+(index+1)+'='+candidate);row.append(suggest);
    }
  }
  if(!data.labels?.length)$('plan').textContent='依頼内容がここに表示されます。';
  const missing=data.missing||[];
  $('stepContent').classList.toggle('done',!!data.labels?.length);
  $('stepCodes').classList.toggle('done',!!data.labels?.length&&!missing.includes('引き継ぎコード')&&!missing.includes('認証番号'));
  $('stepRun').classList.toggle('done',data.kind==='started');
  $('runDraft').hidden=!(data.ready&&data.kind!=='started'&&!credentialsDirty&&!settingsDirty);
  confirmationId=data.confirmation_id||'';canConfirm=!!data.ready&&data.kind!=='started'&&!!confirmationId&&!credentialsDirty&&!settingsDirty;
}
function showSettings(settings){
  $('requestMode').value=settings.mode||'daiko';
  $('accountType').value=settings.account_type||'new';
  $('createCount').value=String(settings.count||1);
  $('creationFields').hidden=settings.mode!=='create';
  $('credentialsCard').hidden=settings.mode==='create';
  settingsDirty=false;
  $('settingsStatus').textContent=settings.mode==='create'?'新規作成では引き継ぎ情報は不要です。種別・個数と追加の変更内容を確認してください。':'既存アカウントの変更を受け付けます。';
}
function showCredentialStatus(credentials){
  if(!credentials)return;
  $('credentialsStatus').textContent='引き継ぎコード：'+(credentials.transfer_code?'登録済み':'未登録')+' ／ 認証番号：'+(credentials.auth_code?'登録済み':'未登録');
}
function busy(value){
  sending=value;
  for(const id of ['send','runDraft','applySettings','saveCredentials','requestMode','accountType','createCount','newRequest'])$(id).disabled=value;
}
function requireSettings(){
  if(!settingsDirty)return true;
  toast('先に「モード・作成設定を反映」を押してください。');return false;
}
function updateEngine(engine){
  $('engineStatus').replaceChildren(el('i'),document.createTextNode(engine.ready?'代行エンジン接続済み':engine.loading?'エンジン読み込み中':'エンジン未接続'));
  $('engineStatus').classList.toggle('error',!engine.ready&&!engine.loading);
  if(engine.error&&!engineErrorShown){message('代行エンジンを読み込めませんでした。\n'+engine.error+'\nファイルの配置と、元プロジェクトの依存パッケージを確認してください。','error');engineErrorShown=true;}
}
async function sendText(text){
  if(sending||!requireSettings())return;busy(true);
  // A retry of the same request retains its ID, so response loss cannot execute twice.
  // The server recognizes approval replies and binds them to this displayed review.
  const payload=lastRequest&&lastRequest.message===text?lastRequest:{message:text,confirmation_id:confirmationId||null,request_id:crypto.randomUUID()};
  lastRequest=payload;message(text,'user');
  try{
    const data=await api('/api/chat',payload);lastRequest=null;$('input').value='';showPlan(data);const bubble=message(data.message);
    if(data.kind==='draft'&&data.labels?.length)bubble.append(reviewBlock(data));
    if(data.kind==='started'){activeJobs.set(data.job_id,{bubble,status:''});pollJobs();}
  }catch(err){message(err.isServer?err.message:'通信を確認できません。同じ内容で送信し直すと、同じ送信IDで受付状況を確認します。','error');if(err.isServer){lastRequest=null;try{await refreshDraft();}catch{}}}
  finally{busy(false);}
}
async function confirmDraft(acknowledgement='確認して実行'){
  if(credentialsDirty){toast('変更した引き継ぎ情報を先に登録してください。');return;}
  if(sending||!requireSettings()||(!canConfirm&&!lastConfirmation))return;
  busy(true);
  const payload=lastConfirmation||{confirmation_id:confirmationId,request_id:crypto.randomUUID()};lastConfirmation=payload;
  message(acknowledgement,'user');
  try{
    const data=await api('/api/confirm',payload);lastConfirmation=null;lastRequest=null;$('input').value='';showPlan(data);
    const bubble=message(data.message);
    if(data.kind==='draft'&&data.labels?.length)bubble.append(reviewBlock(data));
    if(data.kind==='started'){activeJobs.set(data.job_id,{bubble,status:''});pollJobs();}
  }catch(err){
    message(err.isServer?err.message:'実行確認の応答を受け取れません。同じ確認ボタンで受付状況を確認できます。','error');
    if(err.isServer){lastConfirmation=null;try{await refreshDraft();}catch{}}
  }finally{busy(false);}
}
async function refreshDraft(){
  const data=await api('/api/bootstrap');if(data.settings)showSettings(data.settings);showPlan(data.draft||{});
  if(data.draft?.labels?.length)message(data.draft.message).append(reviewBlock(data.draft));
}
let polling=false;
async function pollJobs(){
  if(polling)return;polling=true;
  try{for(const [jid,view] of activeJobs){
    try{
      const job=await api('/api/job/'+jid);
      if(['done','error','interrupted'].includes(job.status)){
        view.bubble.replaceChildren(resultBlock(job));view.bubble.closest('.message').classList.toggle('error',job.status!=='done');activeJobs.delete(jid);$('stepRun').classList.toggle('done',job.status==='done');
      }else if(view.status!==job.log){view.bubble.firstChild.textContent=job.log||labels[job.status];view.status=job.log;}
    }catch{/* Keep polling after connection loss; never resend an execution. */}
  }}finally{polling=false;}
}
async function history(){
  try{const data=await api('/api/jobs');$('historyList').replaceChildren();
    if(!data.jobs.length)$('historyList').append(el('div','context-card','まだ実行履歴がありません。'));
    for(const job of data.jobs){const card=el('article','history-card'),head=el('div','history-head');head.append(el('span','',job.created_at?new Date(job.created_at*1000).toLocaleString('ja-JP'):'受付済み'),el('span','badge '+job.status,labels[job.status]||job.status));card.append(head,el('div','history-labels',(job.labels||[]).map(x=>x.label).join(' / ')),resultBlock(job));$('historyList').append(card);}
  }catch(err){toast(err.message);}
}
document.querySelectorAll('[data-view]').forEach(button=>button.onclick=()=>{
  const view=button.dataset.view;for(const name of ['chat','history','help'])$(name+'View').hidden=name!==view;
  document.querySelectorAll('.nav').forEach(x=>x.classList.toggle('active',x===button));$('pageLabel').textContent={chat:'代行チャット',history:'実行履歴',help:'使い方・対応メニュー'}[view];if(view==='history')history();
});
document.querySelectorAll('[data-example]').forEach(button=>button.onclick=()=>{$('input').value=button.dataset.example;$('input').focus();});
$('composer').onsubmit=event=>{event.preventDefault();const text=$('input').value.trim();if(text)sendText(text);};
$('input').onkeydown=event=>{if(event.key==='Enter'&&!event.shiftKey&&!event.isComposing){event.preventDefault();$('composer').requestSubmit();}};
$('runDraft').onclick=()=>confirmDraft();
$('newRequest').onclick=async()=>{if(sending)return;busy(true);try{await api('/api/reset',{});lastRequest=null;lastConfirmation=null;$('input').value='';$('transferCode').value='';$('authCode').value='';credentialsDirty=false;showCredentialStatus({});showPlan({});message('新しい依頼を始めます。代行内容を送ってください。');}catch(err){toast(err.message);}finally{busy(false);}};
$('refreshHistory').onclick=history;
for(const id of ['requestMode','accountType','createCount'])$(id).onchange=()=>{
  settingsDirty=true;canConfirm=false;$('runDraft').hidden=true;
  const creating=$('requestMode').value==='create';
  $('creationFields').hidden=!creating;$('credentialsCard').hidden=creating;
  $('settingsStatus').textContent='設定を変更しました。「モード・作成設定を反映」を押してください。';
};
$('settingsForm').onsubmit=async event=>{
  event.preventDefault();if(sending)return;busy(true);
  try{
    const data=await api('/api/settings',{mode:$('requestMode').value,account_type:$('accountType').value,count:Number($('createCount').value)});
    lastRequest=null;lastConfirmation=null;
    credentialsDirty=false;
    $('transferCode').value='';$('authCode').value='';
    showPlan(data);message(data.message).append(reviewBlock(data));
  }catch(err){toast(err.message);}finally{busy(false);}
};
$('credentialsForm').onsubmit=async event=>{
  event.preventDefault();if(sending||!requireSettings())return;busy(true);
  try{
    const data=await api('/api/credentials',{transfer_code:$('transferCode').value.normalize('NFKC').trim(),auth_code:$('authCode').value.normalize('NFKC').trim()});
    lastRequest=null;lastConfirmation=null;
    credentialsDirty=false;
    $('transferCode').value='';$('authCode').value='';$('showCodes').checked=false;
    $('transferCode').type='password';$('authCode').type='password';
    showPlan(data);message('引き継ぎ情報を登録しました。コードは履歴に表示しません。');
  }catch(err){toast(err.message);}finally{busy(false);}
};
$('showCodes').onchange=()=>{const type=$('showCodes').checked?'text':'password';$('transferCode').type=type;$('authCode').type=type;};
for(const id of ['transferCode','authCode'])$(id).oninput=()=>{credentialsDirty=true;canConfirm=false;$('runDraft').hidden=true;$('credentialsStatus').textContent='入力を変更しました。引き継ぎ情報を登録してください。';};
message('こんにちは。代行内容を送ってください。引き継ぎ情報は専用欄から登録できます。\n\n新規アカウントは上の利用モードを変更して作成できます。推測した表現・数量・対象を確認してから「確認して実行」を押してください。');
(async()=>{try{const data=await api('/api/bootstrap');csrf=data.csrf;showSettings(data.settings||{});updateEngine(data.engine);if(data.draft)showPlan(data.draft);for(const job of data.jobs){if(['pending','running','interrupted'].includes(job.status)){const bubble=message('前回の依頼を確認しています。');activeJobs.set(job.job_id,{bubble,status:''});}}pollJobs();await loadMenu();}catch(err){message('接続できませんでした。ログイン状態を確認してページを再読み込みしてください。','error');}})();
setInterval(pollJobs,1800);
setInterval(async()=>{try{updateEngine((await api('/api/status')).engine);}catch{}},5000);
async function loadMenu(){
  try{
    const data=await api('/api/menu'),wrapper=el('div','menu-scroll'),table=el('table','menu-table');
    const defaults=data.max_defaults,items=defaults.items,maxTable=el('table','menu-table');
    for(const [name,value] of [['キャラのMAX',defaults.character.base_level+'+'+defaults.character.plus_level],['猫缶',items.catfood],['XP',items.xp],['NP',items.np],['プラチケ・レジェチケ',items.platinum_tickets],['マタタビ・本能玉',items.matatabi],['その他アイテム',items.battle_items]]){
      const row=el('tr');row.append(el('th','',name),el('td','',typeof value==='number'?value.toLocaleString('ja-JP'):value));maxTable.append(row);
    }
    $('maxDefaults').replaceChildren(el('h3','','MAXの既定値'),maxTable);
    const head=el('thead'),hr=el('tr');for(const title of ['番号','項目','料金','Bot対応','入力'])hr.append(el('th','',title));head.append(hr);table.append(head);
    const body=el('tbody');let group='';
    for(const item of data.items){
      if(item.group!==group){group=item.group;const row=el('tr','menu-group'),cell=el('td','',group);cell.colSpan=5;row.append(cell);body.append(row);}
      const row=el('tr',item.status==='ready'?'':'pending');
      const price=item.price_jpy===null?Object.entries(item.class_prices_jpy||{}).map(([name,value])=>name+value+'円').join(' / '):item.price_jpy.toLocaleString('ja-JP')+'円'+(item.billing_unit==='件'?'':' / '+item.billing_unit);
      row.append(el('td','',item.number),el('td','',item.name),el('td','menu-price',price),el('td','',item.status==='ready'?(item.target_required?'接続済み・対象名必須':'接続済み'):'個別接続は未対応'));
      const cell=el('td'),button=el('button','','入力');button.type='button';button.onclick=()=>{document.querySelector('[data-view="chat"]').click();$('input').value=item.kind==='unlock_named'?'指定キャラ解放：':'メニュー'+item.number;$('input').focus();};cell.append(button);row.append(cell);body.append(row);
    }
    table.append(body);wrapper.append(table);$('menuCatalogue').replaceChildren(wrapper);
  }catch{$('menuCatalogue').textContent='定義表を取得できません。ページを再読み込みしてください。';}
}
