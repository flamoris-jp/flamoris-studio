# FLAMORIS Studio Architecture

Status: Phase 1 design  
Repository: `flamoris-jp/flamoris-studio`

## 1. Purpose

FLAMORIS Studio is the web-based creative control plane for FLAMORIS.

The first release focuses on an AI Prompt Console that lets a user choose a creative domain, enter domain-specific generation/intelligence parameters, submit work through MCP, observe execution, inspect results, and retrieve generated assets.

The central architectural rule is:

> **FLAMORIS Studio is a creative control plane, not a runtime authority or project-file server.**

Studio coordinates existing authorities. It does not replace them.

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
- persistent execution history;
- a general workflow designer;
- a universal schema-driven form renderer;
- Studio-owned GPU/runtime switching;
- Studio-owned provider routing;
- a second Generation job/asset database;
- a second Intelligence task authority.

These may be introduced only by later design/Issues.

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

The backend owns:

- Studio HTTP API;
- MCP client connections;
- Studio-facing DTO normalization;
- capability/availability aggregation;
- generation/intelligence gateway implementations;
- result/asset retrieval;
- validation and resource bounds;
- logging and diagnostics.

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

## 14. Execution presentation model

Do not force every domain into a fake Generation-style job model.

Studio should distinguish presentation state from upstream authority.

Conceptual view:

```text
StudioExecutionView
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
external_id = Generation MCP job_id
```

Current state is resolved from `jobs.status`.

If a future Intelligence MCP call is synchronous, Studio may present it as a direct execution that transitions to a terminal result without inventing a persistent Intelligence job.

## 15. Generation execution flow

The Image vertical slice follows the existing Generation MCP contract.

### 15.1 Discover

Backend calls:

```text
system.health
capabilities.get("image.generate")
models.list(...)
workflows.list
```

The backend normalizes this into Studio DTOs for the Image editor.

### 15.2 Build

On submit:

```text
Image editor
  -> Studio API request
  -> IGenerationGateway
  -> workflows.build
  -> workflow_id
```

The browser never constructs a raw provider graph.

### 15.3 Submit

```text
workflow_id
  -> jobs.submit
  -> job_id
```

Submission is non-idempotent.

Studio must not automatically retry an ambiguous submit failure.

### 15.4 Status

Phase 1 uses bounded HTTP polling from browser to Studio backend.

The browser does not poll Generation MCP directly.

Suggested active polling behavior:

- poll while the execution is non-terminal;
- stop automatically at terminal state;
- pause/reduce polling when the page is not actively displaying the execution if practical;
- do not create a server-side background poller merely to avoid browser polling.

SSE/WebSocket is not required for Phase 1.

### 15.5 Result

After completion:

```text
jobs.result
  -> result metadata
assets.list
  -> asset references
```

The Result panel displays normalized metadata and asset actions.

## 16. Asset representation

Core invariant:

> **AssetRef is not a FilePath.**

Studio-facing conceptual DTO:

```text
AssetReference
  source
  id
  media_kind
  mime_type
  display_name
  size_bytes?
  materialized?
```

For Phase 1 Generation assets:

```text
source = generation
id = Generation MCP asset_id
```

Do not expose Generation MCP local output paths to the browser.

Do not derive local filesystem access from provider-supplied names.

## 17. Result preview and download

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
GET /api/assets/{source}/{assetId}/content
GET /api/assets/{source}/{assetId}/download
```

Exact route naming may change during implementation.

Backend behavior:

1. validate `source`;
2. resolve the matching gateway;
3. request the asset from the owning MCP;
4. validate returned media metadata and configured size limits;
5. choose/sanitize a safe download filename;
6. return content with an explicit MIME type;
7. for download, set safe `Content-Disposition: attachment`;
8. do not persist the asset permanently on the Studio server.

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

## 18. Studio HTTP API

Exact resource names may evolve, but Phase 1 should expose a small typed API.

Conceptual surface:

```text
GET  /api/system/status

GET  /api/generation/capabilities
GET  /api/generation/models
GET  /api/generation/workflows

POST /api/generation/image/jobs
GET  /api/generation/jobs/{jobId}
GET  /api/generation/jobs/{jobId}/result
POST /api/generation/jobs/{jobId}/cancel

