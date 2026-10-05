# FLAMORIS Studio

The dedicated [Raw Intelligence editor](docs/RAW_INTELLIGENCE.md) executes the
shared non-MCP intelligence execution adapter. The standalone **Assistant**
workspace and contextual Image panel keep Agent conversations separate.

Creative control center for FLAMORIS, connecting intelligence, generative AI, and production tools.

FLAMORIS Studio is the web-based creative control plane for the FLAMORIS ecosystem.

It coordinates AI capabilities through narrow backend contracts while avoiding ownership of GPU runtime state or large production files. Raw Intelligence calls the shared vendor adapter; Agent Support calls the Agent HTTP API. Generation retains its existing compatibility path while Controller remains unimplemented.

Studio is **multi-user by design**. Authenticated users must be isolated from one another: prompts, execution references, results, and asset downloads are user-scoped even when an upstream MCP service is shared.

Phase 1 uses a Python FastAPI backend with PostgreSQL-backed local accounts and secure, server-side cookie sessions. React, TypeScript, and Vite provide the browser UI. Studio-owned user/execution/asset catalog metadata is stored in PostgreSQL; generated media binaries remain outside the database.

**Current main contains the authenticated multi-user shell, Image generation, opt-in native Speech and Music slices, and the per-user Generated results catalog with metadata/details, download, and confirmed deletion.** Runtime/model qualification and additional provider contracts remain separate acceptance gates.

See [assistant settings and model integration](docs/ASSISTANT_SETTINGS.md) for the opt-in extension and migration gates.

## Current integration boundaries

[Architecture cleanup](docs/ARCHITECTURE_CLEANUP.md) records the implemented internal Intelligence/Agent paths and custom ComfyWorkFlow retirement under [FLAMORIS AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). The former composition/reference-image proposal is preserved in pinned Git history; it is not current implementation guidance.

The [multimodal result catalog and bounded retrieval](docs/MULTIMEDIA_RESULTS.md)
preserves explicit output roles and supported media metadata, renders controlled
audio/video, and provides owner-checked immutable single ranges/attachment downloads.
Additional generation providers remain gated; shared-user Agent support is implemented and requires configured grants and operational acceptance.

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
snapshot and offer **Use settings** to return to the Image editor. The recipe
picker accepts only the original builtin `text-to-image` and `text-to-image-lora`
descriptors. Custom definitions and v3 execution are retired. A restored custom
selection remains blocked until the user explicitly selects a supported builtin;
Studio does not silently retarget the draft.

Existing owned Assets and immutable PNG/JPEG/WebP inputs retain their bounded
upload, preview and deletion APIs. Uploads remain limited to 8 MiB and 24 hours.
Neither retained builtin accepts reference images: an attached reference blocks
generation until explicitly removed. Historical executions and uncertain input
references keep their ownership and deletion fences. Source cleanup deletes no
stored inputs or assets and does not implement a replacement img2img path.

**Clear inputs** resets the Image draft: both prompts, Style selection/name,
reference attachment, Workflow and additional parameters, LoRAs, seed and advanced
settings. It restores 512 × 512, 20 steps, CFG 7, Euler/normal, denoise 1 and the
first discovered model; valid numeric defaults use the existing per-user autosave.
The reference picker closes and focus returns to the positive prompt. The action
is disabled during submission, reference operations and Style writes. Saved
Styles, uploaded snapshots, generated assets and current execution tracking remain
available; clearing the draft does not cancel or resubmit a job.
Upload support implements the image portion of #30 through #58; audio upload is
outside this contract.
See [architecture cleanup](docs/ARCHITECTURE_CLEANUP.md) for retained behavior.
The old custom Image/v3 runbooks have been removed from current documentation.
Pinned historical references and retained-data rules are in
[architecture cleanup](docs/ARCHITECTURE_CLEANUP.md).

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
      +-- shared flamoris_intelligence -> configured local vendor/runtime API
      |
      +-- AI Agent HTTP API -> internal execution adapter
      |
      +-- legacy Generation compatibility gateway
             retained image / music / speech / asset operations
```

Studio does not directly manage GPU-heavy runtimes.

Runtime activation, shutdown, switching, and GPU exclusivity belong to `flamoris-gpu-node-manager`.

Retained generation recipes, jobs, inputs and generated assets currently remain behind the `flamoris-generation-mcp` compatibility boundary. Generation Controller is a future owner and remains unimplemented.

Language/reasoning/coding inference belongs to Runtime/API/vendor execution. `flamoris-intelligence-mcp` is an external facade and is not an internal Studio gateway.

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
- raw Intelligence through the shared non-MCP adapter and Agent support through Agent HTTP;
- retained Generation MCP compatibility gateway;
- Image generation submit / status / result flow;
- opt-in native no-reference Speech and Music generation, plus Music transcription;
- media-aware image preview and generated-result catalog;
- per-user download and confirmed deletion;
- cross-user execution/asset isolation.

Still tracked in Phase 1:

- Raw Intelligence and Agent HTTP cutover acceptance on the operator-selected deployment;
- Image/Speech/Music installed-provider and output-quality acceptance;
- existing bounded transfers, input authorization and account fences on the selected deployment;
- separately authorized Controller design/implementation to replace the Generation compatibility hop.

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

- [FLAMORIS Intelligence MCP](https://github.com/flamoris-jp/flamoris-intelligence-mcp) — external MCP facade and package distribution for the shared non-MCP intelligence adapter
- [FLAMORIS Generation MCP](https://github.com/flamoris-jp/flamoris-generation-mcp) — retained generation recipes, jobs, providers, inputs and assets behind the compatibility boundary
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

Phase 1では、認証済みmulti-user shell、Image生成、Raw Intelligence、Agent Assistant、opt-inのSpeech・Music生成／音楽採譜、Generated一覧・取得・削除、bounded transferとmanaged inputの認可まで実装されています。内部Intelligenceは共有adapter、AgentはHTTP APIを使います。GenerationだけはMCP互換経路が残り、Controllerは未実装です。実機導入と各providerの受け入れは別途必要です。

生成結果は単なる画面表示で終わらせず、asset authorityを経由して安全にユーザーが取得できることを最初から要件に含めます。

将来は `flamoris-studio-client` を通じてローカル環境にある大容量project / media / production toolsへ接続します。Studio側は可能な限りファイルパスではなくasset referenceを扱います。

勝手に使ってください。  
改造しても、組み込んでも、面白いものや変なものを作ってもOKです。
