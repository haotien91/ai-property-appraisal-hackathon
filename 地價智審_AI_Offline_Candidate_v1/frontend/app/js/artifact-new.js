(() => {
  const $=id=>document.getElementById(id);
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  let busy=false;
  window.addEventListener('beforeunload',e=>{if(busy){e.preventDefault();e.returnValue='';}});
  $('form').addEventListener('submit',async e=>{
    e.preventDefault(); if(busy)return;
    const file=$('archive').files[0];if(!file)return;
    $('status').className='';$('done').hidden=true;
    if(file.size>40*1024*1024){$('status').textContent='檔案超過 40 MB，請檢查生成內容。';return;}
    busy=true;Array.from($('form').elements).forEach(el=>el.disabled=true);
    try{
      const params=new URLSearchParams({case_name:$('caseName').value.trim(),group_name:$('groupName').value.trim()});
      $('status').textContent='正在傳送檔案…';
      const bytes=await file.arrayBuffer();
      const metadata=new TextEncoder().encode(params.toString());
      const fingerprint=new Uint8Array(bytes.byteLength+metadata.length);fingerprint.set(new Uint8Array(bytes));fingerprint.set(metadata,bytes.byteLength);
      const key=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',fingerprint)),x=>x.toString(16).padStart(2,'0')).join('');
      const response=await fetch('/artifact-api/deliver?'+params,{method:'POST',headers:{'Content-Type':'application/zip','Idempotency-Key':key},body:bytes});
      if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(data?.error||'無法開始匯入，請確認本機服務已啟動。');}
      const job=await response.json();
      $('status').textContent='正在驗證書表並上傳案件庫…';
      for(;;){
        await delay(1500);
        const r=await fetch('/artifact-api/jobs/'+encodeURIComponent(job.job_id),{cache:'no-store'});
        if(!r.ok)throw new Error('暫時無法讀取進度。請使用相同檔案與名稱重試。');
        const state=await r.json();
        if(state.status==='failed')throw new Error(state.error);
        if(state.status==='completed'){
          $('status').textContent='案件已建立，三類書表已匯入。';$('done').hidden=false;break;
        }
      }
    }catch(error){$('status').className='error';$('status').textContent=error.message;$('submit').textContent='重試匯入';}
    finally{busy=false;Array.from($('form').elements).forEach(el=>el.disabled=false);}
  });
})();
