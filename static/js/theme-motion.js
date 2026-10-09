/* CPS theme effects. Job completion always comes from the existing job API. */
(()=>{
  'use strict';
  const root=document.documentElement;
  const eva=root.classList.contains('theme-eva')||root.classList.contains('theme-eva-amber');
  const minecraft=root.classList.contains('theme-classic');
  if(!eva&&!minecraft)return;
  const reduced=matchMedia('(prefers-reduced-motion: reduce)');
  const en=root.lang==='en';
  const text=(ja,english)=>en?english:ja;
  const operation={daiko:text('代行','Edit'),create:text('作成','Create'),clone:text('複製','Clone')};
  const historyKey='catps_motion_durations_v1';
  const active=new Set();
  let frame=0,lastTick=0,particles=0;
  const segments={0:'abcdef',1:'bc',2:'abdeg',3:'abcdg',4:'bcfg',5:'acdfg',6:'acdefg',7:'abc',8:'abcdefg',9:'abcdfg','-':'g'};
  const lines={a:'12,8 42,8',b:'49,15 49,41',c:'49,55 49,81',d:'12,88 42,88',e:'5,55 5,81',f:'5,15 5,41',g:'12,48 42,48'};
  function putText(element,value){if(element.textContent!==value)element.textContent=value;}

  function readHistory(){
    try{const data=JSON.parse(localStorage.getItem(historyKey)||'{}');return data&&typeof data==='object'&&!Array.isArray(data)?data:{};}catch{return {};}
  }
  function estimate(tab,count){
    const samples=readHistory()[`${tab}:${count}`];
    const valid=Array.isArray(samples)?samples.filter(n=>Number.isFinite(n)&&n>=1&&n<=7200).slice(-8).sort((a,b)=>a-b):[];
    if(valid.length)return {seconds:valid[Math.floor(valid.length/2)],learned:true};
    // First-run estimates are explicitly labeled as a guide, not server deadlines.
    return {seconds:tab==='daiko'?90:tab==='create'?20+45*count:45+60*count,learned:false};
  }
  function remember(tab,count,seconds){
    if(!Number.isFinite(seconds)||seconds<1||seconds>7200)return;
    try{
      const before=readHistory(),next={};
      for(const [key,value] of Object.entries(before)){
        if(/^(daiko|create|clone):[1-5]$/.test(key)&&Array.isArray(value))next[key]=value.filter(n=>Number.isFinite(n)&&n>=1&&n<=7200).slice(-8);
      }
      const key=`${tab}:${count}`;
      next[key]=[...(next[key]||[]),Math.round(seconds*100)/100].slice(-8);
      localStorage.setItem(historyKey,JSON.stringify(next));
    }catch{}
  }
  function format(seconds){
    const centiseconds=Math.max(0,Math.floor(seconds*100));
    const minutes=Math.min(99,Math.floor(centiseconds/6000));
    return `${String(minutes).padStart(2,'0')}:${String(Math.floor(centiseconds/100)%60).padStart(2,'0')}:${String(centiseconds%100).padStart(2,'0')}`;
  }
  function burst(x,y,count=12){
    if(!minecraft||reduced.matches||document.hidden||particles>=64)return;
    const layer=document.createElement('div');layer.className='cps-particle-layer';layer.setAttribute('aria-hidden','true');
    const amount=Math.min(count,64-particles);particles+=amount;
    for(let i=0;i<amount;i++){
      const part=document.createElement('i');part.className='cps-block-fragment';
      const angle=Math.PI*2*i/amount,distance=28+Math.random()*58;
      part.style.cssText=`left:${x}px;top:${y}px;--dx:${Math.cos(angle)*distance}px;--dy:${Math.sin(angle)*distance+38}px;--spin:${Math.round(Math.random()*180-90)}deg;--piece:${4+Math.random()*5}px;--shade:${['#76a83f','#4d7333','#92704a','#c2a26e'][i%4]}`;
      layer.append(part);
    }
    document.body.append(layer);
    setTimeout(()=>{layer.remove();particles-=amount;},1100);
  }
  function schedule(){if(!frame&&active.size&&!document.hidden)frame=requestAnimationFrame(tick);}
  function tick(now){
    frame=0;
    if(document.hidden)return;
    if(now-lastTick>=100){for(const controller of active)controller.render();lastTick=now;}
    schedule();
  }
  function makeTimer(){
    const timer=document.createElement('div');timer.className='cps-limit-clock';timer.setAttribute('aria-hidden','true');
    for(let i=0;i<6;i++){
      if(i===2||i===4){const colon=document.createElement('span');colon.className='cps-clock-colon';colon.textContent=':';timer.append(colon);}
      const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');
      svg.setAttribute('viewBox','0 0 54 96');svg.classList.add('cps-clock-digit');
      for(const [name,points] of Object.entries(lines)){
        const line=document.createElementNS('http://www.w3.org/2000/svg','polyline');
        line.setAttribute('points',points);line.dataset.segment=name;svg.append(line);
      }
      timer.append(svg);
    }
    return timer;
  }
  class JobMotion{
    constructor(box,options){
      this.box=box;this.tab=['daiko','create','clone'].includes(options.tab)?options.tab:'daiko';
      this.count=Math.max(1,Math.min(5,Math.trunc(Number(options.count)||1)));
      this.resumed=Boolean(options.resumed);this.preview=Boolean(options.preview);this.startedAt=Date.now();this.total=this.preview?{seconds:8,learned:false}:estimate(this.tab,this.count);
      this.phase=this.preview?'running':this.resumed?'restoring':'starting';this.connected=true;this.closed=false;this.job=null;
      this.mount();active.add(this);this.render();schedule();
    }
    mount(){
      this.panel=document.createElement('section');
      this.panel.className=`cps-job-motion ${eva?'cps-eva-limit':'cps-mc-mining'}`;
      this.panel.setAttribute('data-i18n-skip','');this.panel.setAttribute('aria-label',text('テーマ付き処理状況','Themed job status'));
      if(eva){
        this.panel.innerHTML=`<div class="cps-limit-top"><span class="cps-limit-label">${text('活動限界','ACTIVITY LIMIT')}</span><span class="cps-motion-state" role="status" aria-live="polite"></span></div><div class="cps-limit-main"><div class="cps-clock-slot"></div><div class="cps-limit-caption"></div></div><div class="cps-limit-rail" aria-hidden="true"><i></i></div><div class="cps-motion-report"><span class="cps-motion-mode"></span><span class="cps-motion-elapsed"></span></div><p class="cps-motion-note"></p>`;
        this.timer=makeTimer();const slot=this.panel.querySelector('.cps-clock-slot');slot.setAttribute('role','timer');slot.setAttribute('aria-live','off');slot.append(this.timer);
        this.digits=[...this.timer.querySelectorAll('svg')];
      }else{
        this.panel.innerHTML=`<div class="cps-mc-workbench" aria-hidden="true"><div class="cps-mc-block"><div class="cps-mc-grass"></div><svg class="cps-mc-cracks" viewBox="0 0 80 80"><path d="M40 0L40 18L29 29L42 40L38 58L23 70L23 80M80 24L59 24L42 40L17 40L0 54M38 58L56 60L69 80M29 29L14 15L0 15"/></svg></div><div class="cps-mc-pick"></div><div class="cps-mc-chips">${Array.from({length:8},(_,i)=>`<i style="--n:${i}"></i>`).join('')}</div></div><div class="cps-mc-copy"><span class="cps-mc-title">${text('ブロックを採掘中','MINING BLOCKS')}</span><span class="cps-motion-state" role="status" aria-live="polite"></span><div class="cps-mc-slots" aria-hidden="true">${'<i></i>'.repeat(9)}</div><p class="cps-motion-note"></p></div>`;
      }
      this.state=this.panel.querySelector('.cps-motion-state');this.note=this.panel.querySelector('.cps-motion-note');
      this.box.prepend(this.panel);this.lastDigits='';this.lastState='';
    }
    update(job){
      if(this.closed)return;
      this.job=job;this.connected=true;
      const timestamp=Number(job.started_at||job.created_at)*1000;
      if(Number.isFinite(timestamp)&&timestamp>0&&Math.abs(Date.now()-timestamp)<7200000)this.startedAt=timestamp;
      this.phase=job.status==='pending'?'waiting':'running';this.render();
    }
    pause(){if(!this.closed){this.connected=false;this.render();}}
    elapsed(){return Math.max(0,(Date.now()-this.startedAt)/1000);}
    setDigits(value){
      const compact=value.replaceAll(':','');
      for(let i=0;i<this.digits.length;i++){
        if(compact[i]===this.lastDigits[i])continue;
        const lit=segments[compact[i]]||'';
        for(const line of this.digits[i].children)line.classList.toggle('lit',lit.includes(line.dataset.segment));
      }
      this.lastDigits=compact;
      this.timer.setAttribute('data-time',value);
    }
    render(){
      if(!this.panel.isConnected){if(this.closed)return;active.delete(this);return;}
      const elapsed=this.closed?this.finishedElapsed:this.elapsed();
      const remaining=Math.max(0,this.total.seconds-elapsed);
      const waiting=['restoring','waiting'].includes(this.phase);
      const exceeded=!this.closed&&!waiting&&this.connected&&remaining<=0;
      let state=this.phase==='done'?text('処理完了','COMPLETE'):this.phase==='error'?text('処理エラー','ERROR'):this.phase==='unknown'?text('状態確認停止','CHECK STOPPED'):!this.connected?text('通信確認中','RECONNECTING'):this.phase==='restoring'?text('処理状況を復元中','RESTORING'):waiting?text('サーバーの開始待ち','QUEUED'):exceeded?text('処理継続中','STILL PROCESSING'):text('処理中','PROCESSING');
      if(state!==this.lastState){this.state.textContent=state;this.lastState=state;}
      this.panel.dataset.phase=this.phase;this.panel.classList.toggle('cps-motion-unavailable',!this.connected||waiting||this.phase==='unknown');
      this.panel.classList.toggle('cps-motion-extended',exceeded);
      if(eva){
        const value=this.phase==='unknown'?'--:--:--':this.closed?format(elapsed):(!this.connected||waiting||exceeded)?'--:--:--':format(remaining);
        this.setDigits(value);
        const caption=this.panel.querySelector('.cps-limit-caption');
        putText(caption,this.preview?text('演出プレビュー','ANIMATION PREVIEW'):this.phase==='unknown'?text('完了は未確認','UNCONFIRMED'):this.closed?text('所要時間','ELAPSED TIME'):text('完了までの予測','ESTIMATED REMAINING'));
        this.timer.parentElement.setAttribute('aria-label',caption.textContent+' '+value);
        putText(this.panel.querySelector('.cps-motion-mode'),`MODE / ${operation[this.tab]}${this.count>1?' ×'+this.count:''}`);
        putText(this.panel.querySelector('.cps-motion-elapsed'),`${text('経過','ELAPSED')} / ${format(elapsed).slice(0,5)}`);
      }else{
        putText(this.panel.querySelector('.cps-mc-title'),this.phase==='done'?text('ブロック破壊完了！','BLOCK BROKEN!'):this.closed?text('状況を確認してください','CHECK JOB STATUS'):text('ブロックを採掘中','MINING BLOCKS'));
      }
      const note=this.preview?(this.closed?text('プレビュー終了。実際の処理は行っていません。','Preview finished. No operation was performed.'):text('8秒間の演出プレビューです。実際の処理は行いません。','8-second animation preview. No operation will be performed.')):this.phase==='done'?text('サーバーから完了通知を受信しました。','Completion confirmed by the server.'):this.phase==='error'?text('詳しい内容は下のエラー表示を確認してください。','See the error details below.'):this.phase==='unknown'?text('完了は確認できていません。下の案内を確認してください。','Completion is unconfirmed. See the message below.'):!this.connected?text('通信を確認しています。サーバーでは処理が続いている可能性があります。','Checking the connection. Server processing may continue.'):waiting?text('開始状態を確認しています。','Checking the server status.'):exceeded?text('予測時間を超えています。完了通知を待っています。','The estimate has been exceeded. Waiting for confirmation.'):eva?(this.total.learned?text('このブラウザの過去の所要時間から予測しています。','Estimated from previous runs in this browser.'):text('初回は目安です。通信や設定により前後します。','First-run guide. Network and settings affect duration.')):text('完了するとブロックが砕けて結果を表示します。','The block breaks when the server confirms completion.');
      if(this.note.textContent!==note)this.note.textContent=note;
    }
    finish(result){
      if(this.closed)return;
      this.finishedElapsed=this.elapsed();this.closed=true;this.connected=true;
      this.phase=result==='done'?'done':this.job?.status==='error'?'error':'unknown';
      active.delete(this);
      if(!this.panel.isConnected)this.mount();
      this.render();
      if(this.phase==='done'){
        if(!this.resumed&&!this.preview)remember(this.tab,this.count,this.finishedElapsed);
        if(minecraft){const r=this.panel.getBoundingClientRect();burst(r.left+Math.min(68,r.width/2),r.top+r.height/2,24);}
      }
      if(!active.size&&frame){cancelAnimationFrame(frame);frame=0;}
    }
  }
  window.CatPSMotion={begin:(box,options={})=>box?new JobMotion(box,options):null};
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden){if(frame)cancelAnimationFrame(frame);frame=0;}
    else{for(const controller of active)controller.render();schedule();}
  });
  reduced.addEventListener?.('change',()=>{document.querySelectorAll('.cps-particle-layer').forEach(layer=>layer.remove());});
  function boot(){
    document.body.classList.add('cps-motion-ready');
    const previewButton=document.getElementById('cps-motion-preview');
    previewButton?.addEventListener('click',()=>{
      const box=document.getElementById('cps-motion-preview-result');if(!box)return;
      previewButton.disabled=true;box.hidden=false;box.replaceChildren();
      const controller=new JobMotion(box,{tab:'create',count:1,preview:true});
      setTimeout(()=>{controller.finish('done');previewButton.disabled=false;},8000);
    });
    if(minecraft&&!reduced.matches){
      [...document.querySelectorAll('body > .main .card,body > main .card,body > main .box')].filter(el=>el.getBoundingClientRect().top<innerHeight).slice(0,5).forEach((el,i)=>{el.classList.add('cps-motion-enter');el.style.setProperty('--enter-delay',`${i*65}ms`);});
    }
    if(minecraft)document.addEventListener('click',event=>{
      const target=event.target instanceof Element?event.target.closest('button,.btn,.opt,.tab'):null;
      if(!target||target.disabled||target.getAttribute('aria-disabled')==='true')return;
      const r=target.getBoundingClientRect();
      const pointer=event.detail>0;
      burst(pointer?event.clientX:r.left+r.width/2,pointer?event.clientY:r.top+r.height/2);
    },{passive:true});
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})();
