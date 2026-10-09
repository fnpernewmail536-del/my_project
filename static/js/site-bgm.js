(()=>{
  'use strict';
  if(window.CatPSBGM)return;
  const KEY='catps_bgm_v1';
  const en=document.documentElement.lang==='en';
  const text=(ja,english)=>en?english:ja;
  const audio=new Audio();audio.preload='none';audio.loop=true;
  function readPrefs(raw){
    const value={track:'',volume:.3,loop:true,time:0};
    try{const saved=JSON.parse(raw);if(saved&&typeof saved==='object'){
      if(typeof saved.track==='string')value.track=saved.track;
      if(Number.isFinite(saved.volume))value.volume=Math.max(0,Math.min(1,saved.volume));
      if(typeof saved.loop==='boolean')value.loop=saved.loop;
      if(Number.isFinite(saved.time)&&saved.time>=0)value.time=saved.time;
    }}catch{}return value;
  }
  let prefs=readPrefs(null);try{prefs=readPrefs(localStorage.getItem(KEY));}catch{}
  audio.volume=prefs.volume;audio.loop=prefs.loop;
  let tracks=[],catalogLoaded=false,catalogTask=null,objectURL='',localName='',sourceName='',pending=false,generation=0,lastSaved=0,resumeAt=0,sourceReady=false;
  let message=text('曲を選んで再生を押してください。','Choose a track and press Play.');
  const panels=[];
  function save(){try{localStorage.setItem(KEY,JSON.stringify(prefs));return true;}catch{return false;}}
  function setMessage(value){message=value;update();}
  function option(value,label){const el=document.createElement('option');el.value=value;el.textContent=label;return el;}
  function update(){
    for(const panel of panels){
      const select=panel.querySelector('[data-bgm-track]');
      if(select.dataset.catalog!==String(tracks.length)+'|'+localName){
        select.replaceChildren(option('',text('曲を選択','Select track')));
        for(const track of tracks)select.appendChild(option(track.name,track.title));
        if(objectURL)select.appendChild(option('__local__',text('端末の曲：','Local: ')+localName));
        select.dataset.catalog=String(tracks.length)+'|'+localName;
      }
      select.value=objectURL?'__local__':prefs.track;
      const playing=!audio.paused;
      const play=panel.querySelector('[data-bgm-play]');
      play.textContent=pending?text('読み込み中…','Loading…'):playing?text('一時停止','Pause'):text('再生','Play');
      play.disabled=pending||(!objectURL&&!tracks.some(t=>t.name===prefs.track));
      play.setAttribute('aria-pressed',String(playing));
      panel.querySelector('[data-bgm-stop]').disabled=!sourceName&&!pending;
      panel.querySelector('[data-bgm-volume]').value=String(Math.round(prefs.volume*100));
      panel.querySelector('output').textContent=Math.round(prefs.volume*100)+'%';
      panel.querySelector('[data-bgm-loop]').checked=prefs.loop;
      panel.querySelector('[data-bgm-status]').textContent=message;
    }
  }
  function clearAudio(){generation++;pending=false;resumeAt=0;sourceReady=false;audio.pause();sourceName='';audio.removeAttribute('src');audio.load();}
  function resetLocal(){if(objectURL)URL.revokeObjectURL(objectURL);objectURL='';localName='';}
  function stop(){clearAudio();prefs.time=0;save();setMessage(text('停止しました。','Stopped.'));}
  async function play(){
    if(pending)return;
    if(!audio.paused){audio.pause();prefs.time=audio.currentTime||0;save();setMessage(text('一時停止中です。','Paused.'));return;}
    const found=tracks.find(t=>t.name===prefs.track);
    const url=objectURL||(found&&found.url);
    if(!url){setMessage(text('先に曲を選択してください。','Select a track first.'));return;}
    if(sourceName!==url){resumeAt=objectURL?0:prefs.time;sourceReady=false;audio.src=url;sourceName=url;audio.load();}
    const operation=++generation;pending=true;setMessage(text('読み込み中…','Loading…'));
    try{
      await audio.play();
      if(operation!==generation)return;
      pending=false;setMessage(text('再生中：','Playing: ')+(objectURL?localName:found.title));
    }catch(error){
      if(operation!==generation)return;
      pending=false;
      setMessage(error.name==='NotAllowedError'?text('ブラウザが再生を止めました。もう一度「再生」を押してください。','Playback was blocked. Press Play again.'):text('曲を再生できません。ファイルの形式と接続を確認してください。','Cannot play this track. Check its format and connection.'));
    }
  }
  async function loadCatalog(){
    if(catalogTask)return catalogTask;
    if(catalogLoaded)return;
    catalogTask=(async()=>{
      const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),10000);
      try{
        const response=await fetch('/api/theme/bgm',{cache:'no-store',credentials:'same-origin',signal:controller.signal});
        if(!response.ok)throw new Error('catalog');
        const data=await response.json();
        tracks=(Array.isArray(data.tracks)?data.tracks:[]).filter(t=>{
          if(typeof t.name!=='string'||typeof t.url!=='string')return false;
          try{const u=new URL(t.url,location.origin);return u.origin===location.origin&&u.pathname.startsWith('/static/bgm/');}catch{return false;}
        }).map(t=>({name:t.name,title:typeof t.title==='string'?t.title:t.name,url:t.url}));
        catalogLoaded=true;
        if(prefs.track&&!tracks.some(t=>t.name===prefs.track)){prefs.track='';prefs.time=0;save();}
        if(!tracks.length&&!objectURL)setMessage(text('BGMはまだありません。端末の曲を試せます。','No BGM tracks yet. You can try a local file.'));
        else if(prefs.time>0)setMessage(text('前回の続きから再生できます。','Press Play to resume your track.'));
        else update();
      }catch{setMessage(text('曲一覧を取得できません。設定を開き直して再試行してください。','Could not load tracks. Reopen settings to retry.'));}
      finally{clearTimeout(timer);catalogTask=null;}
    })();return catalogTask;
  }
  function mount(root){
    const panel=document.createElement('section');panel.className='bgm-controls';panel.setAttribute('data-i18n-skip','');
    panel.innerHTML=`<h3>BGM / AUDIO</h3><label>${text('曲','Track')}<select data-bgm-track aria-label="${text('BGMの曲','BGM track')}"></select></label><div class="bgm-buttons"><button class="bgm-button" type="button" data-bgm-play aria-pressed="false">${text('再生','Play')}</button><button class="bgm-button" type="button" data-bgm-stop>${text('停止','Stop')}</button></div><label>${text('音量','Volume')}<span class="bgm-volume-row"><input data-bgm-volume type="range" min="0" max="100" step="1" aria-label="${text('BGM音量','BGM volume')}"><output></output></span></label><label class="bgm-loop"><input data-bgm-loop type="checkbox">${text('繰り返し再生','Loop track')}</label><label class="bgm-local">${text('端末の音楽ファイルを試す','Try an audio file on this device')}<input data-bgm-local type="file" accept="audio/*,.mp3,.ogg,.wav,.m4a,.aac,.flac" aria-label="${text('端末のBGMファイル','Local audio file')}"></label><p class="bgm-status" data-bgm-status role="status" aria-live="polite"></p>`;
    root.appendChild(panel);panels.push(panel);
    panel.querySelector('[data-bgm-track]').addEventListener('change',event=>{
      clearAudio();prefs.time=0;
      if(event.target.value!=='__local__'){resetLocal();prefs.track=event.target.value;save();}
      setMessage(text('曲を選択しました。「再生」を押してください。','Track selected. Press Play.'));
    });
    panel.querySelector('[data-bgm-play]').addEventListener('click',()=>{play();});
    panel.querySelector('[data-bgm-stop]').addEventListener('click',stop);
    panel.querySelector('[data-bgm-volume]').addEventListener('input',event=>{
      prefs.volume=Number(event.target.value)/100;audio.volume=prefs.volume;save();update();
    });
    panel.querySelector('[data-bgm-loop]').addEventListener('change',event=>{
      prefs.loop=event.target.checked;audio.loop=prefs.loop;save();update();
    });
    panel.querySelector('[data-bgm-local]').addEventListener('change',event=>{
      const file=event.target.files[0];if(!file)return;
      if(!file.type.startsWith('audio/')&&!/\.(mp3|ogg|wav|m4a|aac|flac)$/i.test(file.name)){setMessage(text('音楽ファイルを選んでください。','Choose an audio file.'));return;}
      clearAudio();resetLocal();prefs.time=0;objectURL=URL.createObjectURL(file);localName=file.name;
      setMessage(text('このページで試聴できます。サーバーには送信しません。','Ready for this page. This file stays on your device.'));event.target.value='';
    });
    update();
  }
  audio.addEventListener('loadedmetadata',()=>{
    if(!objectURL&&resumeAt>0&&Number.isFinite(audio.duration)&&resumeAt<audio.duration)audio.currentTime=resumeAt;
    resumeAt=0;sourceReady=true;
  });
  audio.addEventListener('ended',()=>{prefs.time=0;save();setMessage(text('再生が終了しました。','Playback finished.'));});
  audio.addEventListener('error',()=>{if(sourceName){pending=false;setMessage(text('曲を読み込めません。別の曲を選んでください。','Unable to load track. Try another file.'));}});
  audio.addEventListener('timeupdate',()=>{if(sourceReady&&!audio.paused&&!objectURL&&Date.now()-lastSaved>3000){lastSaved=Date.now();prefs.time=audio.currentTime||0;save();}});
  audio.addEventListener('pause',update);audio.addEventListener('play',update);
  document.addEventListener('visibilitychange',()=>{
    if(document.hidden&&(pending||!audio.paused)){generation++;pending=false;if(sourceReady)prefs.time=objectURL?0:audio.currentTime||0;audio.pause();save();setMessage(text('画面を離れたため一時停止しました。','Paused while the page is hidden.'));}
  });
  window.addEventListener('pagehide',()=>{if(!objectURL&&sourceName&&sourceReady)prefs.time=audio.currentTime||0;save();audio.pause();});
  window.addEventListener('storage',event=>{
    if(event.key!==KEY)return;
    clearAudio();resetLocal();prefs=readPrefs(event.newValue);audio.volume=prefs.volume;audio.loop=prefs.loop;
    setMessage(text('別のタブのBGM設定を反映しました。「再生」で再開できます。','BGM settings updated from another tab. Press Play to resume.'));
  });
  function start(){
    const controls=document.getElementById('site-preferences');
    if(controls){mount(controls);controls.addEventListener('toggle',()=>{if(controls.open)loadCatalog();});}
    const settings=document.getElementById('bgm-settings');if(settings){mount(settings);loadCatalog();}
  }
  window.CatPSBGM={play,stop};
  // The locale script creates the shared preferences at DOMContentLoaded.
  // A deferred script runs while readyState is still interactive, before that event.
  if(document.readyState==='complete')start();else document.addEventListener('DOMContentLoaded',start,{once:true});
})();
