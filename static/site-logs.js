(() => {
  'use strict';
  const rows=document.getElementById('log-rows'), status=document.getElementById('log-status'), more=document.getElementById('log-more');
  const form=document.getElementById('log-filter'), action=document.getElementById('log-action');
  let before=null, busy=false;
  async function load(append=false){
    if(busy)return;busy=true;more.disabled=true;form.querySelector('button').disabled=true;
    status.textContent='読み込み中...';
    try {
      const query=new URLSearchParams({q:document.getElementById('log-query').value,action:action.value});
      if(append&&before)query.set('before',before);
      const data=await window.accountAPI(`/api/admin/logs?${query}`);
      if(action.options.length===1){for(const [value,label] of Object.entries(data.actions)){const option=document.createElement('option');option.value=value;option.textContent=label;action.append(option);}}
      if(!append)rows.replaceChildren();
      for(const item of data.items){
        const row=document.createElement('tr');
        const values=[new Date(item.created_at*1000).toLocaleString('ja-JP',{timeZone:'Asia/Tokyo'}),item.username,item.actor,item.message+(item.detail?`（${item.detail}）`:''),item.reference||'—'];
        values.forEach((value,index)=>{const cell=document.createElement('td');cell.textContent=value;if(index===1&&item.public_id){const id=document.createElement('small');id.textContent=item.public_id;cell.append(id);}row.append(cell);});
        rows.append(row);
      }
      before=data.next_before;more.hidden=!before;
      status.textContent=rows.children.length?`${rows.children.length}件のログを表示しています。`:'該当するログはありません。';
    }catch(error){status.textContent=error.message;}
    finally{busy=false;more.disabled=false;form.querySelector('button').disabled=false;}
  }
  form.addEventListener('submit',event=>{event.preventDefault();load();});
  more.addEventListener('click',()=>load(true));load();
})();
