/* Standalone prototype. Stable case IDs are independent of editable display names.
 * Replace the demo data / name storage adapter when the case database is ready.
 * No demo case IDs are sent to the existing team's document workflow. */
(() => {
  'use strict';
  const root = document.getElementById('case-library');
  if (!root) return;
  const districts = window.NTPC_DISTRICTS || [];
  const group = (id, section, landUse) => ({id, section, landUse, inputs:2});
  const demoCases = [
    ['sl-01','樹林區','樹人街周邊住宅用地查估案','1110901-99-001',[group('01','P002-00','第一種住宅區'),group('02','P003-00','第一種住宅區'),group('03','P004-00','第一種住宅區'),group('04','P005-00','第一種住宅區')]],
    ['sl-02','樹林區','東榮街住宅用地查估案','1110901-99-002',[group('01','P003-00','第一種住宅區')]],
    ['sl-03','樹林區','啟智街捷運開發區查估案','1110901-99-003',[group('01','P005-00','捷運開發區'),group('02','P006-00','捷運開發區')]],
    ['sl-04','樹林區','鎮前街周邊查估案','1110901-99-004',[group('01','P004-00','第一種住宅區'),group('02','P004-01','第一種住宅區'),group('03','P004-02','第一種住宅區')]],
    ['bq-01','板橋區','文化路周邊住宅用地查估案','1150901-99-005',[group('01','示範區段 A','第一種住宅區'),group('02','示範區段 B','第一種住宅區')]],
    ['bq-02','板橋區','江子翠住宅用地查估案','1150901-99-006',[group('01','示範區段 B','第一種住宅區')]],
    ['js-01','金山區','金山商業用地查估案','1140901-99-007',[group('01','示範區段 C','商業區')]],
    ['ds-01','淡水區','淡海周邊住宅用地查估案','1150901-99-008',[group('01','示範區段 D','第一種住宅區'),group('02','示範區段 E','第一種住宅區')]],
    ['xd-01','新店區','北新路周邊查估案','1150901-99-009',[group('01','示範區段 E','第一種住宅區')]],
    ['xc-01','新莊區','中平路周邊查估案','1150901-99-010',[group('01','示範區段 F','第一種住宅區'),group('02','示範區段 G','第一種住宅區')]]
  ].map(([id,district,name,number,groups])=>({id,district,name,number,groups}));
  const storageKey = 'ntpc-case-library-names-v1';
  let saved = {};
  try { const value=JSON.parse(localStorage.getItem(storageKey)||'{}'); if(value && typeof value==='object' && !Array.isArray(value)) saved=value; } catch (_) { /* Storage may be disabled. */ }
  const cases=demoCases.map(c=>({...c,name:typeof saved[c.id]==='string' && saved[c.id].trim() ? saved[c.id].slice(0,60) : c.name}));
  let selected='', query='', active=null, opener=null, toastTimer;
  const $=id=>document.getElementById(id);
  const esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const count=d=>cases.filter(c=>c.district===d).length;
  const sections=c=>c.groups.map(g=>g.section);
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
    const shown=scope.filter(c=>!query||[c.name,c.number,c.district,...sections(c)].some(s=>s.toLowerCase().includes(query)));
    $('cl-district-title').textContent=searching?'全市搜尋結果':selected;
    $('cl-district-count').textContent=`${shown.length} 件案件`;
    $('cl-district-select').value=searching?'':selected;
    $('cl-district-select').options[0].textContent=searching?'全市搜尋':'選擇行政區';
    root.querySelectorAll('[data-district]').forEach(el=>{const yes=el.dataset.district===selected;el.classList.toggle('selected',yes);el.setAttribute('aria-pressed',String(yes));});
    root.querySelectorAll('[data-label]').forEach(el=>el.classList.toggle('selected',el.dataset.label===selected));
    $('cl-books').innerHTML=shown.length?shown.map(c=>`<button type="button" class="cl-book" data-case="${c.id}" title="${esc(c.name)}" aria-label="開啟${esc(c.name)}，共 ${c.groups.length} 組">${searching?`<span class="cl-book-district">${esc(c.district)}</span>`:''}<span class="cl-document-preview"><img src="img/case-thumbnails/sample-form.png" alt="估價表範例縮圖" loading="lazy"><span class="cl-preview-label">範例預覽</span></span><span class="cl-book-name">${esc(c.name)}</span><span class="cl-book-meta">${c.number.slice(0,3)} 年度 · 案號 ${c.number.slice(-3)}</span><span class="cl-book-footer"><span>${c.groups.length} 組</span></span></button>`).join(''):`<div class="cl-empty"><strong>${searching?'沒有符合條件的案件':'此行政區尚無案件'}</strong><button type="button" class="cl-text-button" id="cl-reset">${searching?'清除搜尋':'返回行政區地圖'} →</button></div>`;
    $('cl-results-message').textContent=showShelf?`${searching?'全市':selected}顯示 ${shown.length} 件案件`:'';
  }
  function choose(name){selected=name;query='';$('cl-search').value='';render();if(name)$('cl-district-title').focus({preventScroll:true});}
  root.addEventListener('click',e=>{
    const district=e.target.closest('[data-district]');if(district)choose(district.dataset.district);
    const c=e.target.closest('[data-case]');if(c)openCase(c.dataset.case,c);
    if(e.target.id==='cl-reset'){if(query){query='';$('cl-search').value='';render();}else choose('');}
  });
  $('cl-home').addEventListener('click',()=>{choose('');$('cl-district-select').focus();});
  window.matchMedia('(max-width: 760px)').addEventListener('change',()=>{$('cl-map-panel').inert=root.classList.contains('has-shelf') && window.matchMedia('(max-width: 760px)').matches;});
  $('cl-back').addEventListener('click',()=>{const previous=selected;choose('');const target=previous?root.querySelector(`[data-district="${previous}"]`):$('cl-district-select');target?.focus({preventScroll:true});});
  $('cl-map').addEventListener('keydown',e=>{if(e.target.dataset.district && ['Enter',' '].includes(e.key)){e.preventDefault();choose(e.target.dataset.district);}});
  $('cl-district-select').addEventListener('change',e=>choose(e.target.value));
  $('cl-search').addEventListener('input',e=>{query=e.target.value.trim().toLowerCase();render();});
  function openCase(id,button){active=cases.find(c=>c.id===id);opener=button;renderDetail();$('cl-dialog').showModal();}
  function renderDetail(){
    const c=active;
    $('cl-detail-title').textContent=c.name;
    $('cl-detail-district').textContent=c.district+' / 案件卷宗';
    $('cl-detail-meta').innerHTML=`<dt>正式案號</dt><dd>${esc(c.number)}</dd>`;
    $('cl-group-count').textContent=`${c.groups.length} 組`;
    $('cl-detail-groups').innerHTML=c.groups.map(g=>{
      const outputs=['地價區段勘查表','比較法調查估價表','影響地價區域因素分析明細表'];
      return `<details class="cl-group"><summary><span><b>${esc(g.name || g.section)}</b><small>${esc(g.landUse)}</small></span><span class="cl-group-summary"><i aria-hidden="true">⌄</i></span></summary><div class="cl-group-content"><p class="cl-group-date">估價基準日 · ${c.number.slice(0,3)} 年 09 月 01 日</p><h4>來源資料</h4><p class="cl-source-placeholder">已提供 2 份資料 · 示範未提供檔名</p><h4>產出書表</h4>${`<ul class="cl-output-list">${outputs.map(name=>`<li><span>${esc(name)}</span></li>`).join('')}</ul>`}</div></details>`;
    }).join('');
    $('cl-rename-form').hidden=true;
    $('cl-title-area').hidden=false;
  }
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
