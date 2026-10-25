(function () {
  'use strict';
  const doc=document;
  const seen=new WeakMap();
  const format=(tc,ac)=>`引継ぎコード： ${tc}\n認証番号： ${ac}`;
  async function write(value,textarea){
    try{if(!navigator.clipboard?.writeText)throw Error();await navigator.clipboard.writeText(value);return true;}
    catch{textarea.focus();textarea.select();textarea.setSelectionRange(0,textarea.value.length);try{return doc.execCommand('copy');}catch{return false;}}
  }
  function scan(){
    doc.querySelectorAll('.code-display').forEach(box=>{
      const values=[...box.querySelectorAll('.code-val')].map(node=>{const copy=node.cloneNode(true);copy.querySelectorAll('button').forEach(b=>b.remove());return copy.textContent.trim();});
      const [tc,ac]=values;
      if(!/^[a-f\d]{9}$/i.test(tc||'')||!/^\d{4}$/.test(ac||''))return;
      const value=format(tc,ac);if(seen.get(box)===value)return;seen.set(box,value);
      box.querySelector('.code-pair-copy')?.remove();
      const row=doc.createElement('div');row.className='code-pair-copy';
      const text=doc.createElement('textarea');text.readOnly=true;text.rows=2;text.value=value;text.setAttribute('aria-label','引継ぎコードと認証番号');
      const button=doc.createElement('button');button.type='button';button.textContent='まとめてコピー';
      button.onclick=async()=>{const ok=await write(text.value,text);button.textContent=ok?'コピーしました':'選択済み・手動でコピー';};
      row.append(text,button);box.append(row);
    });
  }
  window.CPSCodeCopy={format,scan};
  new MutationObserver(scan).observe(doc.documentElement,{childList:true,subtree:true,characterData:true});
  scan();
}());
