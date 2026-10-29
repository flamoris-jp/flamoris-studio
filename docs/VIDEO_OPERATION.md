# Video generation through Controller

## MiniMax H3 comparison profile (1.3.0)

With Controller / Generation 1.3.0, the Video editor also accepts the explicit
`minimax-h3-video-v1` descriptor. H3 dimensions use multiples of 32, frames follow
`17k+5` (5, 22, 39…158 within the bounded contract), and FPS is fixed to 24.
124 frames encode 5.1667 seconds. The form explains that output is silent.
Wan retains its existing geometry, `4k+1` frames and variable frame rate.

The profile is selected by the immutable registered recipe. Studio never guesses
the model from a display name or silently converts an invalid frame count.
Existing per-user managed references, request identities, response-time checks,
unknown/no-replay fences and MP4 output delivery remain shared. No database
migration or new Studio configuration is required. Model installation and actual
GPU/browser/visual acceptance are separate from these source tests. See
[Controller H3](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/MINIMAX_H3.md).

Owner: [Studio #97](https://github.com/flamoris-jp/flamoris-studio/issues/97),
following [Controller #31](https://github.com/flamoris-jp/flamoris-generation-controller/issues/31)
and [Generation #25](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/25).

Studio 1.2.0 adds a dedicated Video editor. It uses the existing authenticated
Controller HTTP API, never MCP/Hub or a separate AnimeGen process. Controller
owns model/node validation, recipes, GPU submissions, job state, managed inputs
and video bytes. GPU lifecycle remains with the runtime manager.

## Configuration and supported recipes

Keep the existing `STUDIO_GENERATION_ENDPOINT` and private
`STUDIO_GENERATION_TOKEN` configuration. No new credentials, database migration,
model download or Studio-specific GPU setting is required. Use Controller /
Generation 1.2.1 or later with the reviewed native SaveVideo V3 support.

Video discovery requires a healthy Controller, available ComfyUI
`video.generate`, ready managed-input storage, and a supported immutable
metadata-v4 definition: `registered-comfy`, definition version 1, matching
ID/digest and `wan-video-v1` profile. Dimensions, frames and FPS need valid
declared defaults. Required default models must be discoverable.
Unsupported definitions stay unavailable. Image discovery excludes video recipes.
Studio does not register/edit graphs or activate runtimes.

The normalized browser descriptor contains scalar choices and semantic image
roles, not the graph, provider paths or service credential. Only `i2v` and
`flf2v` are supported. I2V requires exactly `start_image`; FLF2V additionally
requires its distinct `end_image` graph binding. Studio does not invent a
FLF2V recipe or discard an end image to fall back to I2V. A registered contract
does not attest visual endpoint fidelity or general model compatibility.

## User flow

1. Open **Video**, choose an available video recipe, and attach the start image.
   Upload one PNG/JPEG/WebP up to 8 MiB or select an owned image Asset. References
   use the existing private, expiring managed-input snapshots. When the selected
   recipe is FLF2V, attach its end image separately.
2. Describe the motion, review dimensions, frame count, FPS and optional seed.
   Advanced controls come from the selected recipe's declared scalar/model
   choices. A blank seed is assigned by Studio and persisted in the request
   snapshot before submission; explicit zero stays zero.
3. Generate once. The Video draft and current execution remain mounted across
   navigation. Status/result/cancel use the shared owned execution routes.
4. Preview and download the single MP4. Completed output is also available under
   **Assets → Video**, with its owned request details and existing range support.
   A saved execution link reopens Video. This slice adds no video presets or
   reusable history system.

Width/height are integers 64–1024, multiples of 16, and total pixels cannot exceed
832×832. Frames must be 5–161 with `4n+1`; FPS is 1–32 and encoded duration cannot
exceed 10.1 seconds. The UI shows `frames / FPS`, so **81 frames at 16 FPS is
5.0625 seconds**, not exactly five seconds. Descriptor limits may be narrower.
Controller rechecks the actual graph budgets and installed contracts.

## Request, account and media boundaries

`POST /api/generation/video/jobs` accepts a fresh browser request UUID, immutable
recipe version/digest, declared scalar values and Studio-owned image handles.
It accepts no upstream input/job/asset ID, graph, provider filename or URL.
Every reference is ownership-checked before discovery. References are locked
in a stable order and `ExecutionInput` deletion fences, the owned execution and
the generic `GenerationRequestRecord` are committed before input/build/submit IO.
The original Studio handles and effective concrete seed stay in the snapshot.

Studio rechecks session, configuration and references after build, validates
schema 11 / `registered-video`, exact definition and input/parameter echoes,
then calls the shared Controller's `jobs.submit` once. Ambiguous submission or
invalid acknowledgement stays fenced as `submission_unknown`; another POST with
the same UUID retrieves the existing outcome and never resubmits. Browser
session storage keeps only the per-account pending UUID, not prompts/images.
`GET /api/generation/video/requests/{request_id}` recovers that owned execution.
Changing a payload under an existing UUID is rejected.

The shared status/result/cancel paths recheck authentication after remote IO.
Video results require exactly one `video/mp4` / `video_clip` on the `video` port.
The existing owned Asset routes bound transfers, verify digests and support
single ranges for the MP4 player. Controller validates H.264, dimensions, frame
count/rate/timestamps before publishing movie bytes. Studio stores references
and metadata, not a competing provider state machine or production file store.
Cancellation remains scoped; an unsupported running cancellation is not replaced
with a global GPU interrupt.

## Validation and acceptance

Normal tests use synthetic Wan conditioning/decode fixtures, native
SaveVideo/CreateVideo metadata and a tiny locally encoded MP4, never private
media, weights or a live GPU. The native metadata fixture follows ComfyUI
revision `c194dd00cd42aa18d9dbf27d977bf6b85d9ea565`, already reviewed in Controller
1.2.1; it contains API metadata only, not third-party implementation code.
The matched Controller HTTP test covers both image-role modes, unchanged
managed staging, one shared admission authority, native dotted output fields
and verified movie retrieval. PostgreSQL tests cover account/CSRF isolation,
input fences, UUID/restart uncertainty, changed access/configuration, manifest
validation and owned preview/download ranges. Browser component tests cover
attachment, budgets, unavailable recipes, recovery and late account responses.

An external Controller I2V trial produced a verified 768×432, 81-frame, 16-FPS
MP4 with some foot ghosting. It proves the upstream path, not this Studio UI's
deployment/browser acceptance, production quality, or FLF2V fidelity. Record
those outcomes separately after deploying the reviewed Studio release; keep
operator topology and user media out of public source and Issues.
