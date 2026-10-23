'use strict';
(()=>{
  const allowed=['white','black'];
  let theme='white';
  try{const saved=localStorage.getItem('cps-chat-theme');if(allowed.includes(saved))theme=saved;}catch{}
  document.documentElement.dataset.theme=theme;
  document.addEventListener('DOMContentLoaded',()=>{
    const select=document.getElementById('themeSelect');if(!select)return;
    select.value=theme;
    select.addEventListener('change',()=>{
      theme=allowed.includes(select.value)?select.value:'white';
      document.documentElement.dataset.theme=theme;
      try{localStorage.setItem('cps-chat-theme',theme);}catch{}
    });
  });
})();
