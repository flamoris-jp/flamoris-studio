# FLAMORIS Studio Architecture

Status: Phase 1 design  
Repository: `flamoris-jp/flamoris-studio`

## 1. Purpose

FLAMORIS Studio is the web-based creative control plane for FLAMORIS.

The first release focuses on an AI Prompt Console that lets a user choose a creative domain, enter domain-specific generation/intelligence parameters, submit work through MCP, observe execution, inspect results, and retrieve generated assets.

The central architectural rule is:

> **FLAMORIS Studio is a creative control plane, not a runtime authority or project-file server.**

Studio coordinates existing authorities. It does not replace them.

Studio is **multi-user by design**. Authentication and authorization are part of the Phase 1 architecture, not a later retrofit. User isolation applies even when multiple users share one Generation MCP, Intelligence MCP, or GPU runtime.

## 2. Phase 1 goals

Phase 1 establishes the new Studio architecture and the first complete AI execution paths.

Target categories:

- Intelligence
- Image
- Music

The shell also reserves navigation for:

- Video
- Speech

Video and Speech are not required to execute in Phase 1.

Phase 1 must provide:

- a new web Studio shell;
- authenticated multi-user sessions;
- per-user authorization for Studio executions, results, cancellation, previews, and downloads;
- dedicated editors per creative category;
- a backend MCP client boundary;
- capability/availability discovery;
- job submission/status/result presentation where the upstream contract is job-based;
- media-aware result presentation;
- generated-asset listing;
- safe per-asset download from the Studio UI;
- explicit handling of unavailable upstream capabilities;
- bounded errors, timeouts, cancellation behavior, and media retrieval;
- normal CI without a live GPU or private deployment.

## 3. Non-goals

Phase 1 does not include:

- Studio Client implementation;
- permanent hosting of large production projects;
- arbitrary workstation file access;
- AudioAnalyzer migration;
- Kinetic Typography migration;
- Lyrics / Timeline migration;
- Cutwork integration;
- Kachinco integration;
- FLAMORIS 2D integration;
- persistent prompt presets;
- a general workflow designer;
- a universal schema-driven form renderer;
- Studio-owned GPU/runtime switching;
- Studio-owned provider routing;
- a second Generation job/asset database;
- a second Intelligence task authority.

Phase 1 uses ASP.NET Core Identity with PostgreSQL-backed local accounts and secure cookie authentication as the initial implementation. Keep identity access behind standard ASP.NET Core authentication/authorization boundaries so OIDC or another provider can be added later without coupling generation/application logic to local-account internals. Anonymous cross-user access is not acceptable.

These may be introduced only by later design/Issues.

Phase 1 **does** persist Studio ownership/catalog metadata for users, executions, and assets. This persistence supports multi-user authorization, restart-safe references, basic history, and future Phase 2 features without making Studio the execution or binary-asset authority.

## 4. Current upstream reality

Phase 1 design must distinguish implemented contracts from planned contracts.

### `flamoris-generation-mcp`

Generation MCP already owns a provider-neutral generation path with:

- `system.health`;
- `capabilities.list` / `capabilities.get`;
- `models.list` / `models.get`;
- `workflows.list` / `workflows.build` / `workflows.save`;
- `jobs.submit` / `jobs.status` / `jobs.result` / `jobs.cancel`;
- `assets.list` / `assets.get`.

The current implemented capability is:

```text
image.generate
  provider: comfyui
  runtime: janku
  workflows:
    - text-to-image
    - text-to-image-lora
```

Generation MCP remains the authority for generation workflow identity, generation job state, provider coordination, and generated assets.

### `flamoris-intelligence-mcp`

The repository and architectural boundary exist, but its runtime/tool contract is not yet implemented.

Studio must not invent a private Intelligence contract and later force Intelligence MCP to match it.

### Music

Music belongs behind Generation MCP, but the YuE2-facing capability/workflow contract is not yet implemented.

Studio may define the intended user experience, but executable Music integration must wait for the real Generation MCP capability.

## 5. Phase 1 delivery slices

Phase 1 is delivered as several vertical slices sharing the same architecture.

### Phase 1A: Studio foundation + Image vertical slice

