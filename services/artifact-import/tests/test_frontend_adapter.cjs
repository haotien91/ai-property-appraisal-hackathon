const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('地價智審_AI_Offline_Candidate_v1/frontend/app/js/artifact-adapter.js','utf8');
const payload={
 '/cases':{items:[{case_id:'c',name:'案件',case_no:'114',district:'樹林區'}]},
 '/cases/c/groups':{items:[{group_id:'g',name:'組別'}]},
 '/groups/g/runs':{items:[{run_id:'r',status:'imported'}]},
 '/imports/r':{case_id:'c',group_id:'g',forms:[{form_type:'survey',segment_code:'P1',pdf:{document_id:'pdf1',page_start:2,page_end:3}},{form_type:'survey',segment_code:'P2',pdf:{document_id:'pdf2',page_start:1,page_end:1}}],documents:[]}
};
const ctx={window:{},location:{search:'?data=live'},URLSearchParams,fetch:async url=>({ok:true,json:async()=>payload[url.replace('/artifact-api/v1','')]})};
vm.runInNewContext(source,ctx);
(async()=>{
 const cases=await ctx.window.ArtifactLibraryAPI.listCases();assert.equal(cases[0].groups[0].id,'g');
 const data=await ctx.window.GroupWorkspaceAPI.loadGroup({caseId:'c',groupId:'g'});
 assert.equal(data.documents[0].segments.length,2);
 assert.notEqual(data.documents[0].segments[0].url,data.documents[0].segments[1].url);
 assert.equal(data.documents[1].pdfUrl,undefined);assert.equal(data.sources[0].url,undefined);
 await assert.rejects(()=>ctx.window.GroupWorkspaceAPI.loadGroup({caseId:'other',groupId:'g'}));
 const mock={window:{},location:{search:''},URLSearchParams};vm.runInNewContext(source,mock);assert.equal(mock.window.GroupWorkspaceAPI,undefined);
 console.log('PASS: live mapping, split PDFs, missing PDFs, scope isolation, mock unchanged');
})();
