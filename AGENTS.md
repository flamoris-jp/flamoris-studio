# AGENTS.md

## 2026-10-29 Studio #102

The 24-hour receipt comparison defect is reproduced with real Controller
metadata and a controlled 0.1-second clock offset. Follow
docs/REFERENCE_UPLOAD_RECOVERY.md: validate finite created/expiry times and a
declared lifetime at most 24 hours, tolerate at most one second of future
creation and cap stored expiry to the local declared lifetime. Do not extend
expired inputs or weaken ID/digest/MIME/size/provenance/ownership fences. Surface
only allowlisted receipt reasons, with no raw metadata. Actual production
clock offset and attribution of the reported PNG failure remain unconfirmed.

## 2026-10-29 Studio #101

The owner extended this behavior to Video, Music generation/transcription,
Speech, Raw Intelligence, Assistant send/model selection and personality save.
Use docs/ACTION_FEEDBACK.md. Keep active/unknown request fences, exact model
switch retry identities and read-only permissions; clicking for an explanation
must not create a request identity, send work or change a draft. In-flight
operations and structural/read-only controls retain applicable disabled states.

Image Generate stays clickable for validation feedback, including Service ready
with incomplete/invalid settings. Only an in-flight Image submission disables
it. Preserve the same payload/reference/model gates, no submission on validation
failure, drafts and explicit retry after correction. Use the existing payload
validators for field diagnostics; bypass native form bubbles with noValidate so
clicks and keyboard submission receive the same focused explanation. Remove the
PHASE 1A development badge. Yesterday's historical cause remains unconfirmed.

## 2026-10-29 Studio #99

Repair failed reference-upload byte reservations only after exact upstream deletion
and thumbnail cleanup are confirmed and no execution references the input. Keep
ownership/count tombstones, historical previews and unknown charges. Expose only
allowlisted upload diagnostics, preserve drafts and never replay automatically.
The observed original HTTP 422 cause remains unconfirmed; do not weaken image,
receipt, expiry or account checks as a speculative fix.
The owner's follow-up requests a practical 2 GiB per-user reference budget.
Use finite 8 GiB aggregate / 1,024 per-user rows / 4,096 aggregate rows defaults;
preserve explicit environment overrides, per-file bounds and retention. Record
source defaults separately from actual host configuration and acceptance.

## 2026-10-28 Studio #97

The user requests Studio Video integration after the first real Controller AnimeGen I2V receipt. Follow docs/VIDEO_OPERATION.md. Reuse authenticated Controller HTTP, owned managed inputs, request/execution/input fences and existing MP4 Assets delivery. Do not introduce MCP routing, a second job/GPU authority, raw graph editing, model downloads or Kachinco changes. A successful external test clip is not Studio browser acceptance or general model-quality qualification.

## 2026-10-18 Controller #26

The explicit user request authorizes source implementation, matched MCP/Studio/Hub integration, tests, documentation and reviewable PRs for declarative image graphs. It supersedes older fixed-topology/model-family restrictions for this slice. Follow the [declaration contract](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/DECLARED_COMFY.md). Preserve existing profiles/data, single authority, immutable inputs, ownership and unknown/no-replay. Live deployment, inference and merges remain separate.

## Scope

These instructions apply to the entire repository.

This repository contains FLAMORIS Studio, the creative control center for FLAMORIS.

## Current direction

The latest 2026-10-12 work implements #83: remember Assistant model selection across navigation/reloads under stable per-account browser preferences. Studio #84 is merged and 1.0.5 published; decopon's Updater update succeeded and Server Manager observed Studio running. Preserve allowed-model validation, explicit start/switch authority and immutable handoff payloads. Browser interaction remains operator acceptance. Updater post-success old-version cleanup is pending with `unsafe_storage`; do not describe that as a failed runtime update or force-delete retained files. No Agent, DB schema or provider/runtime configuration change was needed.

The earlier #68 consent-removal scope was delivered in 1.0.4. Agent grants, account/CSRF checks, sessions/history and immutable pending handoff payloads remain enforced. Reference-workflow PR #80 remains a separate unmerged scope.