This is the first executable path and should be implemented first.

It proves:

```text
Browser
  -> Studio backend
  -> Generation MCP
  -> workflow
  -> job
  -> asset
  -> preview
  -> download
```

Phase 1A is the architecture validation slice.

### Phase 1B: Intelligence

Add executable Intelligence behavior only after `flamoris-intelligence-mcp` defines its public request/result contract.

The Studio shell/editor may exist before then, but it must render an explicit unavailable/not-configured state rather than a fake backend.

### Phase 1C: Music

Add executable Music behavior after Generation MCP publishes the relevant music capability/workflow contract.

The dedicated Music editor may be prepared earlier, but request mapping must follow the real Generation MCP contract.

## 6. Technology stack

### Frontend

- React
- TypeScript
- Vite

The frontend is a single-page application.

Phase 1 should prefer ordinary React components, hooks, and a small typed API client over a large frontend framework.

Do not introduce SSR, server components, or a schema-generated form framework unless a later requirement proves the need.

### Backend

- ASP.NET Core
- .NET 10
- Entity Framework Core
- PostgreSQL (shared infrastructure hosted on decopon, using a Studio-specific database or schema and least-privilege credentials)

The backend owns:

- Studio HTTP API;
- MCP client connections;
- Studio-facing DTO normalization;
- capability/availability aggregation;
- generation/intelligence gateway implementations;
- result/asset retrieval;
- validation and resource bounds;
- logging and diagnostics;
- Studio user/execution/asset catalog persistence.

### MCP client implementation

Use the official .NET MCP client SDK for outbound Studio-to-MCP connections.

`Flamoris.Mcp.Core` is currently focused on exposing authoritative FLAMORIS application sessions to MCP clients, including local host/bridge/permission infrastructure. Studio Phase 1 is primarily an MCP **client**, so it should not depend on MCP Core merely for package symmetry.

If a future stable client-side abstraction is added to a shared FLAMORIS package, Studio may adopt it through a separate Issue.

### Logging

Use `Flamoris.Logging` when the .NET backend is initialized.

Do not vendor the logging DLL/source into this repository.

## 7. Deployment model

Phase 1 uses one Studio backend process and one built frontend.

Production shape:

```text
Authenticated User A ----+
                          |
Authenticated User B ----+--> Browser / Studio session
                                |
                                | HTTPS
                                v
FLAMORIS Studio
ASP.NET Core
   |
   +-- authentication / authorization
   +-- PostgreSQL-backed per-user execution & asset catalog
   +-- serves built React application
   +-- Studio HTTP API
   +-- MCP client connections
          |
          +-- shared flamoris-generation-mcp
          +-- shared flamoris-intelligence-mcp
```

The upstream MCP services may be shared by many Studio users. Sharing an MCP connection/service does not imply sharing user-visible executions or assets.

The original service topology is therefore:

```text
Browser
   |
   | HTTPS
   v
FLAMORIS Studio
ASP.NET Core
   |
   +-- serves built React application
   |
   +-- Studio HTTP API
   |
   +-- MCP client connections
          |
          +-- flamoris-generation-mcp
          |
          +-- flamoris-intelligence-mcp
```

The browser never connects directly to MCP services.

In development, Vite may run separately and proxy `/api` to the ASP.NET backend.

Production should be same-origin where practical.

## 8. Authority matrix

| Concern | Authority |
| --- | --- |
| Studio UI state | Studio |
| Studio presentation/orchestration | Studio |
| Authentication/session boundary | Studio / configured identity provider |
| Authorization for Studio-visible executions/assets | Studio |
| Studio user/execution/asset catalog metadata | Studio PostgreSQL |
| GPU runtime activation/shutdown/exclusivity | `flamoris-lime-manager` |
| Generation workflows | `flamoris-generation-mcp` |
| Generation jobs | `flamoris-generation-mcp` |
| Generated assets | `flamoris-generation-mcp` |
| Intelligence execution | `flamoris-intelligence-mcp` |
| Persistent Agent memory/conversation/policy | `flamoris-ai-agent` |
| Production application document state | owning application |
| Workstation-local project/media access | future `flamoris-studio-client` |

