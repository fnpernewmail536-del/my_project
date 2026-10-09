(()=>{
  'use strict';
  function start(){
    if(document.getElementById('eva-command-deck'))return;
    const root=document.querySelector('body > .main,body > main,body > .box,body > .wrap,body > .app,body > .shell');
    if(!root)return;
    const en=document.documentElement.lang==='en';
    const amber=document.documentElement.classList.contains('theme-eva-amber');
    const deck=document.createElement('section');
    deck.id='eva-command-deck';deck.className='eva-command-deck';deck.setAttribute('data-i18n-skip','');
    deck.setAttribute('aria-label',en?'CPS console theme and Japanese clock':'CPSテーマ・日本標準時');
    deck.innerHTML=`<div class="eva-deck-cell"><div class="eva-overline">CAT PROXY SERVICE / INTERFACE</div><div class="eva-code">${amber?'CPS-00':'CPS-02'}</div><div class="eva-subcode eva-route"></div></div><div class="eva-deck-cell"><div class="eva-overline">${amber?'AMBER TERMINAL':'TACTICAL DISPLAY'}</div><div class="eva-deck-title">${en?'CONTROL<br>CONSOLE':'汎用型<br>操作端末'}</div><div class="eva-hexes" aria-hidden="true"><span class="eva-hex">C</span><span class="eva-hex">P</span><span class="eva-hex">S</span></div></div><div class="eva-deck-cell eva-deck-clock"><div><div class="eva-overline">${en?'JAPAN STANDARD TIME':'日本標準時'} / JST</div><time class="eva-clock"></time></div><svg class="eva-wave" viewBox="0 0 200 40" preserveAspectRatio="none" aria-hidden="true"><path d="M0 20C20 -8 35 -8 55 20S90 48 110 20S145 -8 165 20S190 35 200 20"/><path class="secondary" d="M0 24C20 -4 35 -4 55 24S90 52 110 24S145 -4 165 24S190 39 200 24"/></svg></div>`;
    deck.querySelector('.eva-route').textContent='DISPLAY / '+location.pathname;
    root.prepend(deck);
    const formatter=new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});
    const clock=deck.querySelector('time');
    const tick=()=>{const date=new Date();clock.textContent=formatter.format(date);clock.dateTime=date.toISOString();};
    tick();setInterval(()=>{if(!document.hidden)tick();},1000);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