GET  /api/assets/{source}/{assetId}/content
GET  /api/assets/{source}/{assetId}/download
```

The public Studio API is not required to mirror MCP tool names one-for-one.

Prefer task-oriented Studio DTOs.

## 19. Persistence

Phase 1 uses no application database.

Do not add SQLite/PostgreSQL solely to preserve MCP-owned job state.

Phase 1 persistence is limited to:

- normal application configuration;
- no durable prompt history;
- no durable Studio job database;
- no durable Studio asset cache.

Browser draft state may be transient.

Persistent history/presets belong to a later phase with an explicit ownership/retention design.

## 20. Configuration

Backend configuration may include:

- Generation MCP endpoint;
- Intelligence MCP endpoint when implemented;
- request timeout;
- active-job polling guidance exposed to frontend if needed;
- maximum Studio asset retrieval size;
- logging settings.

Secrets must come from deployment configuration/secret storage, not committed configuration.

Do not expose backend MCP endpoints, credentials, internal hostnames, or private tunnel identifiers through frontend configuration.

## 21. Error model

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

## 22. Cancellation

For Generation:

```text
Studio cancel
  -> jobs.cancel(job_id)
```

Studio must preserve the upstream cancellation semantics.

Do not report a job as cancelled merely because the browser requested cancellation.

Continue resolving state until the owning MCP reports a terminal state or the request itself returns an authoritative terminal result.

## 23. Runtime authority

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

## 24. Frontend availability behavior

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

## 25. Security

Phase 1 security requirements:

- browser never receives MCP credentials;
- browser never receives provider-local file paths;
- backend validates all IDs and media metadata;
- result/download routes use bounded content size;
- non-idempotent submission is not automatically retried;
- provider/MCP responses are untrusted input;
- prompts/attachments/results are treated as potentially private;
- logs must not contain credentials or full sensitive payloads by default;
- normal CI must use fake/mock MCP behavior.

Application-level user authentication is not defined by this Phase 1 document.

Deployment must not expose Studio or unauthenticated MCP services beyond the intended trust boundary without an explicit access-control design.

## 26. Observability

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

## 27. Testing strategy

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
- unknown asset/source rejection.

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
- asset Download action.

### End-to-end smoke

A local fake backend/upstream fixture may verify the Browser -> Studio -> fake MCP shape.

Live LIME/ComfyUI verification is a manual/integration environment check, not a normal CI requirement.

## 28. Phase 1 acceptance criteria

### Foundation

- React/TypeScript/Vite frontend exists.
- ASP.NET Core .NET 10 backend exists.
- production backend can serve the built frontend.
- structured logging is enabled.
- normal build/test/CI is reproducible.

### Architecture

- browser has no direct MCP dependency.
- outbound MCP access is behind Studio gateway interfaces.
- Studio does not own GPU runtime state.
- Studio has no database in Phase 1.
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

## 29. Recommended implementation Issues

The Phase 1 design should be implemented as several reviewable Issues.

### Issue A — Bootstrap Studio shell and build

Scope:

- .NET 10 backend;
- React/TypeScript/Vite frontend;
- production static serving;
- development proxy;
- `Flamoris.Logging`;
- basic health/status;
- CI.

### Issue B — Add outbound MCP gateway foundation

Scope:

- official .NET MCP client;
- configuration;
- Generation MCP connection;
- gateway interfaces;
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

### Issue D — Implement result asset preview and download

Scope:

- asset reference DTO;
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

## 30. Work handoff guidance

For initial architecture/foundation implementation:

- Recommended model: GPT-5.6 Sol
- Reasoning intensity: High

Reasoning is intentionally high for the first implementation because it establishes:

- frontend/backend boundaries;
- MCP client isolation;
- job/asset semantics;
- security-sensitive download behavior;
- future Intelligence/Music extension points.

After the foundation is stable, isolated UI editors, tests, and small fixes can use a lighter reasoning level.

Commit meaningful units frequently.

Suggested commit boundaries for the initial Work task:

1. solution/frontend foundation;
2. backend/application contracts;
3. MCP gateway foundation;
4. Image vertical slice;
5. asset preview/download;
6. tests/docs/fixes.

Do not hold the entire Phase 1 implementation as one uncommitted change.