Studio must not cache authority merely because it displays the state.

## 9. Source layout

Phase 1 should start with a small repository layout rather than a large Clean Architecture hierarchy.

Suggested structure:

```text
flamoris-studio/
  src/
    Flamoris.Studio.Server/
      Api/
      Configuration/
      Identity/
      Access/
      Data/
      Catalog/
      Intelligence/
      Generation/
      Executions/
      Assets/
      Mcp/
      Program.cs

  web/
    src/
      app/
      api/
      components/
      editors/
        intelligence/
        image/
        music/
      executions/
      results/
      assets/

  tests/
    Flamoris.Studio.Server.Tests/

  docs/
    STUDIO_ARCHITECTURE.md
```

One backend project is enough for Phase 1.

Split projects only when dependency or testing boundaries become concrete.

## 10. Backend boundary

The frontend must not know MCP SDK types, tool-call payload shapes, transport details, provider URLs, or provider-local paths.

Use Studio-owned interfaces.

Initial conceptual boundary:

```csharp
public interface IGenerationGateway
{
    Task<GenerationHealth> GetHealthAsync(...);
    Task<IReadOnlyList<GenerationCapability>> GetCapabilitiesAsync(...);
    Task<IReadOnlyList<ModelSummary>> GetModelsAsync(...);
    Task<WorkflowBuildResult> BuildWorkflowAsync(...);
    Task<JobReference> SubmitAsync(...);
    Task<JobStatusView> GetStatusAsync(...);
    Task<JobResultView> GetResultAsync(...);
    Task<IReadOnlyList<AssetReference>> ListAssetsAsync(...);
    Task<AssetContent> GetAssetAsync(...);
    Task<CancelResult> CancelAsync(...);
}
```

```csharp
public interface IIntelligenceGateway
{
    // Define only after Intelligence MCP publishes its real contract.
}
```

Names may change during implementation, but the boundary must remain.

The HTTP API consumes Studio DTOs and application contracts, not MCP SDK objects.

## 11. Capability model

Studio is capability-first, not provider-first.

Primary UI concepts:

```text
Category
  -> Operation
      -> Workflow / editor
```

Examples:

```text
Image
  -> image.generate
      -> text-to-image
      -> text-to-image-lora
```

Provider/runtime metadata may be displayed in diagnostics or advanced UI, but it must not define primary navigation.

A missing capability means unavailable.

Studio must not silently choose a different provider or manufacture an unsupported operation.

## 12. Prompt Editor model

Each major category owns a dedicated editor.

```text
PromptEditor
  +-- IntelligenceEditor
  +-- ImageEditor
  +-- MusicEditor
  +-- VideoEditor      (later)
  +-- SpeechEditor     (later)
```

### Editor responsibilities

An editor owns:

- input controls;
- local draft state;
- basic client-side validation;
- user-friendly defaults;
- workflow selection within its category;
- conversion to a Studio API request.

An editor does not own:

- MCP connections;
- provider API calls;
- provider routing;
- GPU runtime switching;
- job authority;
- generated asset authority.

### Intelligence editor intent

Planned UI fields:

- Prompt
- System instruction
- Temperature
- Max tokens
- text/code attachments

Final request semantics follow Intelligence MCP once implemented.

### Image editor

Phase 1 executable editor.

Initial fields map to Generation MCP trusted workflow parameters:

- Positive prompt
- Negative prompt
- Width
- Height
- Steps
- CFG
- Seed
- Checkpoint/model
- ordered LoRA entries and strengths where supported

The UI should expose only parameters supported by the selected Generation MCP workflow.

### Music editor intent

Planned UI fields:

- Style
- Lyrics
- ABC / symbolic plan
- Duration
- Seed
- generation parameters

Final executable mapping follows the future Generation MCP YuE2 capability/workflow.

## 13. Studio shell

Initial navigation:

```text
[ Intelligence ]
[ Image        ]
[ Video        ]
[ Music        ]
[ Speech       ]
```

Phase 1 behavior:

- Image: executable when `image.generate` is available.
- Intelligence: enabled only when its MCP contract/connection is available.
- Music: enabled only when a matching generation capability is available.
- Video: visible but marked as later/unavailable.
- Speech: visible but marked as later/unavailable.