The assistant model handoff UI/API and source migration are accepted in main
through Studio #66 and Agent #43 after the explicit merge instruction. Preserve
the logical Studio conversation key; Agent owns immutable internal lineage.
Follow docs/MODEL_CONTINUATION.md and AI PROGRESS §4.9. The 2026-10-11 fresh-install checkpoint accepts the operator's Assistant conversation,
model switching and continuation after the Studio 1.0.3 clock-skew fix (#76).
See docs/REAL_MACHINE_ACCEPTANCE.md and AI PROGRESS §4.21. Studio Controller
model discovery/real generation and independent second-account/comprehensive
fence checks remain open. Do not replay successful setup or infer live readiness
for unrelated providers from source/CI or historical pre-reset receipts.

Studio is a creative control plane.

Phase 1 advances through the smallest complete vertical slices needed to submit work, observe jobs, preview results, and retrieve generated assets. The authenticated multi-user shell and Image vertical slice already exist in current main; do not describe them as future scaffolding.

Studio is multi-user from the beginning. Every user-visible execution, result, prompt, attachment, and asset access path must be scoped to the authenticated Studio user/session.

Phase 1 uses a Python FastAPI backend with PostgreSQL-backed local accounts and secure, server-side sessions. Keep domain logic dependent on stable Studio user identity/authorization rather than authentication implementation details so external identity providers can be added later.

Do not revive assumptions from the old Studio architecture that required the Studio server to directly own or permanently store large production projects.

## Authority boundaries

Studio may own:

- Studio UI state and presentation;
- Studio-specific configuration;
- user-facing orchestration;
- references to external jobs and assets;
- Studio-specific history/presets only when explicitly introduced by a later Issue.

Studio must not become a competing authority for:

- GPU runtime state or GPU exclusivity;
- generation jobs/assets owned by Controller through its authenticated HTTP API;
- intelligence execution owned by the internal Runtime/API/vendor adapter;
- Agent personalities, conversations and principal policy owned by `flamoris-ai-agent`;
- project/document state owned by FLAMORIS production applications;
- arbitrary workstation filesystem state that belongs behind `flamoris-studio-client`.

`flamoris-gpu-node-manager` is the provider-neutral local runtime/GPU authority. Studio should not directly encode GPU/VRAM switching logic or make a particular node name part of its architecture.

## Architecture principles

1. **Keep internal execution behind a backend boundary**
   - Studio raw Intelligence uses the shared non-MCP `flamoris_intelligence` adapter; Agent Support uses Agent HTTP API `/api/v1`. Never route either through MCP or MCP Hub.
   - Browser code uses Studio DTOs, never provider credentials or protocol types.
   - The latest 2026-10-05 user instruction authorizes Controller implementation and matched Studio integration. Generation uses authenticated Controller HTTP v1; external MCP shares that same runtime. Keep domain/provider state in Controller and do not restore MCP fallback.
   - Normalize upstream errors/results before frontend publication and preserve scoped admission fences.

2. **Capability first, provider second**
   - User-facing UI should be organized primarily around creative operations such as Intelligence, Image, Video, Music, and Speech.
   - Provider/runtime identifiers may be shown for diagnostics or advanced configuration but must not define the primary product model.

3. **Dedicated editors over one universal prompt form**
   - Prefer purpose-built editors for major workflows.
   - Do not introduce a large schema-driven UI framework in Phase 1.
   - Capability metadata may drive availability and validation without dictating the entire UX.

4. **Job references are not job authority**
   - Studio may retain a reference to a generation-owned job for presentation.
   - The retained Generation domain service remains authoritative for current execution state; Studio uses its authenticated JSON/binary HTTP transport.
   - Do not invent a second durable job state machine unless an explicit design requires one.

5. **Asset references are not file paths**
   - Never expose provider-local or machine-local filesystem paths as the Studio asset model.
   - Generated results should be represented through stable asset references and safe content/download endpoints.
   - Generated-result retrieval is a first-class Studio requirement, not an optional debug feature.

6. **Large project files do not belong on the Studio server by default**
   - Future workstation-local projects/media should flow through `flamoris-studio-client`.
   - Transfer full payloads only when needed; prefer metadata, previews, ranges, or references when sufficient.

7. **Do not duplicate shared foundations**
   - Reuse compatible FLAMORIS shared logging, MCP, diagnostics, and security components when appropriate. Python services use Python-native libraries; do not add .NET-only packages for symmetry.
   - Do not copy shared library source into this repository merely for convenience.

8. **Multi-user isolation is an architectural boundary**
   - Treat authenticated user identity as part of every Studio-facing execution and asset access decision.
   - Upstream generation job IDs and asset IDs are identifiers, not authorization grants.
   - Do not expose raw shared-upstream identifiers as sufficient proof of ownership.
   - Use Studio-owned opaque handles/mappings and verify ownership before status, result, cancel, preview, or download operations.
   - Do not leak another user's prompt, parameters, filenames, result metadata, active job identity, or diagnostics through busy/error responses.

9. **Bound remote and media operations**
   - Explicitly bound request sizes, result sizes, downloads, uploads, timeouts, concurrency, and retries.
   - Never invisibly retry non-idempotent submissions after ambiguous failure.

## Phase discipline

[AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and [Studio #62](https://github.com/flamoris-jp/flamoris-studio/issues/62) govern the authorized Intelligence cleanup. Controller now owns retained generation domain code; Studio calls its authenticated internal API. Retire the old custom ComfyWorkFlow/v3 integration; retain baseline generation, owned assets/inputs and uncertain request reservations. Use `ExecuteFlow` for inference flow and `ComfyWorkFlow` for ComfyUI graphs; existing wire identifiers remain exact.

Follow the current Issue/design document as the source of truth.

Phase 1 should not grow unrelated features such as:

- large-project hosting;
- Studio Client implementation;
- AudioAnalyzer migration;
- Kinetic Typography migration;
- Cutwork/Kachinco/2D integration;
- a generalized workflow designer;
- a speculative universal provider framework;
- durable history/presets unless explicitly scoped.

Prefer complete vertical slices over broad scaffolding.

## Generated assets and downloads

Result UI must distinguish presentation from ownership.

For generated media:

- verify that the authenticated user owns the Studio execution/asset reference before resolving the upstream asset;

- obtain generation metadata/content through the owning Controller API;
- validate media type and size;
- serve content to the browser through an explicit Studio endpoint or equally bounded mechanism;
- do not trust provider filenames/paths as local paths;
- do not leak private deployment topology;
- preserve enough metadata for the user to identify and download the intended output.

## Development workflow

Before substantial changes:

- read README.md and this file;
- read CONTRIBUTING.md and SECURITY.md;
- read the relevant Issue/design document;
- inspect current code and tests;
- inspect the public boundaries of `flamoris-intelligence-mcp`, `flamoris-generation-mcp`, and relevant shared packages;
- identify authority and dependency direction before implementation.

Keep changes focused and commit meaningful units rather than holding a large uncommitted change set.

For larger work, useful commit boundaries include foundation, domain/contracts, MCP integration, UI, tests, and fixes.

## Testing and CI

Normal CI must not require:

- a live GPU;
- private model weights;
- private tunnels;
- personal machine paths;
- paid provider credentials;
- large production media.

Use mocks/fakes and bounded test assets for normal tests.

Add targeted integration tests for Studio gateway contracts and result/download handling.

## Security and privacy

Never commit, log, or return secrets, credentials, API keys, private keys, cookies, or private tunnel identifiers.

Treat prompts, attachments, generated outputs, local-project metadata, and user files as potentially private.

In a multi-user deployment, authorization failures must fail closed. A user must not gain access to another user's execution or asset through guessed/replayed Studio or upstream identifiers.

Validate untrusted provider/Controller responses before using them as URLs, paths, filenames, media types, commands, or structured control data.

## Licensing

Unless stated otherwise, code and documentation are licensed under Apache License 2.0.

Do not add third-party code, models, model weights, datasets, fonts, media, prompts, or generated assets unless their licenses and redistribution terms are compatible and clearly documented.

Generated media does not automatically inherit this repository's code license.

## Support

FLAMORIS does not provide guaranteed individual support.

Repository documentation, Issues, tests, logs, and source code are the primary references. AI-assisted self-support is encouraged.


## 2026-10-13 linked external gallery scope

Studio #86 / Controller #16 add automatic linked-client result registration, category filters and WAV/sidecar detail presentation. The user explicitly authorizes implementation through release and real-host changes. Preserve account/CSRF checks, binding reauthorization, permanent claims/tombstones and generation-owned media authority. The matched Controller pin is required for discovery. Source/CI acceptance and actual individual-credential/provenance/binding acceptance must be recorded separately; never assign historical anonymous jobs automatically.


## 2026-10-13 reference-image continuation

Resume the prepared reference-image PR against current main. Preserve linked external discovery, YuE2 output placement and remembered Assistant models. Match Controller/Generation/Studio dependency pins and new release versions; source CI and real-host model/shared-input acceptance remain distinct.
