# FLAMORIS Studio

The dedicated [Raw Intelligence editor](docs/RAW_INTELLIGENCE.md) executes the
reviewed synchronous Intelligence MCP contract. The standalone **Assistant**
workspace and contextual Image panel keep Agent conversations separate.

Creative control center for FLAMORIS, connecting intelligence, generative AI, and production tools.

FLAMORIS Studio is the web-based creative control plane for the FLAMORIS ecosystem.

It coordinates AI-facing workflows through stable MCP boundaries while deliberately avoiding ownership of GPU runtime state or large production files.

Studio is **multi-user by design**. Authenticated users must be isolated from one another: prompts, execution references, results, and asset downloads are user-scoped even when an upstream MCP service is shared.

Phase 1 uses a Python FastAPI backend with PostgreSQL-backed local accounts and secure, server-side cookie sessions. React, TypeScript, and Vite provide the browser UI. Studio-owned user/execution/asset catalog metadata is stored in PostgreSQL; generated media binaries remain outside the database.

**Current main contains the authenticated multi-user shell, Image generation, opt-in native Speech and Music slices, and the per-user Generated results catalog with metadata/details, download, and confirmed deletion.** Runtime/model qualification and additional provider contracts remain separate acceptance gates.

See [assistant settings and model integration](docs/ASSISTANT_SETTINGS.md) for the opt-in extension and migration gates.

## Proposed integration design