The editor area should make upstream availability visible without exposing deployment secrets.

Suggested page composition:

```text
+--------------------------------------------------+
| FLAMORIS Studio                                  |
+-------------+------------------------------------+
| Intelligence|                                    |
| Image       | Prompt / Generation Editor         |
| Video       |                                    |
| Music       +------------------------------------+
| Speech      | Execution Status / Result          |
|             | Preview / Asset Downloads          |
+-------------+------------------------------------+
```

## 14. Multi-user identity and access boundary

Every Studio API request that can reveal or mutate user-specific state must run with an authenticated principal.

Phase 1 needs a stable internal `StudioUserId` derived from the authenticated principal. It must not use display name or email address as the authorization key.

Phase 1 implements local accounts with ASP.NET Core Identity backed by PostgreSQL and secure cookie authentication. Keep generation/application code dependent only on the authenticated principal / stable StudioUserId, so a future OIDC provider can replace or complement local accounts without rewriting Studio domain logic.

### User-scoped persistent catalog

Generation MCP currently owns shared process-level job IDs and does not provide a Studio-user authorization layer.

Studio therefore needs a PostgreSQL-backed user-scoped execution/asset catalog:

```text
StudioExecutionHandle
  -> StudioUserId
  -> source
  -> upstream execution/job ID

StudioAssetHandle
  -> StudioUserId
  -> StudioExecutionHandle
  -> upstream asset ID
```

These mappings are **access-control and catalog references**, not second execution/asset authorities.

The owning MCP still decides live job state and asset content.

The Studio mappings are persisted in PostgreSQL so user ownership and generated-result references survive Studio restarts.

Do not accept an arbitrary upstream `job_id` or `asset_id` from the browser as sufficient authorization.

### Shared busy state

Generation MCP currently permits one active generation per server process.

In a multi-user Studio this is a shared capacity constraint.

If User A is generating and User B submits generation, User B may receive a generic busy state. Studio must not reveal User A's identity, prompt, parameters, upstream job ID, result name, or other private metadata.

A Studio-side waiting queue is not part of Phase 1.

## 15. Execution presentation model

Do not force every domain into a fake Generation-style job model.

Studio should distinguish presentation state from upstream authority.

Conceptual view:

```text
StudioExecutionView
  handle              # opaque Studio handle, not raw upstream job_id
  category
  operation
  source
  mode: direct | job
  external_id? 
  state
  result?
  assets[]
```

This is a transient presentation model.

For Generation jobs:

```text
source = generation
external_id = Generation MCP job_id   # server-side only
```

Current state is resolved from `jobs.status`.

If a future Intelligence MCP call is synchronous, Studio may present it as a direct execution that transitions to a terminal result without inventing a persistent Intelligence job.

## 16. Generation execution flow

The Image vertical slice follows the existing Generation MCP contract.

### 16.1 Discover

Backend calls:

```text
system.health
capabilities.get("image.generate")
models.list(...)
workflows.list
```

The backend normalizes this into Studio DTOs for the Image editor.

### 16.2 Build

On submit:

```text
Image editor
  -> Studio API request
  -> IGenerationGateway
  -> workflows.build
  -> workflow_id
```

The browser never constructs a raw provider graph.

### 16.3 Submit

```text
workflow_id
  -> jobs.submit
  -> upstream job_id
  -> persist (StudioUserId, request snapshot, upstream job_id)
  -> opaque StudioExecutionHandle
```

Submission is non-idempotent.

Studio must not automatically retry an ambiguous submit failure.

### 16.4 Status

Phase 1 uses bounded HTTP polling from browser to Studio backend.

The browser does not poll Generation MCP directly.

Suggested active polling behavior:

- poll while the execution is non-terminal;
- stop automatically at terminal state;
- pause/reduce polling when the page is not actively displaying the execution if practical;
- do not create a server-side background poller merely to avoid browser polling.

SSE/WebSocket is not required for Phase 1.

### 16.5 Result

After completion:

```text
jobs.result
  -> result metadata
assets.list
  -> asset references
```

