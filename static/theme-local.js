(()=>{
  'use strict';
  const KEY='catps_theme_global_v1';
  const LEGACY_KEY='autocat_theme_pages_v1';
  function read(){
    try{
      const raw=localStorage.getItem(KEY);
      if(raw!==null){
        const data=JSON.parse(raw);
        if(data&&typeof data.background==='string')return {background:data.background};
      }
      const old=JSON.parse(localStorage.getItem(LEGACY_KEY)||'{}');
      if(old&&typeof old==='object'&&!Array.isArray(old)){
        const selected=old['index.html']||old['vip.html']||Object.values(old).find(v=>typeof v==='string'&&v)||'';
        const data={background:typeof selected==='string'?selected:''};
        if(data.background)localStorage.setItem(KEY,JSON.stringify(data));
        return data;
      }
    }catch{}
    return {background:''};
  }
  function save(background,layout,language){
    const value=['classic','simple','dark','eva','eva-amber'].includes(layout)?layout:'classic';
    localStorage.setItem(KEY,JSON.stringify({background:typeof background==='string'?background:''}));
    localStorage.removeItem(LEGACY_KEY);
    if(language!==undefined)document.cookie=`catps_lang=${language==='en'?'en':'ja'}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`;
    document.cookie=`catps_layout=${value}; Path=/; Max-Age=31536000; SameSite=Lax${location.protocol==='https:'?'; Secure':''}`;
  }
  window.CatPSTheme={read,save};
  async function apply(){
    const video=document.querySelector('.mc-background-video');
    if(!video)return;
    const selected=read().background;
    if(!selected)return;
    try{
      const res=await fetch('/api/theme/videos',{cache:'no-store'});
      if(!res.ok)return;
      const data=await res.json();
      const found=(data.videos||[]).find(v=>v.name===selected);
      if(!found)return;
      const source=video.querySelector('source');
      if(source){
        const types={mp4:'video/mp4',webm:'video/webm',ogg:'video/ogg',mov:'video/quicktime'};
        source.src=found.url;
        source.type=types[found.name.split('.').pop().toLowerCase()]||'video/mp4';
        video.load();
      }else video.src=found.url;
      document.documentElement.classList.add('has-theme-video');
      video.muted=true;
      const play=video.play();
      if(play?.catch)play.catch(()=>{});
    }catch{}
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply,{once:true});else apply();
})();
