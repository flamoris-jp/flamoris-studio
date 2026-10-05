# FLAMORIS Studio architecture

Authority: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and
[Studio #62](https://github.com/flamoris-jp/flamoris-studio/issues/62).
This document describes the current source after the internal connection cleanup.
[The previous architecture](https://github.com/flamoris-jp/flamoris-studio/blob/cdaa2f93b39fbdeae1909d45ee22c84cda6eeef7/docs/STUDIO_ARCHITECTURE.md)
remains historical. The detailed source/test inventory is in
[architecture cleanup](ARCHITECTURE_CLEANUP.md).

## Product and ownership

Studio is a web creative control plane, with dedicated Intelligence, Assistant,
Image, Speech and Music editors. It owns presentation, user-facing orchestration,
local account/session authorization and owner-scoped correlation/catalog metadata.
It is multi-user from the beginning. It does not own model execution, Agent
conversations/personality, host GPU lifecycle, generation jobs/media binaries or
production applications' project/document state.

| Operation | Current boundary | Authoritative execution/state owner |
| --- | --- | --- |
| Raw Intelligence | Shared non-MCP `flamoris_intelligence` adapter to configured local vendor HTTP API | Provider executes; Studio owns its admission and correlation fences |
| Agent Support | Agent JSON HTTP API `/api/v1` | Agent owns personality, principal sessions, model/export policy, conversations and durable Agent fences |
| Image, native Speech/Music, managed inputs and assets | Authenticated Controller HTTP `/api/v1/generation` | One Controller runtime shared with external MCP |
| GPU runtime lifecycle | No Studio activation/switching implementation | GPU Node Manager |
| Large desktop-local projects | Future Studio Client boundary | Owning production application/client |

Internal Intelligence and Agent calls do not use MCP or Hub. Agent is optional;
raw inference and generation require no persona or Agent conversation. Generation also uses a direct non-MCP contract. The facade hosts one Controller
shared by its external tools and internal HTTP adapter; Studio does not construct
a JobStore or own a second generation reservation. See [the generation contract](GENERATION_CONTROLLER.md).

Runtime ExecuteFlow is inference flow; its compiled ExecutionPlan remains distinct.
ComfyWorkFlow means ComfyUI graph/API JSON. Studio does not implement either engine,
move provider graph construction into Runtime or create a general execution service.
Existing wire/configuration names retain their real spelling.

## Backend and browser contracts

The Python FastAPI backend uses PostgreSQL accounts, server-side cookie sessions,
CSRF and per-user application DTOs. React/TypeScript/Vite render the browser UI.
The browser never holds provider credentials, upstream protocol objects, private
endpoint settings or filesystem paths. Text, Markdown and metadata remain escaped;
model output cannot authorize tools, change trusted policy or execute scripts.

Backend gateways normalize bounded upstream results/errors before publication.
Raw Intelligence uses the shared neutral library with exact served model aliases,
operator context/output limits and approved-local data-flow configuration. It has
no remote export/fallback policy. Its synchronous result is terminal; Studio does
not invent an upstream inference polling job.

Agent uses plain versioned HTTP DTOs and the same Agent domain service as the
separate external MCP facade. A service token identifies the backend delegator;
principal membership/grants and immutable session identity remain separate checks.
The exact version-1 operation catalog must match core or explicitly enabled
settings mode. Old `/mcp` URLs fail, without protocol fallback or resubmission.

Intelligence/Agent credentials and approved endpoint/model configuration are
operator-controlled. Personality text and requests cannot select arbitrary URLs,
paths, models or credentials. Remote Agent selection retains explicit consent for
all sent personality/history/question/attached context, plus independent grants.
Drafts and prior/model messages remain untrusted context below system policy.

## Retained generation and retirement

The Image editor selects only original builtin `text-to-image` and
`text-to-image-lora` descriptors. Schema-1 build identity, bounded parameters,
model discovery and ordered LoRA semantics remain. Custom definition/version pins,
v3 composition and custom/img2img dispatch are retired in both browser and server.
A restored retired selection must be explicitly replaced with a supported builtin.
It cannot silently retarget a draft.

Owned immutable inputs, uploads and asset routes remain available. Neither
retained builtin accepts reference images; a reference attachment blocks submission
until the user explicitly removes it. Historical execution/input relationships and
uncertain submissions continue to prevent premature deletion. Controller extraction changes the upstream transport while preserving these
fences. No reference-image feature, DB schema or data migration is added.

Native no-reference Japanese Speech, Music generation and owned-WAV transcription
remain opt-in behind their exact Generation descriptors. Configured availability
is not evidence of a live model/GPU run. Additional providers, Video and reference
media require their own reviewed contracts and acceptance.

Studio stores request snapshots and opaque owner-scoped upstream mappings rather
than taking over generation execution. It normalizes submit/status/result while
the upstream authority determines live state. An upstream job/input/asset UUID is
an identifier, never an ownership grant.

## State, authorization and uncertainty

Every execution, attachment, preview, download and deletion checks authenticated
Studio ownership. Another user's guessed/replayed Studio or upstream ID grants
nothing. Shared upstream services do not create shared user-visible histories.
CSRF protects mutations and paid/remote admission paths.

Studio holds no row transaction across private network I/O. It commits existing
owner-scoped correlation/request fences before dispatch, then rechecks login,
configuration, grants and session ownership immediately before sending and before
publishing. Revocation withholds results; it does not assert that already admitted
provider work stopped.

Ambiguous non-idempotent submission remains uncertain and cannot be automatically
replayed. Matching request UUIDs preserve the existing duplicate/frozen-body
semantics. No fresh-ID retry, hidden provider fallback, second queue or second
job store is introduced. Source deletion never clears durable unknown reservations,
conversations, stored definitions, assets, inputs or evidence.

Agent remains the sole transcript/personality authority. Studio stores session,
conversation/request references and presentation metadata, not a competing Agent
transcript or model policy database. Model/personality changes preserve unsent
drafts and require explicit session choices. Existing conversation snapshots do
not silently change after a personality revision.

## Assets and bounded I/O

Generated media lives with the generation asset authority. Studio persists safe
metadata, generated display names, ownership, tombstones and storage locators;
large binary media is not stored in PostgreSQL. Asset references are not paths.
Metadata synchronization precedes retrieval; a preview failure does not remove
catalog entries. Tombstones prevent unintended reimport after deletion.

Preview, thumbnails, immutable ranges and attachment downloads verify owner,
media type, size, bounded chunks and digests. Existing concurrency/aggregate limits
and explicit deletion behavior remain. Deleting a managed copy need not delete the
original provider output. Unmaterialized results remain subject to their original
execution mapping; source cleanup does not fabricate missing content.

All private operations bound request/response size, context, timeout and concurrency.
Upstream metadata/paths are untrusted. Redirects, compressed private responses,
arbitrary URLs/filesystem paths and automatic retries are refused. Safe public
errors and provenance omit credentials, private topology and raw diagnostics.

## Verification and operational boundary

Normal CI uses disposable PostgreSQL, bounded HTTP fixtures, no GPU/weights or paid
credentials, browser tests/build and the production container. A pinned Agent test
extra exercises the real Studio gateway -> Agent HTTP -> Agent session -> shared
provider adapter. Synthetic principal/conversation stores establish transport and
message/provenance compatibility; the owning PostgreSQL suites verify durable
state, grants, duplicate handling and ownership.

Tests cover two users, CSRF, login/configuration revocation, frozen/uncertain
requests, direct model identity, partial/invalid output, retired selection denial,
input/asset ownership, bounded downloads/uploads and safe UI output. See
[raw Intelligence](RAW_INTELLIGENCE.md), [scoped assistant](SCOPED_ASSISTANT.md),
[assistant settings](ASSISTANT_SETTINGS.md), [Speech](SPEECH_OPERATION.md),
[Music](MUSIC_OPERATION.md), [media results](MULTIMEDIA_RESULTS.md) and
[deployment](DEPLOYMENT.md) for their retained contracts.

Source merges do not authorize operational rollout. Before a separately approved
cutover, inventory old endpoint/mode settings and outstanding work, reconcile
custom/delegated generation under its previous matched version, retain journals,
leases and outputs, and follow existing backup/migration procedures. Do not clear
fences or retarget old sessions to manufacture readiness. Actual provider/runtime,
restart and two-account acceptance remain separate from synthetic CI.