The Result panel displays normalized metadata and asset actions.

## 17. PostgreSQL catalog model

PostgreSQL is introduced in Phase 1.

Its purpose is to persist **Studio-owned metadata**, not to duplicate MCP execution authority or store large generated binaries.

Use a dedicated Studio database or schema and a least-privilege Studio database user on decopon PostgreSQL.

### Users

Conceptual fields:

```text
users
  id                  UUID / stable StudioUserId
  external_subject?   identity-provider subject when applicable
  display_name?
  created_at
  updated_at
```

Authentication credentials are managed by ASP.NET Core Identity in Phase 1. Do not create a second password/credential system in Studio catalog tables.

### Executions

Conceptual fields:

```text
executions
  id                  UUID / opaque StudioExecutionHandle
  user_id
  source              generation | intelligence
  category            image | music | ...
  operation
  workflow?
  upstream_job_id?    server-side only
  request_snapshot    JSONB
  last_known_status   cache/presentation only
  submitted_at
  started_at?
  completed_at?
  created_at
  updated_at
```

`request_snapshot` stores the normalized Studio request needed for inspection and future rerun/history features.

It may include prompts and generation parameters, so it is private user data and must never be exposed cross-user or logged wholesale.

`last_known_status` is a cache for presentation/history. The owning MCP remains authoritative for live execution state.

### Assets

Conceptual fields:

```text
assets
  id                  UUID / opaque StudioAssetHandle
  user_id
  execution_id
  source
  upstream_asset_id?  server-side only
  storage_provider
  storage_locator
  storage_path?       optional internal diagnostic/catalog path
  original_filename?
  display_name
  media_kind
  mime_type
  size_bytes?
  width?
  height?
  checksum?
  thumbnail_locator?
  availability
  created_at
  updated_at
  metadata            JSONB
```

The binary image/audio/video itself is not stored in PostgreSQL.

`storage_locator` is the durable abstraction used by Studio. It may represent a Generation MCP asset reference today and a Studio Client/local asset reference later.

`storage_path` is optional internal metadata when a real filesystem path is known and useful. It is not an asset identity, must not be trusted as a browser-supplied path, and must never be exposed as authorization.

### Thumbnails

Studio may create small thumbnails/previews for catalog/history performance.

Store thumbnail files outside PostgreSQL in a Studio-managed bounded storage location and persist only `thumbnail_locator` plus metadata in PostgreSQL.

For images, a bounded WebP/JPEG thumbnail (for example around 256 px) is appropriate.

Do not fetch or decode a full-size remote asset on every history-grid render when a safe thumbnail already exists.

### History foundation

Phase 1 does not need a full History product UI, but persisted executions/assets intentionally form the foundation for Phase 2 history.

A later history UI should query Studio-owned catalog records by authenticated user rather than scanning Generation MCP output directories.

## 18. Asset representation

Core invariant:

> **AssetRef is not a FilePath.**

Studio-facing conceptual DTO:

```text
AssetReference
  source
  handle              # opaque Studio asset handle
  media_kind
  mime_type
  display_name
  size_bytes?
  materialized?
```

For Phase 1 Generation assets:

```text
source = generation
handle = Studio-generated opaque handle
# Generation MCP asset_id remains server-side in the user-scoped registry
```

Do not expose Generation MCP local output paths to the browser.

Do not derive local filesystem access from provider-supplied names.

## 19. Result preview and download

Generated file retrieval is a first-class Phase 1 feature.

### Result UI

For each completed asset show, as applicable:

- preview;
- media type;
- display filename;
- size when known;
- Download action.

Initial Image results support image preview.

Future renderers:

- text -> text / Markdown
- audio -> audio player
- video -> video player

### Download path

Conceptual browser request:

```text
GET /api/executions/{executionHandle}/assets/{assetHandle}/content
GET /api/executions/{executionHandle}/assets/{assetHandle}/download
```

Exact route naming may change during implementation.

Backend behavior:

