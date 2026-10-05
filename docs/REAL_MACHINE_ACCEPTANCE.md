# Current real-machine acceptance handoff

Source authority: [architecture cleanup](ARCHITECTURE_CLEANUP.md),
[AI progress](https://github.com/flamoris-jp/flamoris-ai/blob/main/PROGRESS.md) and
[AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18). This handoff describes
retained behavior after custom ComfyWorkFlow/v3 retirement. Source tests and merged
PRs do not establish deployment, installed models, physical GPU capacity or output
quality. This document performs no rollout or inference.

## Record the actual deployment

Record UTC time, each deployed commit, running image digest, clean/dirty source,
DB migration heads, enabled feature flags and the reviewed external tool catalog.
An image tag or repository checkout alone does not identify a running build.
Keep credentials, endpoints, private paths, prompts, cookies and raw logs in the
operator's private records; publish only sanitized counts/hashes and failure codes.

Use [DEPLOYMENT.md](DEPLOYMENT.md) and two dedicated Studio test accounts with
separate sessions. Apply the checked-out migration head through the migration role;
current source includes `20261004_11` for assistant model settings. Agent has its
own schema, delegation, membership and model grants. Applying schema is not a
principal grant or an instruction to populate production data.

| Capability | Current path and required receipt |
| --- | --- |
| Image | Studio -> authenticated Controller HTTP -> shared Controller -> ComfyUI; two builtin txt2img templates, model discovery, optional ordered LoRAs and bounded image retrieval |
| Raw Intelligence | Studio -> shared non-MCP llama.cpp adapter; exact configured model alias and bounded text/reasoning/code responses |
| Agent Assistant | Studio -> Agent HTTP `/api/v1` -> shared Intelligence adapter; authorized principal/session, optional local/OpenAI model grants and complete-context remote consent |
| Speech | Opt-in native Irodori recipe/CLI adapter; configured immutable resources, Japanese no-reference output up to 30 seconds, owned WAV retrieval |
| Music | Opt-in native YuE2 generation and SheetSage2 transcription contracts; owned WAV/ABC or MIDI/annotation outputs and existing request fences |
| External ChatGPT | Hub -> current external Generation/Intelligence/Agent catalogs; configured authentication and a fresh client tool listing |

Controller source is implemented and Generation now uses its direct HTTP API;
this document still requires a separately authorized matched live cutover. Internal
Intelligence, Agent and generation calls use no MCP. An existing Irodori HTTP
server does not substitute for the retained CLI adapter contract. GPU Node Manager
alone controls host lifecycle; Studio/Agent/Generation do not automatically switch
runtimes to make a capability available.

## Paired upgrade prerequisites

Only execute these steps under separate operational authorization:

1. Pause admission from Studio, direct clients and automation. Preserve images,
   configuration, Studio/Agent DBs, Generation journals, saved definitions/recipes,
   input snapshots, provider-copy ledgers, output volumes and historical evidence.
2. Drain or reconcile active/unknown old custom work using the previous matched
   service version and its original authority. New source reports opaque retired
   debt as unknown and keeps its busy reservation; never clear a journal or start
   an independent store merely to unblock startup.
3. Deploy the reviewed Generation/Hub pair and refresh only the tool definitions
   in deployment-local YAML. Follow the Hub's
   [catalog/rollback handoff](https://github.com/flamoris-jp/flamoris-mcp-hub/blob/main/docs/GENERATION_ROLLOUT.md).
   The current Generation export contains 23 retained tools; registration,
   verification and all v3 tools are absent. Preserve private routing and auth.
4. Configure the direct Intelligence and Agent HTTP paths, DB grants and opt-in
   settings separately. Create a fresh authorized conversation for a changed
   model/target; never silently retarget existing history or enable remote fallback.
5. Obtain fresh deployed catalog and route receipts before admitting provider work.
   Keep the previous compatible image/configuration pair available for rollback.

The public Compose template fixes `container_name: flamoris-studio`. Concurrent
review/rollback instances require an explicit container-name override and separate
ports, storage and configuration; a distinct Compose project name alone is insufficient.

## User-visible acceptance

- Image: generate with each retained template; verify the selected model, saved
  seed/parameters, preview, complete byte hash and download. Restored custom/v3
  selections stay blocked until explicit builtin reselection. A saved or uploaded
  reference remains visible but blocks image generation until removed. No custom
  graph registration, qualified img2img or v3 composition is available.
- Raw Intelligence: exercise each supported operation on the configured local
  model. Wrong aliases, unavailable providers and partial responses fail without
  a paid call, hidden fallback or fabricated successful result.
- Agent: open two separately granted users' conversations; check personality/model
  snapshots, continuation, revocation and request uncertainty. If OpenAI is enabled,
  test explicit full-context export consent and the configured cost/input ceilings.
  Declined or legacy-local history must not be sent remotely.
- Speech/Music: explicitly enable only the installed native contracts. Confirm
  expected output roles, decoding/playback, complete retrieval and actual model
  identity. Transcription consumes an owned generated WAV. No reference audio is
  added to the retained Speech profile.
- Ownership: across both accounts, confirm another user's execution, input, asset,
  conversation, prompt and download are inaccessible. Check logout/account changes
  during dispatch and transfers; late responses must not expose the revoked scope.
- Uncertainty: record a real ambiguous response only under an approved scenario.
  Preserve the existing reservation and request UUID. Reconnect/restart never means
  replay. Distinguish a catalog pass, provider availability, known terminal outcome
  and physically released GPU capacity.

Generation has one shared active-job reservation across enabled providers.
Independent account sessions do not promise simultaneous inference. Preserve
source-owned uncertainty/recovery protections during rollback; a missing queue
record is insufficient proof of release.

## Historical evidence

The previous [2026-10-03 acceptance record](https://github.com/flamoris-jp/flamoris-studio/blob/abba5963a5e680f74b7fda717de5c7dc96a7d571/docs/REAL_MACHINE_ACCEPTANCE.md)
contains earlier CI and the existing-deployment builtin Image/transfer receipt.
It neither proves this cleanup is deployed nor authorizes its old registration,
verification, v3 or Runtime-bridge instructions. Preserve that evidence in Git
history and attach new operational receipts to the appropriate owning Issues.
