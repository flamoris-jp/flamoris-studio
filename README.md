# FLAMORIS Studio

Creative control center for FLAMORIS, connecting intelligence, generative AI, and production tools.

FLAMORIS Studio is the web-based creative control plane for the FLAMORIS ecosystem.

It coordinates AI-facing workflows through stable MCP boundaries while deliberately avoiding ownership of GPU runtime state or large production files.

Studio is **multi-user by design**. Authenticated users must be isolated from one another: prompts, execution references, results, and asset downloads are user-scoped even when an upstream MCP service is shared.

Phase 1 uses a Python FastAPI backend with PostgreSQL-backed local accounts and secure, server-side cookie sessions. React, TypeScript, and Vite provide the browser UI. Studio-owned user/execution/asset catalog metadata is stored in PostgreSQL; generated media binaries remain outside the database.

## Direction

Studio starts small.

Phase 1 focuses on an AI Prompt Console for:

- Intelligence
- Image
- Music
- shared job submission / status / result presentation
- generated-result preview and user download
- multi-user authentication/session boundary and per-user execution/asset isolation

Speech and Video follow in later phases.

The UI uses dedicated editors for each generation type rather than one universal prompt form.

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

Runtime activation, shutdown, switching, and GPU exclusivity belong to `flamoris-lime-manager`.

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

The Generated results page lists each signed-in user's stored output references, offers metadata/details and download, and supports confirmed individual or selected-item deletion. Deletion calls Generation MCP `assets.delete` and then hides the Studio catalog entry; failure leaves it visible for retry. The operation removes the Generation MCP-managed copy, not necessarily the original provider output. Because the current upstream job authority is process-local, deletion of older outputs can fail after a Generation MCP restart until the upstream supports persistent asset identity.

Generated media must also be retrievable by the user from the Studio UI. Studio should expose a safe download path backed by the owning asset authority rather than leaking provider-local filesystem paths.

## Assets

Studio treats generated outputs and future client-side media as asset references, not raw filesystem paths.

This distinction is intentional:

```text
AssetRef != FilePath
```

In early phases, result assets may come from `flamoris-generation-mcp`.

Later, `flamoris-studio-client` will make large desktop-local projects and media available through explicit asset references without requiring permanent storage on the Studio server.

## Deployment

For the decopon Docker/Compose and nginx setup, see [docs/DECOPON_DEPLOYMENT.md](docs/DECOPON_DEPLOYMENT.md). Deployment configuration and database credentials stay outside the repository.

## Architecture

Phase 1 architecture and implementation boundaries are defined in [docs/STUDIO_ARCHITECTURE.md](docs/STUDIO_ARCHITECTURE.md).

## Planned phases

### Phase 1

- new Studio shell
- Intelligence / Image / Music prompt editors
- MCP gateway boundary
- job submit / status / result flow
- media-aware result preview
- generated-asset download
- multi-user isolation for executions and assets

### Phase 2

- Speech
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

Phase 1では、Intelligence / Image / Musicの専用Prompt Editorと、共通のjob表示、生成結果preview、生成ファイルの取得を成立させます。

生成結果は単なる画面表示で終わらせず、asset authorityを経由して安全にユーザーが取得できることを最初から要件に含めます。

将来は `flamoris-studio-client` を通じてMangoなどのローカル環境にある大容量project / media / production toolsへ接続します。Studio側は可能な限りファイルパスではなくasset referenceを扱います。

勝手に使ってください。  
改造しても、組み込んでも、面白いものや変なものを作ってもOKです。