1. require an authenticated Studio user;
2. resolve the opaque execution/asset handles in that user's scope;
3. reject missing or foreign handles without revealing whether another user owns them;
4. resolve the matching gateway and server-side upstream asset ID;
5. request the asset from the owning MCP;
6. validate returned media metadata and configured size limits;
7. choose/sanitize a safe download filename;
8. return content with an explicit MIME type;
9. for download, set safe `Content-Disposition: attachment`;
10. do not persist the asset permanently on the Studio server.

The backend may buffer bounded content when required by the MCP SDK, but it should not create a durable Studio asset store.

Upstream limits still apply. Studio may impose stricter configurable limits.

### Filename handling

Provider/upstream filenames are display metadata, not trusted paths.

Strip/replace unsafe path/control characters.

Never honor directory components supplied by an upstream filename.

### Content type

Do not derive trust solely from a filename extension.

Use validated upstream media metadata plus a Studio allowlist appropriate to the renderer.

Unknown media can be downloadable only if explicitly supported by the governing Issue; do not render arbitrary active content inline.

## 20. Studio HTTP API

Exact resource names may evolve, but Phase 1 should expose a small typed API.

Conceptual surface:

```text
GET  /api/session
GET  /api/system/status

GET  /api/generation/capabilities
GET  /api/generation/models
GET  /api/generation/workflows

POST /api/generation/image/jobs
GET  /api/executions/{executionHandle}
GET  /api/executions/{executionHandle}/result
POST /api/executions/{executionHandle}/cancel

GET  /api/executions/{executionHandle}/assets/{assetHandle}/content
GET  /api/executions/{executionHandle}/assets/{assetHandle}/download
```

The public Studio API is not required to mirror MCP tool names one-for-one.

Prefer task-oriented Studio DTOs.

## 21. Persistence

Phase 1 uses PostgreSQL for Studio-owned identity/catalog metadata.

The initial deployment target is the existing PostgreSQL infrastructure on decopon, using a Studio-specific database or schema and least-privilege credentials.

Persist:

- Studio user identity mapping as required by the selected authentication mechanism;
- execution ownership and opaque Studio handles;
- normalized request snapshots;
- upstream execution/job references;
- cached/presentation status and timestamps;
- asset ownership and opaque Studio handles;
- storage/provider locators;
- optional internal storage paths when known;
- media metadata;
- thumbnail locators;
- basic availability state.

Do not persist large generated image/audio/video binaries in PostgreSQL.

Do not treat persisted `last_known_status` as the live execution authority; refresh from the owning MCP when current state matters.

Do not create a second provider queue or independently mutate Generation MCP execution state from the database.

Browser draft state may remain transient.

A full History/Presets user experience belongs to a later phase, but Phase 1 intentionally stores the metadata needed to implement those features without a migration from transient in-memory handles.

## 22. Configuration

Backend configuration may include:

- PostgreSQL connection configuration / secret reference;
- thumbnail/catalog storage location;
- Generation MCP endpoint;
- Intelligence MCP endpoint when implemented;
- request timeout;
- active-job polling guidance exposed to frontend if needed;
- maximum Studio asset retrieval size;
- logging settings.

Secrets must come from deployment configuration/secret storage, not committed configuration.

Do not expose backend MCP endpoints, credentials, internal hostnames, or private tunnel identifiers through frontend configuration.

## 23. Error model

Normalize MCP/provider failures into bounded Studio errors.

Suggested categories:

- unavailable
- validation
- busy
- timeout
- cancelled
- upstream_failure
- unsupported
- not_found
- internal

User-facing messages should be useful without exposing:

- stack traces;
- credentials;
- provider-local paths;
- private hostnames/topology;
- raw internal exception objects.

Preserve diagnostic correlation/log information server-side.

## 24. Cancellation

For Generation:

```text
Studio cancel(executionHandle)
  -> authenticate user
  -> resolve user-owned executionHandle
  -> upstream job_id
  -> jobs.cancel(upstream job_id)
```

Studio must preserve the upstream cancellation semantics.

Do not report a job as cancelled merely because the browser requested cancellation.

Continue resolving state until the owning MCP reports a terminal state or the request itself returns an authoritative terminal result.

## 25. Runtime authority

Studio does not call LIME Manager merely to activate JANKU/YuE2/LLM before each request.

The owning MCP/provider integration is responsible for satisfying its runtime requirements through the appropriate runtime authority.

