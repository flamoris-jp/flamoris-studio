# Multimodal Studio and contextual Agent panel

Status: proposed product/integration design, 2026-10-02. Coordination: [FLAMORIS AI #15](https://github.com/flamoris-jp/flamoris-ai/issues/15). This does not implement new editors or alter the current Image contract.

## Baseline and responsibility

Implemented result foundation: [MULTIMEDIA_RESULTS.md](MULTIMEDIA_RESULTS.md)
specifies additive role/media/source DTO metadata, controlled audio/video and
bounded immutable single-range/attachment retrieval. The remaining editor,
Agent mapping and orchestration sections below still describe gated work.

Main `f8301ae6b900c367f67e4aaf4f25c0aa2be5db0e` already has multi-user accounts/sessions, Image generation, Styles/preferences, ready graph-free Workflow discovery, authorized reference-image snapshots, Generated catalog and bounded transfers. Keep these working. Current Image-specific DTOs and public roles are not a universal multi-media contract.

Studio owns editor drafts, per-user request/catalog mappings and presentation. Generation owns media workflow/job/input/asset validation and readiness; Agent owns persistent assistant identity/conversations/policy; Intelligence owns raw inference; Runtime and GPU Node Manager retain execution/resource/lifecycle authority. Browser code speaks Studio DTOs, never MCP or provider transport.

## Navigation and editors

| Category | Operations | Profile-aware editor |
| --- | --- | --- |
| Image | Generate / Decompose | Current Image editor; authorized decomposition source and layer/PSD results |
| Music | Generate / Transcribe | Style, lyrics, supported ABC/plan, duration, seed; source audio for transcription |
| Speech | Generate | Text, supported voice/reference audio and style options |
| Video | Generate / Motion Reference | Initial image, prompt, resolution, duration, FPS and seed |
| Assistant | Standalone Agent workspace | Agent/session, conversation messages and authorized context |

Reuse workflow/model selection, Run/Cancel, execution observation and result components. Major editors remain dedicated; supported extra scalar/enum fields may appear in Advanced. Current Image LoRA ordering and semantic roles remain intact. Models are filtered by selected Workflow compatibility/qualified domain, not offered from a global unqualified list.

An unavailable operation may remain visible with a reason, but cannot submit. Unknown profiles do not generate a generic form. Music transcription is a Music operation, not an unrelated Analysis page. Provider names are diagnostic/advanced metadata only. The new `video.generate` and `video.motion_reference` identifiers are proposed canonical ids. Existing Generation #25 uses candidate names `video.image-to-video` / `video.motion-reference`; reconcile them in that owning issue and reviewed catalog/descriptor migration before rollout. Until then unknown spellings remain unavailable; never infer aliases by replacing punctuation or silently advertise both as implemented.

Raw Intelligence #2 remains an explicit advanced inference workspace if delivered; its existing model/sampling request contract is distinct from Agent conversation assistance.

## Discovery and availability

Backend aggregates safe capability descriptors from owning services. Proposed DTO fields include capabilityId, category, operation, executionMode, supportedProfileRevisions, available, reason and workflow summaries. Retain source service/contract revisions server-side. Capability metadata populates operations; Generation's exact ready descriptors populate executable Workflow choices.

Known API/catalog support, upstream reachability, provider availability, infrastructure readiness and Workflow ready are separate. Hub static tools/list does not establish availability. Cache metadata with bounded freshness, and recheck exact workflow id/version/digest/current readiness on build/submit. A stale or unknown descriptor disables Run, preserves the draft and refreshes discovery; never selects a different workflow automatically. Server-side validation remains decisive if UI state races.

Do not expose graphs, provider paths/URLs, credentials, mutation manifests, runtime lock names or Generation input/asset ids to the browser. Diagnostic errors omit other users' prompt, job, Agent principal and queue identities.

## Studio execution model and migration

Retain an owner-scoped opaque execution record with source, operation/profile, immutable submitted request snapshot, revision and server-side upstream reference. Before dispatch, freeze the authorized input/draft/context revision and upstream request identity in that owner-scoped record. After transport uncertainty, retain that same snapshot for reconciliation; neither reconnect nor a new draft/provider selection may mutate or replay the pending attempt. Generation executions store media job references; Agent requests store scoped Agent request/conversation references; raw Intelligence records a synchronous request-scoped correlation only. There is no upstream Intelligence polling job.

UI states may include queued, running, completed, failed, cancel_requested, cancelled and unknown/reconciliation_required. Translate honestly from each owning contract. Accepted transport loss cannot become failed-with-safe-retry; current Agent duplicates mean an attempt existed, not that cached success is retrievable. No fresh-ID automatic replay.

Introduce source-discriminated DTOs and additive DB migrations/readers before moving consumers. Existing Image endpoints are compatibility wrappers over the same authorization/application service, not an independent job engine. Preserve old result rows and request snapshots; old clients continue to work through the reviewed compatibility window. Generic endpoint paths below are proposed, not existing routes:

| Proposed route | Behavior |
| --- | --- |
| GET /api/capabilities | Safe user-scoped operation/workflow availability |
| GET /api/generation/workflows; GET /api/generation/models | Versioned graph-free metadata |
| POST /api/executions | Discriminated dedicated request DTO; exact pins and owned inputs |
| GET /api/executions/{id}; POST .../{id}/cancel | Authorized reference observation; only supported cancellation |
| GET /api/executions/{id}/assets | Metadata-first result sync |
| GET /api/assets/{id}; GET .../{id}/content; GET .../{id}/download | Owner-checked media metadata and bounded retrieval |
| POST /api/inputs; GET /api/inputs/{id}; DELETE .../{id} | Authorized generated-asset snapshot lifecycle |
| GET /api/assistant/availability; POST /api/assistant/requests | Agent-backed availability and one bounded assistance turn |

Separate request DTOs carry category semantics; lifecycle is shared. Input POST initially accepts an owned Studio asset reference, not upload/path/URL. Agent transport/session endpoint shapes must follow the reviewed AI Agent #18 contract rather than invent a Studio-only upstream protocol.

## Asset and result presentation

StudioAsset records owner, execution, role, mediaKind, mimeType, displayName, size, safe typed metadata and preview status, with upstream references only server-side. Keep current durable metadata-first synchronization and deletion tombstones. Failure to generate a preview does not remove a catalog row; lazy retrieval does not require jobs.result to download every output.

| Kind | Renderer | Failure/unsupported behavior |
| --- | --- | --- |
| image | Current image/thumbnail view | Retry/reload preview and download |
| audio | Audio player | Format-aware no-preview and download |
| video | Video player | Format-aware no-preview and download |
| midi | MIDI metadata/file view | Download first; playback only after a separately reviewed synthesizer |
| score | Escaped ABC/score text or approved score renderer | Bounded text/download; never execute notation code |
| data | Bounded structured JSON view | Safe escaped text/download |
| document/archive/unknown | File metadata view | Attachment download |

Select renderers by validated kind/MIME/profile metadata, not filenames. Escape text, sanitize Markdown and filename headers, and never inject provider SVG/HTML/JSON into executable browser content. Group outputs by explicit roles/cardinality. PSD/layers use a bounded manifest and authorized child assets, not paths inside an archive.

Use Generation assets.prepare/read as standard multi-media transport, retaining integrity/offset checks and bounded concurrency/actual-byte limits. assets.get remains the compatible small-image path. Implement HTTP Range for audio/video as a separately tested adapter over those bounded reads: valid single ranges, 206/416, stable representation digest/ETag, content length and strict authorization for each new request. Do not open an unbounded stream or whole-file base64 fallback. Random-access reads require the same prepared whole-object digest, while Generation checks its pinned file-identity receipt and Studio independently checks each returned chunk_sha256. A chunk hash alone is not a proof against the whole-object hash; this depends on the trusted authenticated Generation receipt contract. Full downloads additionally verify the whole-object digest. Do not claim whole-file rehash for each isolated range. Deleted/replaced content invalidates the representation.

Current quotas/deadlines remain unless a reviewed media profile justifies a bounded change. Cataloged unmaterialized outputs may still be unavailable after upstream restart; display that limitation without regenerating work.

## Managed input mapping

Replace future request referenceInputId with a role map `inputs: {initial_image: <Studio handle>, source_audio: <Studio handle>}` in a versioned DTO. Preserve existing Image wrapper behavior. Backend verifies every handle's owner, expiry, source provenance and role/MIME compatibility before resolving server-side Generation IDs; arbitrary caller IDs fail closed. Copying an owned asset creates an immutable snapshot whose source deletion does not mutate it.

Picker filters by actual profile support, not just media kind. Source audio and reference voice need reviewed decode/duration limits upstream; MP3 is not usable because a dropdown accepts it. Existing initial-image semantics do not imply identity/style/mask conditioning. Local upload remains separate acceptance scope.

## Right-side Agent panel

Every editor mounts one reusable contextual Agent panel; it collapses into a drawer on narrow screens. Standalone Assistant uses the same gateway/session component. Controls select an allowed Agent and conversation, not a provider URL/key. Optional model-target selection is an Agent-owned policy feature exposed through an approved Agent contract, not a Studio provider registry.

User explicitly chooses Attach current draft, selected assets and Workflow metadata. Backend constructs a bounded context envelope from authorized product state. It contains category/operation, draft revision and permitted public fields, selected exact Workflow identity and selected asset safe metadata. Raw binaries are not sent by default. Context export policy covers the Agent's selected previous transcript as well as the current attachments/question; permitting a new remote question cannot implicitly export earlier local-only history. The Agent checks the complete assembled request under its owning policy. Attachment retrieval for Agent tools requires a scoped approved gateway; no arbitrary URL/path or opaque ID is ambient authority. Retention is explicit: context may become Agent conversation content but is not automatically promoted to long-term Memory.

Current Agent ask accepts text and an optional closed parent only. Structured context/proposals/availability are extensions requiring Agent-owned schema review. Do not pass new fields to current ask or smuggle authority inside prompt text. A context-as-untrusted-text compatibility path could only serve an explicitly scoped fixed-principal deployment; it is not the shared production integration.

AI Agent #18 is mandatory before multi-user sharing. Studio's authenticated user maps through an authorized server-side account/principal binding. Agent validates human/agent/project membership and binds it immutably at session establishment. Service credentials do not identify the human; browser-supplied principals cannot impersonate users. UI conversation handles are Studio-scoped mappings, and every continuation/status/proposal access checks both Studio ownership and Agent principal. Do not share one upstream principal/session across Studio users.

Panel availability is Agent ask availability for the chosen principal/policy. Display ready, offline, starting, busy or unavailable/unknown with safe reasons; send is disabled when unusable while draft/question/selected context remain. Agent health(alive/dependencies=not_checked) is insufficient. Local-only Agent + stopped runtime is offline; API-authorized Agent may remain usable. Studio never activates a GPU or switches an Agent to an API because a local runtime failed. Refresh bounded availability after reconnect and recheck on send; preserve state and do not replay messages.

Conversation authority remains in Agent. Studio retains only product session mapping and unsent draft state; persisted view caches need scoped retention and invalidation. Logout/account/Agent/session changes must clear inaccessible view caches while keeping only explicitly permitted local drafts. Browser histories must not expose previous users' context.

## Proposals and explicit Apply

Initial assistance returns bounded text only. Later structured proposals may edit allowed draft fields or suggest a new pinned Composition. Proposal DTO includes owner-scoped handle, source Agent/request, base draft revision, target fields, bounded patch and provenance; model output cannot choose arbitrary field paths or tool commands.

Show diff and user Apply. Backend compares current revision, reauthorizes inputs and validates the target DTO. Conflict returns stale_draft without overwriting newer work. Applying parameters does not submit generation. Applying a Composition creates a new Generation candidate version through a separately granted authoring path; most Studio users need only consume ready definitions. A candidate cannot become production-ready merely because Apply succeeded. Registration permissions, static validation and automatic composed verification remain upstream.

## Progress and composition inspection

Foldable stage tree uses sanitized public aliases/status from owning structured events. Show nested steps without internal provider graphs. Chipsy-family animations may reflect stage transitions; they are decoration over observed state. Polling/event replay must not trigger work, invent percentages or regress terminal status. Events/proposals/assets are owner-scoped. Missing events show unknown/stale, not completed.

## Delivery and acceptance

Deliver shared DTO/catalog/result foundation first, then Music Generate/Transcribe, Speech, Video and Image Decompose. Static include selection can consume ready upstream compositions without a Studio graph designer. Agent principal isolation/context gateway is independent but must pass before the right-side panel is enabled for shared users.

Focused checks: old Image UI/Styles/LoRA/reference/use-settings behavior; unsupported profile disabled; stale pins at submit; two users' assets/inputs/conversations/proposals/progress isolated; source ownership checked on every request; metadata present despite failed preview; valid/invalid ranges and digest mutation; Agent dependency unknown and runtime offline; configured API Agent still usable; reconnect with unsent drafts and an unchanged uncertain-request snapshot, with no replay; local-to-remote refusal for prior local-only history; unsupported candidate video-id aliases; stale-draft Apply and exact Workflow candidate verification. Normal CI uses fake MCP/Agent services and bounded fixtures; actual provider and multi-user deployment evidence is separate. No runtime activation, provider keys, large projects or desktop document authority move into Studio.

## Tracking

Owning follow-up: [#39](https://github.com/flamoris-jp/flamoris-studio/issues/39). Cross-repository acceptance stays coordinated by [FLAMORIS AI #15](https://github.com/flamoris-jp/flamoris-ai/issues/15).
