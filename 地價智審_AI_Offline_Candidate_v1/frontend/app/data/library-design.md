# Case library design

Mode: Redesign / overhaul of homepage only. Preserve HTML/CSS/JS, case-new.html, stable case IDs, name-storage key and document processing routes.

Audience: appraisal reviewers on laptop/desktop. Quiet document-management interface. Variance 4, motion 4, density 8, asset dependence 6, brand fidelity 7.

One toolbar: existing landmark wordmark, global search, district selector, one create action. Initial map centered; selection opens shelf from right while map contracts left. Mobile shelf covers map. Reduced motion supported.

Tokens: #111113 background, #1c1c1f surface, #343437 border, #e41779 accent, Noto Sans TC. Solid surfaces, 4–8px radius, no glow.

Thumbnail: img/case-thumbnails/sample-form.png is rendered from frontend/mock/pdf/表1_Golden_Case.pdf. Shared sample only, explicitly labeled 範例預覽; not the document of each demo case. Replace with case-specific thumbnail assets when database integration is ready.

Cases are demonstration data. District boundary provenance is in ntpc-map-source.md.

Information model: district → case → group. A case may contain multiple groups; each group consumes 2 input files and produces the 3 linked appraisal forms. The shelf card shows the group count, while the case drawer exposes each group and its outputs.