Studio sees capability availability.

Conceptually:

```text
Studio
  -> "image.generate"

Generation MCP / runtime integration
  -> runtime requirement

LIME Manager
  -> GPU/runtime authority
```

This prevents GPU topology from leaking into the product UI/backend architecture.

## 26. Frontend availability behavior

Each category/editor must handle:

- backend unavailable;
- MCP unavailable;
- capability unavailable;
- provider unavailable;
- busy execution;
- validation failure.

Unavailable categories should remain understandable in the UI.

Do not silently hide all future categories merely because their backend is not implemented yet.

A disabled category may show a short reason such as:

```text
Music generation is not available from the configured Generation MCP.
```

Do not expose provider connection secrets in that message.

## 27. Security

Phase 1 security requirements:

- user-specific APIs require authentication;
- all catalog queries are filtered/authorized by stable StudioUserId;
- database credentials are backend-only and least-privilege;
- storage paths/locators are server-side metadata and never accepted directly from browser input;
- authorization is checked server-side for every execution/result/cancel/preview/download operation;
- opaque Studio execution/asset handles are scoped to the authenticated Studio user;
- raw upstream job/asset IDs are never treated as authorization tokens;
- foreign/missing handles fail closed without cross-user metadata leakage;
- browser never receives MCP credentials;
- browser never receives provider-local file paths;
- backend validates all IDs and media metadata;
- result/download routes use bounded content size;
- non-idempotent submission is not automatically retried;
- provider/MCP responses are untrusted input;
- prompts/attachments/results are treated as potentially private;
- logs must not contain credentials or full sensitive payloads by default;
- normal CI must use fake/mock MCP behavior.

Phase 1 uses ASP.NET Core Identity local accounts with secure cookie authentication. The architectural requirement remains authenticated identity plus server-side authorization and user isolation; future external identity providers must preserve the same StudioUserId/access-control boundary.

Deployment must not expose Studio or unauthenticated MCP services beyond the intended trust boundary without an explicit access-control design.

## 28. Observability

Use structured logging through `Flamoris.Logging`.

Useful fields may include:

- operation/category;
- upstream service;
- external job ID when non-sensitive;
- elapsed time;
- normalized status;
- error category;
- correlation/request ID.

Do not log entire prompts, generated binary content, secrets, or arbitrary attachments by default.

## 29. Testing strategy

### Backend unit tests

Cover:

- Studio DTO validation;
- MCP-to-Studio normalization;
- error mapping;
- capability unavailable paths;
- busy generation behavior;
- cancellation mapping;
- filename sanitization;
- MIME validation;
- asset size limits;
- unknown asset/source rejection;
- cross-user execution access rejection;
- cross-user result/cancel rejection;
- cross-user asset preview/download rejection;
- PostgreSQL ownership/catalog persistence;
- request_snapshot privacy boundaries;
- storage locator/path handling;
- thumbnail locator validation;
- restart-safe execution/asset handle resolution;
- busy responses that do not leak another user's metadata.

### Gateway integration tests

Use fake/mock MCP servers or SDK transports.

Test the complete Image contract:

```text
discover
-> build
-> submit
-> poll
-> result
-> asset list
-> asset content
```

No live GPU is required in normal CI.

### Frontend tests

Cover:

- category navigation;
- unavailable states;
- Image form validation;
- submit state;
- active polling lifecycle;
- completed preview;
- failed result;
- asset Download action;
- authenticated session handling;
- UI behavior when a session is missing/expired.

### End-to-end smoke

A local fake backend/upstream fixture may verify the Browser -> Studio -> fake MCP shape.

Live LIME/ComfyUI verification is a manual/integration environment check, not a normal CI requirement.

## 30. Phase 1 acceptance criteria

### Foundation

- React/TypeScript/Vite frontend exists.
- EF Core/PostgreSQL persistence is configured for Studio-owned catalog metadata.
- migrations/create/update flow is deterministic and documented.
- ASP.NET Core .NET 10 backend exists.
- production backend can serve the built frontend.
- structured logging is enabled.
- normal build/test/CI is reproducible.

### Architecture

