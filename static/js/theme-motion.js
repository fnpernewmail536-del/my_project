/* Full-screen HUD and live job scenes. Only the job API confirms completion. */
(()=>{
  'use strict';
  const root=document.documentElement;
  const eva=root.classList.contains('theme-eva')||root.classList.contains('theme-eva-amber');
  const minecraft=root.classList.contains('theme-classic');
  if(!eva&&!minecraft)return;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)'),en=root.lang==='en';
  const text=(ja,english)=>en?english:ja;
  const operation={daiko:text('代行','EDIT'),create:text('作成','CREATE'),clone:text('複製','CLONE')};
  const labels={connect:text('接続','CONNECT'),protect:text('保護','PROTECT'),create:text('作成','CREATE'),apply:text('適用','APPLY'),save:text('保存','SAVE')};
  const plans={daiko:['connect','protect','apply','save'],create:['create','apply','save'],clone:['connect','protect','create','save']};
  const historyKey='catps_motion_durations_v1',learnKey='catps_motion_samples_v2';
  const active=new Set(),owners=new WeakMap();let frame=0,lastTick=0,foreground=null,particles=0;
  const segments={0:'abcdef',1:'bc',2:'abdeg',3:'abcdg',4:'bcfg',5:'acdfg',6:'acdefg',7:'abc',8:'abcdefg',9:'abcdfg','-':'g'};
  const lines={a:'12,8 42,8',b:'49,15 49,41',c:'49,55 49,81',d:'12,88 42,88',e:'5,55 5,81',f:'5,15 5,41',g:'12,48 42,48'};
  const put=(el,value)=>{if(el&&el.textContent!==value)el.textContent=value;};
  const safeJSON=(key,fallback)=>{try{return JSON.parse(localStorage.getItem(key))||fallback;}catch{return fallback;}};
  const effect=()=>{try{const v=localStorage.getItem('catps_mc_effect_v1');return ['mine','tnt','creeper','auto'].includes(v)?v:'auto';}catch{return 'auto';}};
  function readHistory(){const value=safeJSON(historyKey,{});return value&&typeof value==='object'&&!Array.isArray(value)?value:{};}
  function samples(){const value=safeJSON(learnKey,[]);return Array.isArray(value)?value.filter(s=>['daiko','create','clone'].includes(s.tab)&&Number.isFinite(s.seconds)&&s.seconds>=1&&s.seconds<=7200&&Date.now()-s.at<90*86400000).slice(-60):[];}
  function estimate(tab,count){
    const observed=samples().filter(s=>s.tab===tab&&s.count===count).slice(-20).map(s=>s.seconds);
    const previous=readHistory()[`${tab}:${count}`];
    const values=observed.length?observed:Array.isArray(previous)?previous.filter(n=>Number.isFinite(n)&&n>=1&&n<=7200).slice(-8):[];
    if(values.length){const sorted=[...values].sort((a,b)=>a-b),median=sorted[Math.floor(sorted.length/2)];const valid=values.filter(n=>Math.abs(n-median)<=Math.max(2,median*.65));let sum=0,weights=0;valid.forEach((n,i)=>{const w=.94**(valid.length-1-i);sum+=n*w;weights+=w;});return {seconds:sum/weights,learned:true,n:values.length};}
    return {seconds:tab==='daiko'?90:tab==='create'?20+45*count:45+60*count,learned:false,n:0};
  }
  function remember(controller,seconds){
    if(!Number.isFinite(seconds)||seconds<1||seconds>7200)return;
    try{
      const old=readHistory(),next={};for(const [key,value] of Object.entries(old))if(/^(daiko|create|clone):[1-5]$/.test(key)&&Array.isArray(value))next[key]=value.filter(n=>Number.isFinite(n)&&n>=1&&n<=7200).slice(-8);
      const key=`${controller.tab}:${controller.count}`;next[key]=[...(next[key]||[]),Math.round(seconds*100)/100].slice(-8);localStorage.setItem(historyKey,JSON.stringify(next));
      localStorage.setItem(learnKey,JSON.stringify([...samples(),{tab:controller.tab,count:controller.count,profile:controller.timing?.profile||'',seconds:Math.round(seconds*100)/100,at:Date.now()}].slice(-60)));
    }catch{}
  }
  function format(seconds){const n=Math.max(0,Math.floor(seconds*100));return `${String(Math.min(99,Math.floor(n/6000))).padStart(2,'0')}:${String(Math.floor(n/100)%60).padStart(2,'0')}:${String(n%100).padStart(2,'0')}`;}
  function clock(className){
    const el=document.createElement('div');el.className=className;el.setAttribute('aria-hidden','true');
    for(let i=0;i<6;i++){if(i===2||i===4){const colon=document.createElement('span');colon.className='cps-clock-colon';colon.textContent=':';el.append(colon);}const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 54 96');svg.classList.add('cps-clock-digit');for(const [name,points] of Object.entries(lines)){const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');line.setAttribute('points',points);line.dataset.segment=name;svg.append(line);}el.append(svg);}return el;
  }
  function setClock(el,value){if(el.dataset.time===value)return;const compact=value.replaceAll(':','');[...el.querySelectorAll('svg')].forEach((digit,i)=>{const lit=segments[compact[i]]||'';for(const line of digit.children)line.classList.toggle('lit',lit.includes(line.dataset.segment));});el.dataset.time=value;}
  function burst(x,y,count=12){
    if(!minecraft||reduced.matches||document.hidden||particles>=64)return;
    const layer=document.createElement('div');layer.className='cps-particle-layer';layer.setAttribute('aria-hidden','true');const amount=Math.min(count,64-particles);particles+=amount;
    for(let i=0;i<amount;i++){const part=document.createElement('i');part.className='cps-block-fragment';const angle=Math.PI*2*i/amount,d=28+Math.random()*58;part.style.cssText=`left:${x}px;top:${y}px;--dx:${Math.cos(angle)*d}px;--dy:${Math.sin(angle)*d+38}px;--spin:${Math.round(Math.random()*180-90)}deg;--piece:${4+Math.random()*5}px;--shade:${['#76a83f','#4d7333','#92704a','#c2a26e'][i%4]}`;layer.append(part);}document.body.append(layer);setTimeout(()=>{layer.remove();particles=Math.max(0,particles-amount);},1100);
  }
  function schedule(){if(!frame&&active.size&&!document.hidden)frame=requestAnimationFrame(tick);}
  function tick(now){frame=0;if(document.hidden)return;if(now-lastTick>=33){for(const controller of active){controller.render();controller.draw();}lastTick=now;}schedule();}
  class JobMotion{
    constructor(box,options){
      this.box=box;this.tab=['daiko','create','clone'].includes(options.tab)?options.tab:'daiko';this.count=Math.max(1,Math.min(5,Math.trunc(Number(options.count)||1)));
      this.resumed=Boolean(options.resumed);this.preview=Boolean(options.preview);this.startedAt=Date.now();this.visualStarted=Date.now();this.total=this.preview?{seconds:8,learned:false,n:0}:estimate(this.tab,this.count);
      this.phase=this.preview?'running':this.resumed?'restoring':'starting';this.connected=true;this.closed=false;this.job=null;this.timing=null;this.scenes=[];
      this.mount();active.add(this);this.render();schedule();if(eva&&(!this.resumed||!foreground))this.expand();
    }
    mount(){
      this.panel=document.createElement('section');this.panel.className=`cps-job-motion ${eva?'cps-eva-limit':'cps-mc-mining'}`;this.panel.setAttribute('data-i18n-skip','');this.panel.setAttribute('aria-label',text('テーマ付き処理状況','Themed job status'));
      this.panel.innerHTML=eva?`<div class="cps-limit-top"><span class="cps-limit-label">${text('活動限界','ACTIVITY LIMIT')}</span><span class="cps-motion-state" role="status" aria-live="polite"></span></div><div class="cps-limit-main"><div class="cps-clock-slot" role="timer" aria-live="off"></div><div class="cps-limit-caption"></div></div><div class="cps-limit-rail" aria-hidden="true"><i></i></div><div class="cps-motion-report"><span class="cps-motion-mode"></span><span class="cps-motion-elapsed"></span></div><p class="cps-motion-note"></p>`:`<div class="cps-mc-world"><canvas class="cps-mc-canvas" aria-hidden="true"></canvas></div><div class="cps-mc-copy"><span class="cps-mc-title"></span><span class="cps-motion-state" role="status" aria-live="polite"></span><div class="cps-mc-slots" aria-hidden="true">${'<i></i>'.repeat(9)}</div><p class="cps-motion-note"></p></div>`;
      this.state=this.panel.querySelector('.cps-motion-state');this.note=this.panel.querySelector('.cps-motion-note');
      if(eva){this.timer=clock('cps-limit-clock');this.panel.querySelector('.cps-clock-slot').append(this.timer);}else if(window.CatPSBlockScene){this.scenes=this.scenes.filter(s=>s.canvas.isConnected);this.scenes.push(new window.CatPSBlockScene(this.panel.querySelector('canvas')));}
      const button=document.createElement('button');button.type='button';button.className='cps-motion-expand';button.textContent=text('全画面で演出を見る','OPEN FULL-SCREEN VIEW');button.addEventListener('click',()=>this.expand());this.panel.append(button);this.box.prepend(this.panel);
    }
    buildScreen(){
      if(this.screen)return;
      const dialog=document.createElement('dialog');this.screen=dialog;dialog.className=`cps-motion-screen ${eva?'cps-eva-cinema':'cps-mc-cinema'}`;dialog.setAttribute('data-i18n-skip','');dialog.setAttribute('aria-label',text('全画面の処理状況','Full-screen job status'));
      dialog.innerHTML=`<div class="cps-screen-grid" aria-hidden="true"></div><div class="cps-screen-ring" aria-hidden="true"></div><div class="cps-screen-shell"><header class="cps-screen-top"><span>CPS / ${eva?'TACTICAL OPERATIONS':'BLOCK OPERATIONS'}</span><button type="button" class="cps-screen-minimize">${text('小さくする','MINIMIZE')} <span aria-hidden="true">↙</span></button></header><main class="cps-screen-main"><div class="cps-screen-kicker">${eva?'INTERNAL POWER / ACTIVE':'SURVIVAL MODE / ACTIVE'}</div><h2 class="cps-screen-heading">${eva?text('活動限界','ACTIVITY LIMIT'):text('採掘オペレーション','MINING OPERATION')}</h2><div class="cps-screen-hero"></div><div class="cps-screen-caption"></div><div class="cps-screen-state" role="status" aria-live="polite"></div><div class="cps-screen-stages">${plans[this.tab].map(stage=>`<span data-stage="${stage}"><i></i>${labels[stage]}</span>`).join('')}</div><p class="cps-screen-log"></p></main><div class="cps-screen-readings"><div><span>${text('経過時間','ELAPSED')}</span><strong class="cps-screen-elapsed"></strong></div><div><span>${text('実測・学習','MEASURED RUNS')}</span><strong class="cps-screen-learning"></strong></div><div><span>${text('実測時間のばらつき','HISTORICAL SPREAD')}</span><strong class="cps-screen-accuracy"></strong></div></div><footer class="cps-screen-footer"><span class="cps-screen-note"></span><span>${text('小さくしても処理は続きます','Minimizing keeps the job running')}</span></footer></div><div class="cps-screen-complete" aria-hidden="true"><span>${text('作業完了','MISSION COMPLETE')}</span><small>SERVER CONFIRMED / ALL SYSTEMS CLEAR</small></div>`;
      if(this.preview){put(dialog.querySelector('.cps-screen-complete span'),text('プレビュー終了','PREVIEW FINISHED'));put(dialog.querySelector('.cps-screen-complete small'),'NO OPERATION / NO SERVER REQUEST');}
      if(eva){this.screenTimer=clock('cps-screen-clock');dialog.querySelector('.cps-screen-hero').append(this.screenTimer);}else{const canvas=document.createElement('canvas');canvas.className='cps-mc-screen-canvas';canvas.setAttribute('aria-hidden','true');dialog.querySelector('.cps-screen-hero').append(canvas);if(window.CatPSBlockScene)this.scenes.push(new window.CatPSBlockScene(canvas));}
      dialog.querySelector('.cps-screen-minimize').addEventListener('click',()=>this.minimize());dialog.addEventListener('cancel',event=>{event.preventDefault();this.minimize();});document.body.append(dialog);
    }
    expand(){
      this.buildScreen();if(foreground&&foreground!==this)foreground.minimize();foreground=this;
      if(!this.screen.open)this.screen.showModal();document.body.classList.add('cps-screen-open');
      if(this.closed){this.celebrationAt=Date.now()-this.visualStarted;for(const scene of this.scenes)scene.exploded=false;active.add(this);this.stopAfter(2400);}
      this.render();this.draw();schedule();
    }
    minimize(){if(this.screen?.open)this.screen.close();if(foreground===this){foreground=null;document.body.classList.remove('cps-screen-open');}this.panel?.querySelector('button')?.focus({preventScroll:true});}
    update(job){
      if(this.closed)return;this.job=job;this.connected=true;
      const timestamp=Number(job.started_at||job.created_at)*1000;if(Number.isFinite(timestamp)&&timestamp>0&&Math.abs(Date.now()-timestamp)<7200000)this.startedAt=timestamp;
      const timing=job.timing;if(timing&&Number.isFinite(timing.elapsed_seconds)&&timing.elapsed_seconds>=0&&timing.elapsed_seconds<=7200){this.timing=timing;this.timingAt=Date.now();if(timing.estimate_source!=='initial'&&Number.isFinite(timing.estimated_total_seconds))this.total={seconds:timing.estimated_total_seconds,learned:true,n:timing.sample_count||0};}
      this.phase=job.status==='pending'?'waiting':'running';this.render();
    }
    pause(){if(!this.closed){this.connected=false;this.render();}}
    elapsed(){return this.timing?this.timing.elapsed_seconds+(this.closed||this.job?.status!=='running'?0:Math.max(0,(Date.now()-this.timingAt)/1000)):Math.max(0,(Date.now()-this.startedAt)/1000);}
    remaining(){if(this.preview)return Math.max(0,8-this.elapsed());if(this.timing){if(!Number.isFinite(this.timing.remaining_seconds))return null;if(this.timing.estimate_source!=='initial'||!this.total.learned)return Math.max(0,this.timing.remaining_seconds-Math.max(0,(Date.now()-this.timingAt)/1000));}return Math.max(0,this.total.seconds-this.elapsed());}
    render(){
      if(!this.panel.isConnected){if(this.closed)return;this.dispose();return;}
      const elapsed=this.closed?this.finishedElapsed:this.elapsed(),remaining=this.remaining(),waiting=['starting','restoring','waiting'].includes(this.phase),exceeded=!this.closed&&!waiting&&this.connected&&(remaining===null||remaining<=0);
      const state=this.phase==='done'?text('処理完了','COMPLETE'):this.phase==='error'?text('処理エラー','ERROR'):this.phase==='unknown'?text('状態確認停止','CHECK STOPPED'):!this.connected?text('通信確認中','RECONNECTING'):this.phase==='starting'?text('受付確認中','SUBMITTING'):this.phase==='restoring'?text('処理状況を復元中','RESTORING'):waiting?text('サーバーの開始待ち','QUEUED'):exceeded?text('処理継続中','STILL PROCESSING'):text('処理中','PROCESSING');
      put(this.state,state);this.panel.dataset.phase=this.phase;this.panel.classList.toggle('cps-motion-unavailable',!this.connected||waiting||this.phase==='unknown');this.panel.classList.toggle('cps-motion-extended',exceeded);
      const value=this.phase==='unknown'?'--:--:--':this.closed?format(elapsed):(!this.connected||waiting||exceeded)?'--:--:--':format(remaining);
      const caption=this.preview?text('演出プレビュー','ANIMATION PREVIEW'):this.phase==='unknown'?text('完了は未確認','UNCONFIRMED'):this.closed?text('所要時間','ELAPSED TIME'):text('完了までの予測','ESTIMATED REMAINING');
      if(eva){setClock(this.timer,value);put(this.panel.querySelector('.cps-limit-caption'),caption);this.timer.parentElement.setAttribute('aria-label',caption+' '+value);put(this.panel.querySelector('.cps-motion-mode'),`MODE / ${operation[this.tab]}${this.count>1?' ×'+this.count:''}`);put(this.panel.querySelector('.cps-motion-elapsed'),`${text('経過','ELAPSED')} / ${format(elapsed).slice(0,5)}`);}else put(this.panel.querySelector('.cps-mc-title'),this.phase==='done'?text('ブロック破壊完了！','BLOCK BROKEN!'):this.closed?text('状況を確認してください','CHECK JOB STATUS'):text('ブロックを採掘中','MINING BLOCKS'));
      const note=this.preview?(this.closed?text('プレビュー終了。実際の処理は行っていません。','Preview finished. No operation was performed.'):text('8秒間の演出プレビューです。実際の処理は行いません。','8-second animation preview. No operation will be performed.')):this.phase==='done'?text('サーバーから完了通知を受信しました。','Completion confirmed by the server.'):this.phase==='error'?text('詳しい内容は下のエラー表示を確認してください。','See the error details below.'):this.phase==='unknown'?text('完了は確認できていません。下の案内を確認してください。','Completion is unconfirmed. See the message below.'):!this.connected?text('通信を確認しています。サーバーでは処理が続いている可能性があります。','Checking the connection. Server processing may continue.'):waiting?text('開始状態を確認しています。','Checking the server status.'):exceeded?text('現在の段階が予測を超えています。完了はサーバー通知で確認します。','This phase exceeded its guide. Waiting for server confirmation.'):this.timing?.estimate_source==='current_run'?text('今回の完了済み件数の実測で予測を補正しています。','Recalibrated from completed units in this job.'):this.total.learned?text('過去の実測と現在の処理段階から予測しています。','Estimated from measured runs and the current phase.'):text('初回は目安です。実測が増えると予測を改善します。','First-run guide. More measured runs improve the estimate.');
      put(this.note,note);
      if(this.screen){
        this.screen.dataset.phase=this.phase;this.screen.classList.toggle('cps-motion-unavailable',!this.connected||waiting);if(eva)setClock(this.screenTimer,value);
        put(this.screen.querySelector('.cps-screen-state'),state);put(this.screen.querySelector('.cps-screen-caption'),caption);put(this.screen.querySelector('.cps-screen-elapsed'),format(elapsed).slice(0,5));put(this.screen.querySelector('.cps-screen-log'),this.preview?'PREVIEW / NO OPERATION':this.job?.log||text('開始応答を確認しています…','Checking the start response…'));put(this.screen.querySelector('.cps-screen-note'),note);
        const n=this.timing?.sample_count||this.total.n||0;put(this.screen.querySelector('.cps-screen-learning'),this.preview?'DEMO':this.timing?.estimate_source==='current_run'?`${this.timing.completed_units}/${this.timing.total_units} ${text('件の実測','MEASURED')}`:`${n} ${text('件','RUNS')}`);
        const error=this.timing?.historical_error_seconds;put(this.screen.querySelector('.cps-screen-accuracy'),this.preview?'—':Number.isFinite(error)?`±${Math.ceil(error)} ${text('秒','SEC')}`:text('学習中','LEARNING'));
        const stage=this.timing?.stage;const index=plans[this.tab].indexOf(stage);for(const el of this.screen.querySelectorAll('[data-stage]')){const i=plans[this.tab].indexOf(el.dataset.stage);el.classList.toggle('current',!this.closed&&i===index);el.classList.toggle('passed',this.phase==='done'||index>=0&&i<index);}
      }
    }
    draw(){
      if(!minecraft)return;const ms=Date.now()-this.visualStarted;
      for(const scene of this.scenes){const canvas=scene.canvas;if(!canvas.isConnected||canvas.closest('dialog')&&!canvas.closest('dialog').open)continue;const r=canvas.getBoundingClientRect();if(r.bottom<0||r.top>innerHeight||!r.width)continue;scene.draw(ms,{phase:this.phase,effect:effect(),reduced:reduced.matches,finishedMs:this.celebrationAt??null,available:this.connected&&!['starting','waiting','restoring','error','unknown'].includes(this.phase)});}
    }
    stopAfter(ms){clearTimeout(this.stopTimer);this.stopTimer=setTimeout(()=>{active.delete(this);if(this.closed&&this.screen?.open)this.minimize();if(!active.size&&frame){cancelAnimationFrame(frame);frame=0;}},ms);}
    finish(result){
      if(this.closed)return;this.finishedElapsed=this.elapsed();this.closed=true;this.connected=true;this.phase=result==='done'?'done':this.job?.status==='error'?'error':'unknown';this.celebrationAt=Date.now()-this.visualStarted;
      if(!this.panel.isConnected)this.mount();this.render();this.draw();
      if(this.phase==='done'&&!this.resumed&&!this.preview)remember(this,this.finishedElapsed);
      if(this.phase==='done'&&minecraft){const r=this.panel.getBoundingClientRect();burst(r.left+r.width/2,r.top+80,24);}
      this.stopAfter(this.phase==='done'?2400:700);schedule();
    }
    dispose(){active.delete(this);clearTimeout(this.stopTimer);this.minimize();this.screen?.remove();}
  }
  window.CatPSMotion={begin:(box,options={})=>{if(!box)return null;owners.get(box)?.dispose();const controller=new JobMotion(box,options);owners.set(box,controller);return controller;}};
  document.addEventListener('visibilitychange',()=>{if(document.hidden){if(frame)cancelAnimationFrame(frame);frame=0;}else{for(const controller of active)controller.render();schedule();}});
  function boot(){
    document.body.classList.add('cps-motion-ready');const previewButton=document.getElementById('cps-motion-preview');
    const select=document.getElementById('cps-mc-effect');if(select){select.value=effect();select.addEventListener('change',()=>{try{localStorage.setItem('catps_mc_effect_v1',select.value);}catch{}for(const controller of active){for(const scene of controller.scenes)scene.exploded=false;controller.draw();}});}
    previewButton?.addEventListener('click',()=>{const box=document.getElementById('cps-motion-preview-result');if(!box)return;previewButton.disabled=true;box.hidden=false;box.replaceChildren();const controller=window.CatPSMotion.begin(box,{tab:'create',count:1,preview:true});setTimeout(()=>{controller.finish('done');previewButton.disabled=false;},8000);});
    if(minecraft)document.addEventListener('click',event=>{const target=event.target instanceof Element?event.target.closest('button,.btn,.opt,.tab'):null;if(!target||target.disabled||target.getAttribute('aria-disabled')==='true')return;const r=target.getBoundingClientRect();burst(event.detail>0?event.clientX:r.left+r.width/2,event.detail>0?event.clientY:r.top+r.height/2);},{passive:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