[Multimodal Studio and contextual Agent](docs/MULTIMODAL_STUDIO.md) records the proposed media/Workflow/Agent integration coordinated by [FLAMORIS AI #15](https://github.com/flamoris-jp/flamoris-ai/issues/15). It is a design proposal, not a claim that new providers, composed execution or shared-user Agent assistance are implemented. Existing public contracts and readiness gates remain authoritative.

The [multimodal result catalog and bounded retrieval](docs/MULTIMEDIA_RESULTS.md)
preserves explicit output roles and supported media metadata, renders controlled
audio/video, and provides owner-checked immutable single ranges/attachment downloads.
Additional generation providers and shared-user Agent integration remain gated.

## Direction

Studio grows through complete vertical slices rather than broad placeholder scaffolding.

Phase 1 covers:

- Intelligence
- Image
- Music
- shared job submission / status / result presentation
- generated-result preview and user download
- multi-user authentication/session boundary and per-user execution/asset isolation

Native no-reference Japanese Speech and Music have dedicated opt-in editors;
Video awaits its own qualified provider contracts.

The UI uses dedicated editors for each generation type rather than one universal prompt form.

The Image editor includes user-owned Styles stored in PostgreSQL. A Style saves
positive and negative prompts; applying it leaves an editable draft. The editor
also exposes size presets, ordered optional LoRAs, sampler, scheduler, denoise,
and a random seed action. Generated result details show Studio's original request
snapshot and offer **Use settings** to return to the Image editor. The Workflow
picker uses graph-free Image-v1 descriptors and only selects ready Definitions
with exact version/digest evidence. Reference images come from the signed-in
user's existing generated Assets when both Workflow and managed-input
infrastructure are ready. The editor also accepts a local PNG/JPEG/WebP image
through file selection or drag and drop, up to 8 MiB, with an owner-only preview
and a 24-hour lifetime. Uploads can be prepared before Workflow/GPU readiness;
generation still requires an exact ready img2img Workflow and managed-input
infrastructure. Attach, detach and replace preserve the prompt draft; expired
references require reselection. A reference attached to txt2img blocks generation
until a compatible Workflow is selected or the reference is explicitly removed.
Upload support implements the image portion of #30 through #58; audio upload is
outside this contract.
See [Workflow Image operation](docs/WORKFLOW_IMAGE_OPERATION.md).
The opt-in [Image v3 route](docs/IMAGE_V3_OPERATION.md) also selects exact ready
pinned Image wrappers through this editor, preserving per-user execution and Asset
access. Multiple components and other media remain gated.

The [native Speech editor](docs/SPEECH_OPERATION.md) submits one Japanese speech
recording with text, optional voice description, duration, steps and seed. It
requires an independent Studio opt-in and the exact configured Irodori contract.
Reference audio, voice cloning and model controls are unavailable. A durable
owner-scoped request identifier prevents replay after an uncertain submission;
the browser retains only that identifier until the outcome is terminal. WAV
playback and download use the existing owned Asset routes. Configured resources
do not certify an actual model/GPU run or output quality.

The opt-in [Music editor](docs/MUSIC_OPERATION.md) creates one WAV/ABC music
recording or transcribes an existing owned generated WAV to MIDI and annotations.
Each operation requires its exact configured native Generation descriptor;
configuration does not attest model execution. Shared durable request UUIDs
preserve uncertain outcomes across restart without replay, and safe owned Asset
routes provide audio playback and ABC/MIDI/JSON downloads. Local file upload,
user-supplied ABC plans and GPU controls remain outside these profiles.

Examples include:

- Intelligence: prompt, system instruction, temperature, max tokens, attachments
- Image: positive/negative prompt, dimensions, steps, CFG, seed, model and LoRA
- Music: style, lyrics, symbolic plan / ABC, duration, seed and generation parameters

## Architecture boundary

**FLAMORIS Studio is a creative control plane, not a runtime authority or project-file server.**

```text
FLAMORIS Studio
      |
      +-- flamoris-intelligence-mcp
      |      language / reasoning / coding
      |
      +-- flamoris-generation-mcp
             image / video / music / speech
```

Studio does not directly manage GPU-heavy runtimes.

Runtime activation, shutdown, switching, and GPU exclusivity belong to `flamoris-gpu-node-manager`.

Generation workflows, generation jobs, and generated assets belong to `flamoris-generation-mcp`.

Language/reasoning/coding execution belongs to `flamoris-intelligence-mcp`.

Studio remains authoritative only for Studio-specific UI state, presentation, orchestration, and Studio-side access control.

Upstream MCP job/asset IDs are not authorization tokens. Studio must scope access to the authenticated user and must not expose another user's prompt, execution metadata, result, or asset merely because the upstream identifier is known.

## Jobs and results

Studio presents execution through a common user-facing flow:

```text
submit
  -> job reference
  -> status
  -> result
```

The underlying MCP remains the authority for execution state.

Result presentation is media-aware:

- text -> text / Markdown
- image -> image preview
- audio -> audio player
- video -> video player

The Generated results page lists each signed-in user's stored output references, offers metadata/details and download, and supports confirmed individual or selected-item deletion. Deletion calls Generation MCP `assets.delete` and then hides the Studio catalog entry; failure leaves it visible for retry. The operation removes the Generation MCP-managed copy, not necessarily the original provider output. Previously materialized outputs remain accessible after a Generation MCP restart once its durable asset support is deployed. Active job status and outputs that were never materialized still belong to the original process session.

Generated media must also be retrievable by the user from the Studio UI. Studio should expose a safe download path backed by the owning asset authority rather than leaking provider-local filesystem paths.

## Assets

The optional [external generation import](docs/EXTERNAL_GENERATION_IMPORT.md)
uses exact operator-provisioned external identity grants and trusted Generation
job/asset provenance to add completed external outputs to the owner's normal
Assets gallery. The explicit import is metadata-only, durable and idempotent;
existing preview/download/deletion authorization remains in effect.

Result synchronization stores metadata before any binary retrieval.
New generated assets receive a persisted Studio filename containing their media
kind, UTC submission time, output ordinal and a Studio asset suffix. Display and
download names match across restarts; safe provider names remain metadata, and
existing catalog names are preserved. Names contain no prompts or upstream IDs.
Gallery thumbnails are generated lazily through their own endpoint; a slow/failed preview
does not remove the row. Completed executions can retry metadata synchronization
after a Studio restart. If an upstream listing is temporarily unavailable, already
cataloged results remain visible with `catalogSync: unavailable` on the result
response. Tombstones prevent deleted entries from being reimported.

Inline preview remains limited to at most 64 MiB (or the lower configured Studio
limit). Images with unknown size or above 512 KiB use Generation MCP
`assets.prepare` and `assets.read` for preview, thumbnail and download, avoiding
a single base64 MCP/SSE event above a typical 1 MiB transport limit. Smaller
images retain native `assets.get`; downloads above the inline limit also use
bounded transfer. The two tools must be registered on the MCP Hub. Studio checks
ownership, reads at most 256 KiB per chunk, verifies chunk/final digests, and
caps concurrent transfers at two per process. Thumbnail/inline-preview requests
wait up to 30 seconds for a shared slot, with at most 24 pending previews per
process; queue overflow/timeouts return 429 with Retry-After. Cached thumbnails
bypass the transfer queue. Preparation is serialized across previews and downloads
to respect Generation MCP's one-prepare limit; this stage waits up to 30 seconds.
Downloads retain immediate transfer-slot busy rejection. The UI
retries failed image loads twice with delays, then offers Reload preview. The aggregate transfer cap defaults
to 1 GiB and may be lowered with `STUDIO_MAX_TRANSFER_BYTES`. Deploy the Generation
bounded-transfer tools and register them on the Hub before large downloads can
work. Small image retrieval retains a bounded 300-second MCP call timeout and
logs tool name, failure class and elapsed time without exposing private paths.
Unmaterialized provider outputs still cannot be downloaded after their live
execution mapping is lost.

Studio treats generated outputs and future client-side media as asset references, not raw filesystem paths.

This distinction is intentional:

```text
AssetRef != FilePath
```

In early phases, result assets may come from `flamoris-generation-mcp`.

Later, `flamoris-studio-client` will make large desktop-local projects and media available through explicit asset references without requiring permanent storage on the Studio server.

## Deployment

For a generic Docker/Compose deployment, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The public setup does not assume a particular FLAMORIS host. PostgreSQL, thumbnail storage, bind address/port, Generation MCP, reverse proxy, and secrets are supplied by the deployment environment.

[Real-machine acceptance](docs/REAL_MACHINE_ACCEPTANCE.md) records the reviewed source baselines, two-user Image/result retrieval checks, independent Agent-service checks and the remaining provider/runtime/Studio integration gates. CI success and merged source do not certify a deployment or live provider result.

The optional [scoped Image assistant](docs/SCOPED_ASSISTANT.md) connects authenticated accounts to operator-granted Agent principals for text advice and explicit Image draft attachment. Apply migration `20261003_05` and configure the private endpoint/token/account mapping before enabling it. Agent owns conversations/inference; the panel does not submit generation or apply edits.

## Architecture

Phase 1 architecture and implementation boundaries are defined in [docs/STUDIO_ARCHITECTURE.md](docs/STUDIO_ARCHITECTURE.md).

## Current and next phases

### Phase 1

Implemented foundations in current main include:

- authenticated multi-user Studio shell;
- PostgreSQL-backed Studio ownership/catalog metadata;
- MCP gateway boundary;
- Image generation submit / status / result flow;
- opt-in native no-reference Speech submit / status / WAV result flow;
- media-aware image preview and generated-result catalog;
- per-user download and confirmed deletion;
- cross-user execution/asset isolation.

Still tracked in Phase 1:

- Intelligence editor integration through the public Intelligence MCP contract;
- Music editor integration after the Generation music contract is ready;
- bounded large-asset transfer adoption and managed-input authorization;
- account/session and login-boundary hardening.

### Phase 2

- additional Speech profiles and reference audio, after qualified contracts
- Video
- prompt presets
- history

### Phase 3

- `flamoris-studio-client`
- local project discovery
- local asset bridge

### Later phases

- AudioAnalyzer
- Kinetic Typography
- Lyrics / Timeline
- Cutwork integration
- Kachinco integration
- FLAMORIS 2D integration

## Related repositories

- [FLAMORIS Intelligence MCP](https://github.com/flamoris-jp/flamoris-intelligence-mcp) — provider-neutral intelligence boundary
- [FLAMORIS Generation MCP](https://github.com/flamoris-jp/flamoris-generation-mcp) — generation workflows, jobs, providers and assets
- [FLAMORIS Studio Client](https://github.com/flamoris-jp/flamoris-studio-client) — local bridge for desktop files, media and production tools
- [FLAMORIS Commons](https://github.com/flamoris-jp/flamoris-commons) — shared foundations and repository policy

## Philosophy

Use it however you like.

Commercial use is welcome and does not require permission.

FLAMORIS software is provided as-is and does not include guaranteed individual support. If you run into trouble, let your AI assistant read the repository, documentation, issues, tests, logs, and source code and help you solve it.

If FLAMORIS helps you or you find it interesting, your support helps fund development and keeps the project growing. 🌱  
<sub>Mostly GPU bills.</sub>

## License

Code and documentation in this repository are licensed under the [Apache License 2.0](LICENSE), unless otherwise noted.

AI models, model weights, datasets, generated media, prompts supplied by third parties, and other non-code assets may use separate licenses and terms.

---

## 日本語

FLAMORIS Studioは、FLAMORISのAI・生成系・制作ツールをつなぐWebベースのCreative Control Centerです。

Studioは最初からマルチユーザー前提です。共有されたMCP/runtimeを利用する場合でも、prompt・execution・result・生成assetへのアクセスはユーザーごとに分離します。

Studio自身はGPU runtimeのauthorityにも、大容量プロジェクトファイルの保管場所にもなりません。

Phase 1では、認証済みmulti-user shell、Image生成、job表示、生成結果preview、Generated一覧・取得・削除までcurrent mainで実装されています。Intelligence / Music連携やlarge asset / managed inputの境界は引き続きPhase 1で進めます。

生成結果は単なる画面表示で終わらせず、asset authorityを経由して安全にユーザーが取得できることを最初から要件に含めます。

将来は `flamoris-studio-client` を通じてローカル環境にある大容量project / media / production toolsへ接続します。Studio側は可能な限りファイルパスではなくasset referenceを扱います。

勝手に使ってください。  
改造しても、組み込んでも、面白いものや変なものを作ってもOKです。