- Studio supports multiple authenticated users;
- one user cannot read, cancel, preview, or download another user's Studio execution/assets;
- raw upstream job/asset IDs are not sufficient to access Studio resources;
- browser has no direct MCP dependency.
- outbound MCP access is behind Studio gateway interfaces.
- Studio does not own GPU runtime state.
- Studio persists user/execution/asset catalog metadata in PostgreSQL without taking over live MCP execution authority.
- Studio asset representation contains no provider-local filesystem path.

### Image vertical slice

With a compatible Generation MCP:

- Image editor loads current usable models/workflows;
- user can build and submit image generation;
- Studio displays current job status;
- busy/failed/cancelled states are handled;
- completed result is shown;
- generated assets are listed;
- image preview works;
- each asset can be downloaded through Studio;
- provider-local paths are not exposed to the browser.

### Intelligence

When Intelligence MCP is unavailable/unimplemented:

- Studio shows an explicit unavailable state;
- Studio does not emulate a fake Intelligence backend.

When the real contract becomes available, implementation follows it through `IIntelligenceGateway`.

### Music

When no Music generation capability is available:

- Studio shows an explicit unavailable state;
- Studio does not hard-code a private YuE2 provider API.

When the Generation MCP music capability becomes available, the Music editor maps to that real contract.

## 31. Recommended implementation Issues

The Phase 1 design should be implemented as several reviewable Issues.

### Issue A — Bootstrap Studio shell, identity/database foundation, and build

Scope:

- .NET 10 backend;
- React/TypeScript/Vite frontend;
- production static serving;
- development proxy;
- `Flamoris.Logging`;
- basic health/status;
- ASP.NET Core authentication/authorization foundation;
- stable internal Studio user identity;
- authenticated session endpoint;
- EF Core + PostgreSQL foundation;
- initial users/executions/assets schema and migrations;
- Studio-specific least-privilege database configuration;
- CI.

### Issue B — Add outbound MCP gateway foundation

Scope:

- official .NET MCP client;
- configuration;
- Generation MCP connection;
- gateway interfaces;
- PostgreSQL-backed per-user opaque execution/asset catalog;
- normalized error model;
- fake MCP test infrastructure.

### Issue C — Implement Image generation vertical slice

Scope:

- capability/model/workflow discovery;
- Image editor;
- workflow build;
- submit/status/result/cancel;
- busy behavior;
- tests.

### Issue D — Implement result asset catalog, thumbnails, preview and download

Scope:

- asset reference/catalog DTO;
- persistent asset metadata;
- storage provider/locator and optional internal path metadata;
- created timestamps and basic media metadata;
- bounded thumbnail generation/storage and thumbnail locator;
- `assets.list` / `assets.get`;
- image preview;
- bounded content endpoint;
- safe download endpoint;
- filename/MIME/size validation;
- tests.

This may be merged with Issue C if the resulting change remains reviewable, but it should remain a distinct architectural concern.

### Issue E — Add Intelligence editor/integration

Blocked until Intelligence MCP has a concrete public contract.

### Issue F — Add Music editor/integration

Blocked until Generation MCP has a concrete Music capability/workflow contract.

## 32. Work handoff guidance

For initial architecture/foundation implementation:

- Recommended model: GPT-5.6 Sol
- Reasoning intensity: High

Reasoning is intentionally high for the first implementation because it establishes:

- frontend/backend boundaries;
- MCP client isolation;
- job/asset semantics;
- security-sensitive download behavior;
- future Intelligence/Music extension points;
- multi-user authentication/authorization and cross-user isolation;
- PostgreSQL ownership/catalog persistence and future history foundation.

After the foundation is stable, isolated UI editors, tests, and small fixes can use a lighter reasoning level.

Commit meaningful units frequently.

Suggested commit boundaries for the initial Work task:

1. solution/frontend foundation;
2. identity + EF Core/PostgreSQL foundation and migrations;
3. backend/application/catalog contracts;
4. MCP gateway foundation;
5. Image vertical slice;
6. asset catalog/thumbnail/preview/download;
7. tests/docs/fixes.

Do not hold the entire Phase 1 implementation as one uncommitted change.
