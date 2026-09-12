# Case library design

Mode: Redesign / overhaul of homepage only. Preserve HTML/CSS/JS, case-new.html, stable case IDs, name-storage key and document processing routes.

Audience: appraisal reviewers on laptop/desktop. Quiet document-management interface. Variance 4, motion 4, density 8, asset dependence 6, brand fidelity 7.

One toolbar: existing landmark wordmark, global search, district selector, one create action. Initial map centered; selection opens shelf from right while map contracts left. Mobile shelf covers map. Reduced motion supported.

Tokens: #111113 background, #1c1c1f surface, #343437 border, #e41779 accent, Noto Sans TC. Solid surfaces, 4–8px radius, no glow.

Thumbnail: img/case-thumbnails/sample-form.png is rendered from frontend/mock/pdf/表1_Golden_Case.pdf. Shared sample only, explicitly labeled 範例預覽; not the document of each demo case. Replace with case-specific thumbnail assets when database integration is ready.

Cases are demonstration data. District boundary provenance is in ntpc-map-source.md.

Homepage entry update: left-side “選擇行政區 / 查看該區案件，開啟估價組別。” guides users toward the larger right-hand map. Selection immediately highlights the district; the guide exits over 200ms, then map moves left and shelf enters from right over 450ms. Closing reverses the sequence. Subsequent district changes update content without repeating entry. All 29 labels render, including 永和 and 蘆洲; no area threshold hides small districts. Mobile uses a compact guide above the map. Reduced-motion preference removes transitions.

Information model: district → case → group. A case may contain multiple groups; each group consumes 2 input files and produces the 3 linked appraisal forms. The shelf card shows the group count, while the case drawer exposes each group and its outputs.

Workspace extension: single-group cases open directly; multi-group cases use the drawer as a group picker. The full-viewport workspace preserves the library DOM and scroll position. Documents and chat share the selected group; document changes do not reset conversation. Desktop split is draggable and keyboard operable; narrow screens stack documents above chat. Existing colors and typography are preserved, with solid surfaces and no new animation.

Mock adapter: define `window.GroupWorkspaceAPI` before `group-workspace.js` to replace the in-memory implementation. `loadGroup({caseId, groupId, version})` resolves `{documents, messages}`. Documents have `id`, `name`, `short`, and `rows` arrays of `[label, value, fieldId]`. `sendMessage(scope, {text, documentId})` resolves `{text, citations}`; each citation has `documentId`, `fieldId`, `label`. Scope is keyed by all three IDs. Pending responses are retained in their original group when users switch. Mock history lasts for the page session only. No network request is sent to AWS.

The document viewer currently renders explicitly labeled HTML fixtures, not real PDFs or case-specific values. Source-file placeholders have no invented filenames. Production integration must replace these fixtures, introduce actual document versions and map citations to PDF coordinates/pages. This workspace provides no review-status controls.

PDF layout update: default reader embeds the unchanged official `data/sources/competition/查估書表範本.pdf`, copied to `app/data/pdf/appraisal-sample.pdf`. Survey/comparison/factors tabs open pages 1/3/2 of this shared six-page sample. Native PDF zoom/page controls and `view=FitH` are browser-dependent. Hidden frames are retained per group/document to preserve native reading state; exact position is not controlled by this mock. Chat defaults to 390px, adjustable 320–520px. Narrow screens switch between document and chat instead of stacking. HTML fixtures remain under “互動算例”; mock citations explicitly open those fixtures, never claim PDF field-level coordinates. PDF coordinate highlighting remains future integration work.
