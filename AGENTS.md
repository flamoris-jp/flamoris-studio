# AGENTS.md

## Scope

These instructions apply to the entire repository.

This repository contains FLAMORIS Studio, the creative control center for FLAMORIS.

## Current direction

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
