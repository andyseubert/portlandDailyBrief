# Reference feature inventory

Inspected October 9, 2026, at upstream commit `0adde78d74f3538f9822957490f386c2805719ed`.

The GitHub API reports no license. The user authorized a GitHub fork; that creates a repository relationship and preserves history, not a license to republish the reference's expressive material. The Portland working tree is independently written. The reference is available through `upstream/master`; no reference code, prompts, news content or assets are used at runtime.

| Capability | Evidence in reference | Verification level | Portland increment |
| --- | --- | --- | --- |
| Daily written brief and archive | `pipeline/TUCSON-BRIEF.md`, `PIPELINE.md`, dated `posts/` HTML, `generate_post.py` | Public instructions and artifacts verified; runtime not exercised | Local text-only brief |
| Agenda previews | `agenda-watch/*-preview.md`, `meeting-watch/*.html`, agenda mining modules | Artifact/module existence verified; live runtime not tested | Official calendar/agenda adapter |
| Recorded/live meeting reports | `ai_reporter_vod.py`, `ai_reporter_live.py`, `news-reports/*.html` | Implementation/artifacts present; access, capture and costs not audited | Independent recoverable civic jobs |
| Development/business public records | `dev_watch_*.py`, `public_record_liquor*.py`, `around-town/`, `public-record/` | Source-specific modules/artifacts present; no assumed Portland equivalent | Verified Portland notices first |
| In-depth reporting | `ORIGINAL-JOURNALISM.md`, `render_indepth.py`, `in-depth/` | Documents/artifacts verified; editorial practice not independently audited | Evidence/editorial schema, later reporting |
| Search/question answering | `rag/build_index.py`, `rag/ask.py`, `rag/server.py`, `RAG.md` | Modules documented/present; retrieval quality not tested | Approved current corpus only; no chat yet |
| Responsiveness/expanded structured data | `ROADMAP.md`, `responsiveness/PLANNING.md` | Proposed/gated in roadmap; not proof of working coverage | Deferred until actual source validation |
| Distribution/audio/mascot/crosswords | Numerous named docs/modules/assets | Out of scope; not executed | Excluded |

Reference dependencies visible in docs include OpenClaw/local Linux paths, hosted text models, transcription and static publishing. A full runtime dependency audit was not performed. None are assumed requirements for the independent Mac proof of concept.

Source: [pinned reference tree](https://github.com/philote/tucson-daily-brief-site/tree/0adde78d74f3538f9822957490f386c2805719ed). GitHub fork: [andyseubert/portlandDailyBrief](https://github.com/andyseubert/portlandDailyBrief). Its Actions were explicitly disabled and Pages is not enabled.

Design interviews: [TWiT 1096](https://twit.tv/posts/transcripts/week-tech-episode-1096-transcript), 02:19:03–02:26:16; [Intelligent Machines 883](https://twit.tv/posts/transcripts/intelligent-machines-883-transcript), 00:08:24–00:16:12 and 00:23:29–00:26:07. These are labeled automatic transcripts. They inform the independent collection/reporting/filings design and human-review workload; they do not verify Portland actions, available feeds, costs or complete runtime parity. Use Portland measurements rather than interview cost claims.
