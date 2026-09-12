/* Live mode uses a same-origin server bridge; never browser AWS credentials. */
(() => {
  if (new URLSearchParams(location.search).get('data') !== 'live') return;
  const get = async path => {
    const r = await fetch('/artifact-api/v1' + path, {cache:'no-store'});
    if (!r.ok) throw new Error('無法讀取 AWS 資料，請確認服務與權限。');
    return r.json();
  };
  async function list(path) {
    const out=[]; let cursor;
    do { const page=await get(path+(cursor?'?cursor='+encodeURIComponent(cursor):''));out.push(...page.items);cursor=page.next_cursor; } while(cursor);
    return out;
  }
  const fileUrl=(run,id)=>'/artifact-api/v1/imports/'+encodeURIComponent(run)+'/documents/'+encodeURIComponent(id)+'/access?disposition=inline';
  window.ArtifactLibraryAPI={
    async listCases(){
      const cases=await list('/cases');
      return Promise.all(cases.map(async c=>({id:c.case_id,name:c.name,number:c.case_no||'',district:c.district||'',live:true,
        groups:(await list('/cases/'+c.case_id+'/groups')).map(g=>({id:g.group_id,name:g.name,sections:[],landUse:'',selectedRun:g.selected_run_id}))})));
    }
  };
  window.GroupWorkspaceAPI={
    live:true,
    async loadGroup(scope){
      const runs=await list('/groups/'+scope.groupId+'/runs');
      const imported=runs.filter(r=>r.status==='imported');
      const runId=scope.version || imported[0]?.run_id;
      const documents=[{id:'survey',name:'地價區段勘查表',short:'區段勘查'},
        {id:'comparison',name:'比較法調查估價表',short:'比較法估價'},
        {id:'factors',name:'影響地價區域因素分析明細表',short:'區域因素分析'}];
      const sources=[{id:'source-criteria',name:'評價基準明細表',label:'評價基準明細表',kind:'source_criteria'},
        {id:'source-question',name:'地價區段勘查表',label:'地價區段勘查表',kind:'source_survey'}];
      if(runId){
        const run=await get('/imports/'+runId);
        if(run.group_id!==scope.groupId || run.case_id!==scope.caseId)throw new Error('版本與組別不一致');
        for(const doc of documents){
          const type={survey:'survey',comparison:'comparison',factors:'regional_factors'}[doc.id];
          const forms=(run.forms||[]).filter(f=>f.form_type===type);
          doc.segments=forms.filter(f=>f.pdf).map(f=>({code:f.segment_code,pageStart:f.pdf.page_start,pageEnd:f.pdf.page_end,url:fileUrl(runId,f.pdf.document_id)}));
          const first=forms.find(f=>f.pdf);
          if(first){doc.pdfUrl=fileUrl(runId,first.pdf.document_id);doc.pageStart=first.pdf.page_start;}
        }
        for(const source of sources){const d=run.documents.find(d=>d.kind===source.kind);if(d)source.url=fileUrl(runId,d.document_id);}
      }
      return {documents,sources,messages:[],runId,versions:imported};
    },
    async sendMessage(){throw new Error('Agent 尚未接上');}
  };
})();
