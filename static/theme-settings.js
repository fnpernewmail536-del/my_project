(()=>{
  'use strict';
  const layout=document.getElementById('layout-select');
  const video=document.getElementById('video');
  const language=document.getElementById('language-select');
  const status=document.getElementById('status');
  const noticeKey='catps_theme_notice_v1';
  video.value=window.CatPSTheme.read().background;
  // A removed video gracefully falls back to the default option.
  if(video.selectedIndex<0)video.value='';
  function save(){
    try{
      window.CatPSTheme.save(video.value,layout.value,language.value);
      status.textContent='全ページの表示と背景を、このブラウザに保存しました。';
      return true;
    }catch{
      status.textContent='設定を保存できませんでした。ブラウザの保存設定を確認してください。';
      return false;
    }
  }
  document.getElementById('save').onclick=()=>{
    if(!save())return;
    try{sessionStorage.setItem(noticeKey,'全ページにテーマを適用しました。');}catch{}
    location.reload();
  };
  document.getElementById('preview').onclick=()=>{if(save())location.href='/';};
  document.getElementById('reset-all').onclick=()=>{
    layout.value='classic';video.value='';
    if(!save())return;
    try{sessionStorage.setItem(noticeKey,'全ページを標準のクラシックに戻しました。');}catch{}
    location.reload();
  };
  try{
    const notice=sessionStorage.getItem(noticeKey);
    if(notice){status.textContent=notice;sessionStorage.removeItem(noticeKey);}
  }catch{}
})();
