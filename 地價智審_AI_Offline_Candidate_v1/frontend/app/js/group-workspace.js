/* UI-only adapter. Replace GroupWorkspaceAPI with the AWS adapter before this script.
 * Scope every request by caseId + groupId + version; demo numbers are not case data. */
(() => {
  'use strict';
  const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
  const histories = new Map();
  let sourceDocuments=[
    {id:'source-criteria',name:'評價基準明細表.pdf',label:'評價基準明細表',url:'data/pdf/valuation-criteria.pdf'},
    {id:'source-question',name:'題目.pdf',label:'地價區段勘查表',url:'data/pdf/competition-question.pdf'}
  ];
  const key = scope => JSON.stringify([scope.caseId, scope.groupId, scope.version]);
  const surveyIndex=[
    {code:'P002-00',pageStart:1,pageEnd:1},
    {code:'P003-00',pageStart:2,pageEnd:2},
    {code:'P004-00',pageStart:3,pageEnd:3},
    {code:'P001-00',pageStart:4,pageEnd:4}
  ];
  const documentTemplates = [
    {id:'survey', name:'地價區段勘查表', short:'區段勘查', rows:[['主要道路寬度','12 公尺','road'],['用地類別','住宅用地（示範）','use'],['區域條件','比準地與比較標的相同（示範）','conditions']]},
    {id:'comparison', name:'比較法調查估價表', short:'比較法估價', rows:[['土地正常單價','100,000 元／㎡','price'],['價格日期調整率','2%','date'],['調整至基準日單價','102,000 元／㎡','adjusted'],['區域因素調整率','0%','regional'],['個別因素調整率','−2%','individual'],['試算價格','99,960 元／㎡','result']]},
    {id:'factors', name:'影響地價區域因素分析明細表', short:'區域因素分析', rows:[['比準地與比較標的條件','相同（示範）','conditions'],['區域因素總修正數','0%','total']]}
  ];
  if(new URLSearchParams(location.search).get('data')==='live'&&!window.GroupWorkspaceAPI){
    const error=document.createElement('p');error.setAttribute('role','alert');error.textContent='真實資料介面未載入，請重新整理；不會使用示範資料替代。';document.body.prepend(error);return;
  }
  window.GroupWorkspaceAPI ||= {
    async loadGroup(scope) {
      await wait(180);
      return {documents:documentTemplates.map(d=>d.id==='survey'?{...d,pdfUrl:'data/pdf/survey-sample.pdf',segments:surveyIndex}:d), messages:[...(histories.get(key(scope)) || [])]};
    },
    async sendMessage(scope, {text}) {
      await wait(900);
      const cross = /比對|跨表|一致|關聯/.test(text);
      const calculation = /計算|算|公式|價格/.test(text);
      const response = cross ? {
        text:'示範回覆：區域因素分析明細表的「區域因素總修正數」為 0%，比較法調查估價表的「區域因素調整率」也是 0%。這兩個示範欄位一致。\n\n區段勘查表提供原始條件，區域因素分析明細表整理調整結果，再由比較法調查估價表使用。\n\n這是預設範例，尚未執行實際案件的跨表比對。',
        citations:[{documentId:'factors',fieldId:'total',label:'區域因素 · 總修正數'},{documentId:'comparison',fieldId:'regional',label:'比較法 · 區域調整率'}]
      } : calculation ? {
        text:'示範回覆：以下使用畫面上的範例數字。\n\n① 日期調整\n100,000 × (1 + 2%) = 102,000 元／㎡\n\n② 示範試算\n102,000 × (1 + 0%) × (1 − 2%) = 99,960 元／㎡\n\n這是用來展示公式、代入值及來源引用的簡化算例，不是此組的實際估價結果。',
        citations:[{documentId:'comparison',fieldId:'result',label:'比較法 · 試算價格'},{documentId:'factors',fieldId:'total',label:'區域因素 · 總修正數'}]
      } : {
        text:'目前是介面示範，尚未連接 AWS agent，因此無法分析這個問題。你可以試試「這個試算價格怎麼算？」或「比對三張表的關聯」，查看預設的回答與來源跳轉。',citations:[]
      };
      const messages = histories.get(key(scope)) || [];
      messages.push({role:'user',text}, {role:'assistant',...response});
      histories.set(key(scope), messages);
      return response;
    }
  };
  const api = window.GroupWorkspaceAPI;
  const workspace = document.createElement('section');
  workspace.className='gw'; workspace.hidden=true; workspace.setAttribute('aria-label','組別工作區');
  workspace.innerHTML=`<header class="gw-top"><button class="gw-back" type="button">← 案件庫</button><div class="gw-case"><strong id="gw-case-name"></strong><small id="gw-case-number"></small></div><select id="gw-group" aria-label="切換組別"></select><button type="button" class="gw-quiet" id="gw-toggle" aria-expanded="true">收起對話</button></header><div class="gw-body"><section class="gw-documents" aria-label="文件"><nav class="gw-tabs" aria-label="切換文件"></nav><div class="gw-viewer" tabindex="0" aria-label="文件內容"></div></section><div class="gw-divider" role="separator" tabindex="0" aria-label="調整文件與對話寬度" aria-orientation="vertical" aria-valuemin="40" aria-valuemax="75" aria-valuenow="64"></div><aside class="gw-chat" id="gw-chat"><div class="gw-chat-head"><div><strong>這組的對話</strong><small id="gw-chat-scope"></small></div><span style="font-size:11px;color:#999">Mock</span></div><div class="gw-messages" role="log" aria-label="對話紀錄" aria-live="polite"></div><form class="gw-composer"><div class="gw-compose-box"><textarea id="gw-question" aria-label="詢問這組資料" placeholder="詢問這組資料…" maxlength="2000"></textarea><div class="gw-compose-bottom"><span>示範回覆 · 尚未連接 agent</span><button class="gw-send" type="submit">送出 ↑</button></div></div><p class="gw-error" role="alert" hidden></p></form></aside></div>`;
  document.body.append(workspace);
  const versionSelect=document.createElement('select');versionSelect.id='gw-version';versionSelect.setAttribute('aria-label','生成版本');versionSelect.hidden=!api.live;
  workspace.querySelector('#gw-group').after(versionSelect);
  if(api.live){workspace.querySelector('.gw-composer').hidden=true;workspace.querySelector('.gw-chat-head span').textContent='尚未連接';}

  const $ = selector => workspace.querySelector(selector);
  const sessions=new Map();
  let currentCase, scope, session, selectedDocument='survey', epoch=0, onReturn, scrollY=0, oldOverflow='', width=390, example=false;
  const narrow=window.matchMedia('(max-width:800px)');
  const pdfFrames=new Map();
  const pdfHost=document.createElement('div');pdfHost.className='gw-pdf-host';
  $('.gw-documents').append(pdfHost);
  const documentBar=document.createElement('div');documentBar.className='gw-document-bar';
  $('.gw-tabs').before(documentBar);
  documentBar.append($('.gw-tabs'));
  const sourceTabs=document.createElement('nav');sourceTabs.className='gw-source-tabs';sourceTabs.setAttribute('aria-label','來源文件');
  sourceTabs.innerHTML=sourceDocuments.map(d=>`<button type="button" class="gw-source-tab" data-tab="${d.id}" aria-pressed="false" title="${esc(d.name)}">${esc(d.label)}</button>`).join('');
  documentBar.append(sourceTabs);
  $('.gw-tabs').setAttribute('aria-label','產出書表');
  const segmentMenu=document.createElement('div');segmentMenu.className='gw-segment-menu';segmentMenu.hidden=true;
  segmentMenu.setAttribute('role','group');segmentMenu.setAttribute('aria-label','區段目錄');
  documentBar.append(segmentMenu);
  function closeSegments(){segmentMenu.hidden=true;$('.gw-segment-toggle')?.setAttribute('aria-expanded','false');}
  function chatVisible(visible) {
    workspace.classList.toggle('gw-chat-hidden',!visible);
    $('#gw-toggle').textContent=narrow.matches?(visible?'查看文件':'開啟對話'):(visible?'收起對話':'開啟對話');
    $('#gw-toggle').setAttribute('aria-expanded',String(visible));
  }
  function renderChat() {
    if(!session)return;
    if(api.live){$('.gw-messages').textContent='書表已連接 AWS；對話助理尚未接上。';return;}
    const log=$('.gw-messages');
    log.innerHTML=session.messages.length ? session.messages.map(m=>`<div class="gw-message ${m.role==='user'?'user':'assistant'}"><strong>${m.role==='user'?'你':'資料助理 · 示範'}</strong>${esc(m.text)}</div>`).join('') : '<div class="gw-empty"><h2>從這組資料開始問</h2><p>查看計算過程，或追溯三張表之間的關聯。</p><button type="button" class="gw-suggestion">這個試算價格怎麼算？</button><button type="button" class="gw-suggestion">比對三張表的關聯</button></div>';
    if(session.pending)log.insertAdjacentHTML('beforeend','<p class="gw-message">正在準備示範回覆…</p>');
    $('.gw-send').disabled=Boolean(session.pending||session.loading);
    $('.gw-error').hidden=!session.error;
    $('.gw-error').textContent=session.error||'';
    log.scrollTop=log.scrollHeight;
  }
  function renderDocument(field) {
    if(!session)return;
    closeSegments();
    if(field)example=true;
    $('.gw-tabs').innerHTML=session.documents.map(d=>`<button type="button" class="gw-tab" data-tab="${esc(d.id)}" aria-pressed="${selectedDocument===d.id}">${esc(d.short)}</button>`).join('');
    const surveyTab=$('[data-tab="survey"]');
    if(surveyTab&&(session.documents.find(d=>d.id==='survey')?.segments?.length||0)>0){
      const wrap=document.createElement('div');wrap.className='gw-survey-tab';surveyTab.before(wrap);wrap.append(surveyTab);
      wrap.insertAdjacentHTML('beforeend','<button type="button" class="gw-segment-toggle" aria-label="選擇區段頁面" aria-expanded="false">▾</button>');
    }
    sourceTabs.querySelectorAll('[data-tab]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.tab===selectedDocument)));
    const g=currentCase.groups.find(g=>g.id===scope.groupId);
    const doc=session.documents.find(d=>d.id===selectedDocument)||sourceDocuments.find(d=>d.id===selectedDocument);
    const viewer=$('.gw-viewer');
    const showPdf=(!api.live||Boolean(doc?.pdfUrl||doc?.url))&&!example&&selectedDocument!=='sources'&&!session.loading;
    viewer.hidden=showPdf;pdfHost.hidden=!showPdf;
    for(const frame of pdfFrames.values())frame.hidden=true;
    if(showPdf){
      const frameKey=key(scope)+selectedDocument;
      if(!pdfFrames.has(frameKey)){
        const frame=document.createElement('iframe');
        frame.title=api.live?doc.name:doc.name+(doc.pdfUrl?'（題目頁面示範，尚未填表）':doc.url?'（比賽當天來源）':'（官方範本）');
        frame.src=doc.pdfUrl?doc.pdfUrl+'#page='+(doc.pageStart||1)+'&view=FitH':doc.url?doc.url+'#view=FitH':'data/pdf/appraisal-sample.pdf#page='+({survey:1,comparison:3,factors:2}[selectedDocument]||1)+'&view=FitH';
        pdfHost.append(frame);pdfFrames.set(frameKey,frame);
      }
      pdfFrames.get(frameKey).hidden=false;return;
    }
    if(session.loading){viewer.innerHTML='<p>正在載入文件…</p>';return;}
    if(api.live){viewer.textContent=session.error||'這份 PDF 尚未上傳。';return;}
    if(selectedDocument==='sources'){
      viewer.innerHTML=`<section class="gw-sources"><h1>來源資料</h1><p>比賽當天提供的兩份文件</p>${sourceDocuments.map(d=>`<button type="button" class="gw-source-file" data-tab="${d.id}"><span>${esc(d.name)}</span><span aria-hidden="true">↗</span></button>`).join('')}</section>`;
      viewer.scrollTop=0;return;
    }
    viewer.innerHTML=`<article class="gw-paper"><div class="gw-paper-kicker">示範文件 · 非本組實際資料</div><h1>${selectedDocument==='sources'?'來源資料':esc(doc?.name||'文件')}</h1><p class="gw-paper-subtitle">${esc([g.name||g.section,g.landUse].filter(Boolean).join(' · '))}</p>${selectedDocument==='sources'?'<div class="gw-source-item">來源資料 1<small>尚未串接原始檔案與檔名</small></div><div class="gw-source-item">來源資料 2<small>尚未串接原始檔案與檔名</small></div>':`<table><caption>互動示範欄位 · 點選數值可帶入問題</caption><tbody>${(doc?.rows||[]).map(([label,value,id])=>`<tr data-row="${esc(id)}" class="${id===field?'gw-highlight':''}"><th scope="row">${esc(label)}</th><td><button type="button" class="gw-field" data-question="請解釋${esc(doc.name)}的「${esc(label)}」如何計算或取得？">${esc(value)}</button></td></tr>`).join('')}</tbody></table><p class="gw-paper-note">此版以 HTML 範例呈現文件互動，數字不代表所選案件；後續可替換為實際 PDF 與欄位定位。</p>`}</article>`;
    viewer.scrollTop=0;
    if(field){const target=Array.from(viewer.querySelectorAll('[data-row]')).find(r=>r.dataset.row===field);if(target)viewer.scrollTop=Math.max(0,target.getBoundingClientRect().top-viewer.getBoundingClientRect().top+viewer.scrollTop-90);}
  }
  async function switchGroup(groupId,version) {
    const token=++epoch;
    scope={caseId:currentCase.id,groupId,version:api.live?(version||currentCase.groups.find(g=>g.id===groupId).selectedRun||''):'demo-v1'};
    if(api.live){pdfFrames.forEach(f=>f.remove());pdfFrames.clear();sessions.clear();}
    $('#gw-group').value=groupId;
    const g=currentCase.groups.find(g=>g.id===groupId);
    $('#gw-chat-scope').textContent=g.name||g.section;
    selectedDocument='survey';
    session=sessions.get(key(scope));
    if(!session){session={messages:[],documents:[],loading:true,draft:''};sessions.set(key(scope),session);}
    $('#gw-question').value=session.draft||'';
    renderChat();renderDocument();
    if(!session.loading)return;
    const target=session, requestedScope={...scope};
    try {const data=await api.loadGroup(requestedScope);target.documents=data.documents;target.messages=data.messages;target.loading=false;
      if(api.live&&token===epoch){
        sourceDocuments=data.sources;
        sourceTabs.innerHTML=sourceDocuments.map(d=>`<button type="button" class="gw-source-tab" data-tab="${esc(d.id)}">${esc(d.label)}</button>`).join('');
        scope.version=data.runId||'';
        versionSelect.innerHTML=(data.versions||[]).map(r=>`<option value="${esc(r.run_id)}">${esc(new Date(r.created_at).toLocaleString('zh-TW'))}${r.pdf_complete?'':' · PDF 未齊'}</option>`).join('')||'<option>尚無生成版本</option>';
        versionSelect.value=data.runId||'';
      }}
    catch(_){target.loading=false;target.error='資料載入失敗，請切換組別後重試。';sessions.delete(key(requestedScope));}
    if(token===epoch&&!workspace.hidden){renderChat();renderDocument();}
  }
  window.GroupWorkspace={open(c,groupId,returnCallback){
    currentCase=c;onReturn=returnCallback;scrollY=window.scrollY;oldOverflow=document.body.style.overflow;
    document.body.style.overflow='hidden';document.getElementById('case-library').inert=true;
    document.querySelector('.cl-minimal-footer').inert=true;
    workspace.hidden=false;chatVisible(api.live?false:!narrow.matches);split(width);
    $('#gw-case-name').textContent=c.name||c.number||'未命名案件';
    const showNumber=Boolean(c.number&&c.number!==c.name);
    $('#gw-case-number').textContent=showNumber?c.number:'';$('#gw-case-number').hidden=!showNumber;
    $('#gw-group').innerHTML=c.groups.map(g=>`<option value="${esc(g.id)}">${esc([g.name||g.section,g.landUse].filter(Boolean).join(' · '))}</option>`).join('');
    switchGroup(groupId);$('.gw-back').focus();
  }};
  $('.gw-back').addEventListener('click',()=>{++epoch;workspace.hidden=true;document.body.style.overflow=oldOverflow;document.getElementById('case-library').inert=false;document.querySelector('.cl-minimal-footer').inert=false;window.scrollTo(0,scrollY);onReturn?.();});
  versionSelect.addEventListener('change',()=>switchGroup(scope.groupId,versionSelect.value));
  $('#gw-group').addEventListener('change',e=>switchGroup(e.target.value));
  $('#gw-toggle').addEventListener('click',()=>chatVisible(workspace.classList.contains('gw-chat-hidden')));
  narrow.addEventListener('change',()=>chatVisible(!narrow.matches));
  $('#gw-question').addEventListener('input',e=>{session.draft=e.target.value;});
  workspace.addEventListener('click',e=>{
    if(e.target.closest('.gw-segment-toggle')){
      const open=segmentMenu.hidden;
      closeSegments();
      if(open){
        const survey=session.documents.find(d=>d.id==='survey');
        segmentMenu.innerHTML=(api.live?'<small>區段目錄</small>':'<small>區段目錄 · 題目頁面示範</small>')+(survey?.segments||[]).map(s=>`<button type="button" data-segment-code="${esc(s.code)}" data-segment-page="${s.pageStart}"><span>${esc(s.code)}</span><small>第 ${s.pageStart} 頁</small></button>`).join('');
        segmentMenu.hidden=false;$('.gw-segment-toggle').setAttribute('aria-expanded','true');segmentMenu.querySelector('button')?.focus();
      }return;
    }
    const segment=e.target.closest('[data-segment-page]');
    if(segment){
      const page=Number(segment.dataset.segmentPage);selectedDocument='survey';example=false;renderDocument();
      const doc=session.documents.find(d=>d.id==='survey');
      const url=doc.segments?.find(s=>s.code===segment.dataset.segmentCode)?.url||doc.pdfUrl;
      const frameKey=key(scope)+'survey';
      const frame=pdfFrames.get(frameKey);
      if(frame&&url){
        // Native PDF viewers may ignore fragment-only navigation on an existing iframe.
        // A fresh iframe opens the requested document/page consistently.
        const replacement=document.createElement('iframe');
        replacement.title=doc.name+' · '+segment.dataset.segmentCode;
        replacement.src=url.split('#')[0]+'#page='+page+'&view=FitH';
        frame.replaceWith(replacement);pdfFrames.set(frameKey,replacement);
      }
      $('.gw-segment-toggle')?.focus();return;
    }
    const tab=e.target.closest('[data-tab]');if(tab){selectedDocument=tab.dataset.tab;example=false;renderDocument();}
    const citation=e.target.closest('[data-document]');if(citation){selectedDocument=citation.dataset.document;renderDocument(citation.dataset.field);if(narrow.matches)chatVisible(false);$('.gw-viewer').focus({preventScroll:true});}
    const question=e.target.closest('[data-question],.gw-suggestion');if(question){chatVisible(true);$('#gw-question').value=question.dataset.question||question.textContent;session.draft=$('#gw-question').value;$('#gw-question').focus();}
  });
  document.addEventListener('click',e=>{if(!documentBar.contains(e.target))closeSegments();});
  workspace.addEventListener('keydown',e=>{if(e.key==='Escape'&&!segmentMenu.hidden){closeSegments();$('.gw-segment-toggle')?.focus();}});
  $('.gw-composer').addEventListener('submit',async e=>{
    e.preventDefault();const text=$('#gw-question').value.trim();if(!text||!session||session.pending||session.loading)return;
    const target=session, requestScope={...scope};target.messages.push({role:'user',text});target.pending=true;target.error='';target.draft='';$('#gw-question').value='';renderChat();
    try{const response=await api.sendMessage(requestScope,{text,documentId:selectedDocument});target.messages.push({role:'assistant',...response});}
    catch(_){target.error='訊息傳送失敗，內容已保留在輸入框，可重新送出。';target.messages.pop();target.draft=text;}
    finally{target.pending=false;if(session===target&&!workspace.hidden){$('#gw-question').value=target.draft;renderChat();}}
  });
  const divider=$('.gw-divider');let pointer=null;
  function split(value){width=Math.max(320,Math.min(520,value));workspace.style.setProperty('--chat-width',width+'px');divider.setAttribute('aria-valuemin','320');divider.setAttribute('aria-valuemax','520');divider.setAttribute('aria-valuenow',String(Math.round(width)));divider.setAttribute('aria-valuetext',`對話寬度 ${Math.round(width)} 像素`);}
  divider.addEventListener('pointerdown',e=>{if(e.button!==0)return;pointer=e.pointerId;divider.setPointerCapture(pointer);workspace.classList.add('dragging');e.preventDefault();});
  divider.addEventListener('pointermove',e=>{if(pointer!==e.pointerId)return;const rect=$('.gw-body').getBoundingClientRect();split(rect.right-e.clientX);});
  function stop(){pointer=null;workspace.classList.remove('dragging');}
  divider.addEventListener('pointerup',stop);divider.addEventListener('pointercancel',stop);divider.addEventListener('lostpointercapture',stop);
  divider.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();split(e.key==='Home'?320:e.key==='End'?520:width+(e.key==='ArrowLeft'?20:-20));});
})();
