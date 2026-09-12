/* Standalone prototype. Stable case IDs are independent of editable display names.
 * Replace the demo data / name storage adapter when the case database is ready.
 * No demo case IDs are sent to the existing team's document workflow. */
(() => {
  'use strict';
  const root = document.getElementById('case-library');
  if (!root) return;
  const districts = window.NTPC_DISTRICTS || [];
  const demoCases = [
    ['sl-01','樹林區','樹人街周邊住宅用地查估案','1110901-99-001','review',['P002-00','P003-00','P004-00','P005-00'],[4,1,1]],
    ['sl-02','樹林區','東榮街住宅用地查估案','1110901-99-002','done',['P003-00'],[1,1,1]],
    ['sl-03','樹林區','啟智街捷運開發區查估案','1110901-99-003','processing',['P005-00'],[2,1,1]],
    ['sl-04','樹林區','鎮前街周邊查估案','1110901-99-004','review',['P004-00'],[3,1,1]],
    ['bq-01','板橋區','文化路周邊住宅用地查估案','1150901-99-005','review',['示範區段 A'],[2,1,1]],
    ['bq-02','板橋區','江子翠住宅用地查估案','1150901-99-006','done',['示範區段 B'],[1,1,1]],
    ['js-01','金山區','金山商業用地查估案','1140901-99-007','done',['示範區段 C'],[1,1,1]],
    ['ds-01','淡水區','淡海周邊住宅用地查估案','1150901-99-008','review',['示範區段 D'],[2,1,1]],
    ['xd-01','新店區','北新路周邊查估案','1150901-99-009','processing',['示範區段 E'],[1,1,1]],
    ['xc-01','新莊區','中平路周邊查估案','1150901-99-010','done',['示範區段 F'],[2,1,1]]
  ].map(([id,district,name,number,status,sections,files])=>({id,district,name,number,status,sections,files}));
  const storageKey = 'ntpc-case-library-names-v1';
  let saved = {};
  try { const value=JSON.parse(localStorage.getItem(storageKey)||'{}'); if(value && typeof value==='object' && !Array.isArray(value)) saved=value; } catch (_) { /* Storage may be disabled. */ }
  const cases=demoCases.map(c=>({...c,name:typeof saved[c.id]==='string' && saved[c.id].trim() ? saved[c.id].slice(0,60) : c.name}));
  let selected='', filter='all', query='', active=null, opener=null, toastTimer;
  const $=id=>document.getElementById(id);
  const labels={review:'待覆核',done:'已完成',processing:'處理中'};
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const count=d=>cases.filter(c=>c.district===d).length;
  const total=c=>c.files.reduce((a,b)=>a+b,0);
  $('cl-district-select').innerHTML='<option value="">選擇行政區</option>'+districts.map(d=>`<option value="${esc(d.name)}">${esc(d.name)}${count(d.name)?" · 有案件":""}</option>`).join('');
  $('cl-district-select').value=selected;
  $('cl-map').innerHTML='<text x="218" y="205" class="cl-city-label">臺北市</text>'+districts.map(d=>`<path class="cl-district ${count(d.name)?'has-cases':''}" d="${d.path}" data-district="${d.name}" role="button" tabindex="0" aria-label="${d.name}，${count(d.name)?'有案件':'尚無案件'}" aria-pressed="false"><title>${d.name} · ${count(d.name)?'有案件':'尚無案件'}</title></path>`).join('')+districts.filter(d=>d.area>900).map(d=>`<text x="${d.x}" y="${d.y}" class="cl-map-label" data-label="${d.name}">${d.name.replace('區','')}</text>`).join('');
  function render(){
    const searching=Boolean(query);
    const showShelf=Boolean(selected)||searching;
    $('cl-map-panel').hidden=false;
    root.classList.toggle('has-shelf',showShelf);
    $('cl-divider').hidden=!showShelf;
    $('cl-map-panel').inert=showShelf && window.matchMedia('(max-width: 760px)').matches;
    $('cl-shelf-panel').hidden=false;
    $('cl-shelf-panel').inert=!showShelf;
    $('cl-shelf-panel').setAttribute('aria-hidden',String(!showShelf));
    if(!showShelf){
      $('cl-district-select').value='';
      $('cl-district-select').options[0].textContent='選擇行政區';
      root.querySelectorAll('[data-district]').forEach(el=>{el.classList.remove('selected');el.setAttribute('aria-pressed','false');});
      root.querySelectorAll('[data-label]').forEach(el=>el.classList.remove('selected'));
      $('cl-results-message').textContent='';
      return; // Keep the current shelf content intact while it slides away.
    }
    const scope=searching?cases:cases.filter(c=>c.district===selected);
    const matching=scope.filter(c=>!query||[c.name,c.number,c.district,...c.sections].some(s=>s.toLowerCase().includes(query)));
    const shown=matching.filter(c=>filter==='all'||c.status===filter);
    $('cl-district-title').textContent=searching?'全市搜尋結果':selected;
    $('cl-district-count').textContent=`${shown.length} 件案件`;
    $('cl-district-select').value=searching?'':selected;
    $('cl-district-select').options[0].textContent=searching?'全市搜尋':'選擇行政區';
    root.querySelectorAll('[data-district]').forEach(el=>{const yes=el.dataset.district===selected;el.classList.toggle('selected',yes);el.setAttribute('aria-pressed',String(yes));});
    root.querySelectorAll('[data-label]').forEach(el=>el.classList.toggle('selected',el.dataset.label===selected));
    root.querySelectorAll('[data-filter]').forEach(el=>{el.classList.toggle('active',el.dataset.filter===filter);el.setAttribute('aria-pressed',String(el.dataset.filter===filter));});
    $('cl-books').innerHTML=shown.length?shown.map(c=>`<button type="button" class="cl-book" data-case="${c.id}" data-status="${c.status}" title="${esc(c.name)}" aria-label="開啟${esc(c.name)}，${labels[c.status]}，${total(c)}份文件">${searching?`<span class="cl-book-district">${esc(c.district)}</span>`:''}<span class="cl-document-preview"><img src="img/case-thumbnails/sample-form.png" alt="估價表範例縮圖" loading="lazy"><span class="cl-preview-label">範例預覽</span></span><span class="cl-book-name">${esc(c.name)}</span><span class="cl-book-meta">${c.number.slice(0,3)} 年度 · 案號 ${c.number.slice(-3)}</span><span class="cl-book-footer"><span class="cl-status ${c.status}">${labels[c.status]}</span><span>${total(c)} 份文件</span></span></button>`).join(''):`<div class="cl-empty"><strong>${searching||matching.length?'沒有符合條件的案件':'此行政區尚無案件'}</strong><button type="button" class="cl-text-button" id="cl-reset">${searching||matching.length?'清除篩選':'返回行政區地圖'} →</button></div>`;
    $('cl-results-message').textContent=showShelf?`${searching?'全市':selected}顯示 ${shown.length} 件案件`:'';
  }
  function choose(name){selected=name;filter='all';query='';$('cl-search').value='';render();if(name)$('cl-district-title').focus({preventScroll:true});}
  root.addEventListener('click',e=>{
    const district=e.target.closest('[data-district]');if(district)choose(district.dataset.district);
    const f=e.target.closest('[data-filter]');if(f){filter=f.dataset.filter;render();}
    const c=e.target.closest('[data-case]');if(c)openCase(c.dataset.case,c);
    if(e.target.id==='cl-reset'){if(query||filter!=='all'){filter='all';query='';$('cl-search').value='';render();}else choose('');}
  });
  $('cl-home').addEventListener('click',()=>{choose('');$('cl-district-select').focus();});
  window.matchMedia('(max-width: 760px)').addEventListener('change',()=>{$('cl-map-panel').inert=root.classList.contains('has-shelf') && window.matchMedia('(max-width: 760px)').matches;});
  $('cl-back').addEventListener('click',()=>{const previous=selected;choose('');const target=previous?root.querySelector(`[data-district="${previous}"]`):$('cl-district-select');target?.focus({preventScroll:true});});
  $('cl-map').addEventListener('keydown',e=>{if(e.target.dataset.district && ['Enter',' '].includes(e.key)){e.preventDefault();choose(e.target.dataset.district);}});
  $('cl-district-select').addEventListener('change',e=>choose(e.target.value));
  $('cl-search').addEventListener('input',e=>{query=e.target.value.trim().toLowerCase();filter='all';render();});
  function openCase(id,button){active=cases.find(c=>c.id===id);opener=button;renderDetail();$('cl-dialog').showModal();}
  function renderDetail(){const c=active;$('cl-detail-title').textContent=c.name;$('cl-detail-district').textContent=c.district+' / 案件卷宗';$('cl-detail-meta').innerHTML=`<dt>正式案號</dt><dd>${esc(c.number)}</dd><dt>估價基準日</dt><dd>${c.number.slice(0,3)} 年 09 月 01 日</dd><dt>涵蓋區段</dt><dd>${c.sections.map(esc).join('、')}</dd><dt>審查狀態</dt><dd><span class="cl-status ${c.status}">${labels[c.status]}</span></dd>`;$('cl-detail-files').innerHTML=['表三｜地價區段勘查表','表四｜比較法調查估價表','表五｜區域因素分析明細表'].map((name,i)=>`<div class="cl-file"><span>${name}<small>${i===0?'依區段分別收納，保留原始文件':'與同案書表一併比對'}</small></span><span>${c.files[i]} 份</span></div>`).join('');$('cl-rename-form').hidden=true;$('cl-title-area').hidden=false;}
  $('cl-close').addEventListener('click',()=>$('cl-dialog').close());
  $('cl-dialog').addEventListener('click',e=>{if(e.target===$('cl-dialog')){const r=e.target.getBoundingClientRect();if(e.clientX<r.left)e.target.close();}});
  $('cl-dialog').addEventListener('close',()=>{const replacement=root.querySelector(`[data-case="${active?.id}"]`);(replacement||opener)?.focus();});
  $('cl-edit-name').addEventListener('click',()=>{$('cl-title-area').hidden=true;$('cl-rename-form').hidden=false;$('cl-name-input').value=active.name;$('cl-name-error').textContent='';$('cl-name-input').focus();});
  $('cl-cancel-name').addEventListener('click',()=>{renderDetail();$('cl-edit-name').focus();});
  $('cl-rename-form').addEventListener('submit',e=>{e.preventDefault();const name=$('cl-name-input').value.trim();if(!name){$('cl-name-error').textContent='請輸入案件名稱。';$('cl-name-input').focus();return;}active.name=name;saved[active.id]=name;let persisted=true;try{localStorage.setItem(storageKey,JSON.stringify(saved));}catch(_){persisted=false;}render();renderDetail();$('cl-edit-name').focus();$('cl-toast').textContent=persisted?'案件名稱已更新，並儲存在此瀏覽器。':'案件名稱已更新；瀏覽器無法儲存，重新整理後會還原。';$('cl-toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('cl-toast').hidden=true,4000);});
  const divider=$('cl-divider');
  const main=root.querySelector('.cl-main');
  let split=32, dragPointer=null;
  function setSplit(value){
    split=Math.max(22,Math.min(60,value));
    root.style.setProperty('--cl-split',split+'%');
    divider.setAttribute('aria-valuenow',String(Math.round(split)));
    divider.setAttribute('aria-valuetext',`地圖 ${Math.round(split)}%，案件列表 ${100-Math.round(split)}%`);
  }
  function endDrag(){
    if(dragPointer!==null && divider.hasPointerCapture(dragPointer)) divider.releasePointerCapture(dragPointer);
    dragPointer=null;
    root.classList.remove('cl-resizing');
  }
  divider.addEventListener('pointerdown',e=>{
    if(e.button!==0)return;
    e.preventDefault();
    dragPointer=e.pointerId;
    divider.setPointerCapture(e.pointerId);
    root.classList.add('cl-resizing');
  });
  divider.addEventListener('pointermove',e=>{
    if(e.pointerId!==dragPointer)return;
    const rect=main.getBoundingClientRect();
    if(rect.width) setSplit((e.clientX-rect.left)/rect.width*100);
  });
  divider.addEventListener('pointerup',endDrag);
  divider.addEventListener('pointercancel',endDrag);
  divider.addEventListener('lostpointercapture',()=>{dragPointer=null;root.classList.remove('cl-resizing');});
  divider.addEventListener('keydown',e=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
    e.preventDefault();
    setSplit(e.key==='Home'?22:e.key==='End'?60:split+(e.key==='ArrowLeft'?-2:2));
  });
  divider.addEventListener('dblclick',()=>setSplit(32));
  window.addEventListener('blur',endDrag);
  setSplit(32);
  render();
})();
