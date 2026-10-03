# Real-machine acceptance handoff

Tracking: Studio [#39](https://github.com/flamoris-jp/flamoris-studio/issues/39),
Generation [#19](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/19) /
[#25](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/25) /
[#42](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/42),
Runtime [#19](https://github.com/flamoris-jp/flamoris-ai-runtime/issues/19),
Agent [#18](https://github.com/flamoris-jp/flamoris-ai-agent/issues/18) /
[#24](https://github.com/flamoris-jp/flamoris-ai-agent/issues/24).

This checklist separates reviewed source from deployed service, real provider,
codec and owner-isolation receipts. Merged source and CI do not establish a live
pass. Native Runtime/model expansion is deferred; the existing builtin Image
route can be tested before qualifying registered Image workflows. Publishing this
document does not deploy, activate a runtime or download a model.

## Source and deployment receipts

| Repository | Reviewed merged source | Delivered boundary |
| --- | --- | --- |
| Generation | `89a6a4079ee10d87a9c67e738a69a28ef09ea637` ([#57](https://github.com/flamoris-jp/flamoris-generation-mcp/pull/57), [#58](https://github.com/flamoris-jp/flamoris-generation-mcp/pull/58)) | Existing Image/managed input/transfers and pinned Image v3; private Runtime delegation/owned cleanup seams; signed external provenance/replay fences and opt-in no-reference Irodori Speech |
| Hub | `c14357342f1280d723c0f0ce18f321c273b93d15` ([#31](https://github.com/flamoris-jp/flamoris-mcp-hub/pull/31), [#32](https://github.com/flamoris-jp/flamoris-mcp-hub/pull/32)) | Exact catalog comparison, opt-in v3 catalog, credential-bound external provenance/bounded session bindings and native ImageContent MIME/data preservation checks |
| Studio | `5b825190ebecac069ecc52e0f326a6f408041c2a` ([#49](https://github.com/flamoris-jp/flamoris-studio/pull/49), [#50](https://github.com/flamoris-jp/flamoris-studio/pull/50), [#51](https://github.com/flamoris-jp/flamoris-studio/pull/51)) | Owned Image/results/inputs/v3 and optional Agent panel; filenames/login/proxy hardening, exact-grant external import, no-reference Speech editor/request fences and large-transfer/account-switch cleanup |
| Agent | `e3e05a71d3627bfa3dabb65ecc520e5dacbb003d` ([#26–31](https://github.com/flamoris-jp/flamoris-ai-agent/pull/31)) | Persisted principals, scoped HTTP, local-only Intelligence adapter, Image context/availability and owner retirement |
| Intelligence | `043b39b064fbedf9ed9a3e9e9eb57c6856efbb5c` | Existing synchronous inference/discovery contract inspected for the Agent adapter |
| Runtime | `f4ca8b785d16947909a24ef909899470d044eaab` | Private compiler/pinned-admission and isolated-target bridge foundations; deployed cross-service settlement remains separate |

Generation [CI 37091966711](https://github.com/flamoris-jp/flamoris-generation-mcp/actions/runs/37091966711)
passed 498 tests and wheel/container checks; Hub
[CI 37093198119](https://github.com/flamoris-jp/flamoris-mcp-hub/actions/runs/37093198119)
passed 136 tests; Studio
[CI 37093318372](https://github.com/flamoris-jp/flamoris-studio/actions/runs/37093318372)
passed fresh migration 09, all 283 backend tests with PostgreSQL 17, 74 frontend
tests/build and container checks. These are source receipts. Speech health reports
`runtime_verified=false`; actual installed-model execution is evidenced separately.

For every scenario record UTC time, actual deployed commit **and** running image
digest, clean/dirty checkout, DB migration versions, exact public tool catalogs
and selected configuration. A checkout or image tag alone does not identify a
running build. Read-only observations of the existing deployment showed an older
legacy Image catalog; do not infer new-source deployment from healthy containers.
Keep credentials, private endpoints/paths, prompts, cookies and raw logs out of
public reports; retain sanitized byte counts/hashes, status codes and failure classes.

The read-only live route reported Generation version `0.1.0`, available
`image.generate` and legacy Workflow descriptors; the running image digest/source
build was not established. An existing-deployment baseline on 2026-10-03 completed
one builtin JANKU Image
job at 512×512, 8 steps and seed 20261003. Native MCP ImageContent rendered in the
explicitly supported client. Bounded prepare/read returned one complete 165394-byte
PNG; actual decoding and SHA-256
`539ebe5e35be31f237fab01bb2e9cd38d0cc1af5509630d8684cbe6dacf3a2b6`
matched both the whole-file and chunk receipts. This proves the old builtin
route and that transfer only; it does not prove the batch above is deployed,
Studio browser behavior, registered/v3 qualification or a general rendering guarantee.
No Runtime activation or deployment was performed for that baseline.

## Operator preflight and paired rollout

Follow [DEPLOYMENT.md](DEPLOYMENT.md), including persistent data/thumbnail backups,
HTTPS and explicit trusted proxy IP/CIDR configuration. Use two dedicated Studio
accounts with distinct cookie jars. Apply existing migrations, including
`20261003_07` for shared login admission, `20261003_08` for external import and
`20261003_09` for durable Speech request fences. Agent integration also requires
`20261003_05`. Review operator grants separately
from schema application. Preserve any uncommitted deployment changes.

[Studio #23](https://github.com/flamoris-jp/flamoris-studio/issues/23)'s naming
consideration was implemented by [#25](https://github.com/flamoris-jp/flamoris-studio/pull/25):
current `compose.yaml` fixes `container_name: flamoris-studio`. This batch preserves
that source decision. Select the service with `docker compose ... studio` or Compose
labels in monitoring. A separate project name alone does not avoid that global
container-name collision: simultaneous review/rollback deployments require an
explicit override/removal of the fixed name plus separate ports/storage/configuration.

1. Pause Studio, direct-client and automation submissions. Back up compatible
   images/configuration, Studio DB, Generation journals/definitions/recipes and
   materialized outputs. Drain or explicitly reconcile unknown provider work.
2. Replace the single Generation instance and the Hub's **deployed** Generation
   YAML together; preserve its private endpoint/authentication. Default legacy
   and opt-in v3 templates are different catalogs. Example files whose names start
   with `_` are not active deployment configuration.
3. Restart Hub and compare the complete upstream tool schemas/annotations on a
   fresh connection. A mismatch blocks the whole Generation connection, including
   health/status, before forwarding. Then deploy Studio and reopen ingress.
4. For Hub routing, set private `STUDIO_GENERATION_NAMESPACE=generation` and the
   backend token; direct Generation uses an empty namespace. Never fall back to
   another endpoint, namespace or anonymous credential after failure.
5. Roll back a compatible Studio/DB and Generation/Hub catalog pair while retaining
   newer persistent data. Do not downgrade immutable versions or discard fences.
   Do not disable v3 while an active v3 journal needs reconciliation. A cleared
   client request is not proof that the provider or its physical resources stopped.

Only enable flags supported by the matched catalog. See
[IMAGE_V3_OPERATION.md](IMAGE_V3_OPERATION.md) for the optional v3 pair and
[EXTERNAL_GENERATION_IMPORT.md](EXTERNAL_GENERATION_IMPORT.md) for private identity
grants. Health, model names and file presence do not qualify a registered workflow.

## Minimal Image acceptance, in order

1. Confirm `system.health`, `capabilities.list` and `workflows.list` through the
   selected route. Run one existing builtin Image workflow with a small installed
   model/output from account A. Record terminal success, decoded dimensions,
   thumbnail, preview and full download. This compatibility smoke does not certify
   registered/v3 readiness. Check native Image Styles/LoRA/preferences and Use
   settings where supported; names must persist across reload/download and omit
   prompts, private paths and upstream identifiers.
2. From B request A's execution status/result/cancel and asset
   content/thumbnail/download/delete using A's Studio handles. Require denial
   without A's metadata or an upstream operation. Use valid CSRF for write checks;
   a CSRF rejection alone does not prove owner enforcement. Login rate windows
   must expire across workers; only configured proxies may supply client addresses.
   Switch A to B while A's submit/cancel/settings reply is pending; late replies
   must not alter B's view or draft.
3. Test an owned image above 512 KiB, preferably the original approximately 5.4 MB
   PNG failure case. Metadata/thumbnail success and full-transfer success are
   separate observations. The bounded prepare/read path must complete and the
   full download match Generation's prepared whole-file SHA-256. Image download
   uses a full response; the audio/video Range checks below do not apply to it.
4. During a throttled transfer revoke the test login or delete its owned asset
   through the normal authorized operation. Further segments must stop at the next
   authorization boundary; a late error truncates the response. Disconnect before
   the first body chunk must also release the slot. Later authorized transfers
   must obtain slots. A failed preview/transfer must preserve metadata
   and must not resubmit generation.
5. Materialize an owned result, then restart through the operator procedure and
   retrieve the same retained bytes/metadata. Separately record the expected
   limitation for provider-only, unmaterialized bytes. Do not replay an uncertain
   submit with a new request identity.
6. Create a managed Image input snapshot from an owned output. Verify B cannot
   select/read it; the snapshot remains immutable after source asset deletion.
   Check expiry, byte/count quotas and deletion/restart accounting. Provider-original
   output/upload cleanup is a separate retention obligation, not implied by deleting
   a Studio row or a Generation local copy.
7. Test Reference only after the registered img2img definition has current exact
   automatic verification **and** the independent managed-input infrastructure
   gate has real evidence. Use the installed graph/node contract; the packaged
   example is a candidate. Confirm bounded decode/staging, selected snapshot,
   decoded output and provider-upload retention before exposing the feature.
8. Test opt-in v3 last: register/pin and automatically verify the **exact parent**,
   then select/build/submit it from Studio. Require the same root version/digest,
   supported dimensions, `primary_image` output role and one decoded result.
   Changed/revoked/stale pins must fail before POST. An unavailable catalog preserves
   existing rows and cannot submit, switch a provider or bypass readiness.

Registered v2/v3 Image needs a trusted provider-host mutation authority, independently
of deferred native Runtime work. `FLAMORIS_RUNTIME_EVIDENCE_FILE` names an atomically
published record with a separate stable regular `<record>.lock` inode. The record,
lock and parent directories must be writable only by the trusted authority.
Require `exclusive-mutation-lock-v1` continuity, a never-reused restart epoch,
actual SHA-256 measurements of core/dependencies/config, node interfaces/implementations
and model contents, and fresh expiry at most 300 seconds away. Every writer/restart
must take the exclusive lock, withdraw evidence, advance epoch, mutate/measure and
atomically publish; Generation holds a shared lock across staging through POST.
An existing lifecycle supervisor qualifies only if it enforces this entire contract.
A read-only inventory, `object_info`, filenames, stat data or hand-written JSON
cannot replace it. `FLAMORIS_MANAGED_INPUT_READY=true` is an independent operational
gate and never makes a Definition ready. Missing authority blocks steps 7–8;
report it explicitly while completing the builtin Image/ownership/transfer steps.

## Optional external provenance import

Use only after the matched Hub/Generation/Studio source and migration 08 are deployed.
Provision two distinct external credentials with stable opaque issuer/subjects and
one-to-one operator bindings to existing Studio accounts. A shared backend token
or MCP session ID cannot identify a Studio person.

1. Submit a small external job under credential A. Verify completed job and every
   asset carry exactly the authenticated provenance; forged caller metadata cannot
   change it. Shared legacy anonymous jobs must remain ineligible for import.
2. Import explicitly as the bound Studio account. The status/list/status check
   registers complete bounded metadata without fetching media bytes. Unknown/large
   byte size or unavailable old bytes must not silently lose catalog rows.
3. Reject B, missing/changed grants, partial/foreign asset provenance and an ordinary
   Studio owner's conflicting job. Check concurrent/idempotent import and restart.
   Public job-ID entry is input, never an identity grant; no private linking fields
   appear in the browser. Imported Image results have no fabricated Use settings.
4. Remove a grant during metadata reads and require rejection before commit. Remove
   it after a successful import and verify original catalog ownership stays fixed.
   Frozen output sets/deletion claims must prevent reimport or reassignment; existing
   owner checks still govern downloads and managed input snapshots.

## Optional no-reference Speech and media transport

First record the merged native `speech-no-reference` schema-4/version-1 descriptor,
provider `irodori`, capability `speech.generate` and `irodori-no-reference-v1` profile.
Configure private `FLAMORIS_IRODORI_CONFIG` and enable `STUDIO_SPEECH_ENABLED=true`
only on the matched deployed source. Follow Generation's
[IRODORI_PROVIDER.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/main/docs/IRODORI_PROVIDER.md).
The trusted local configuration pins upstream
`89f9d8fbd4d51ea019867ee1197725ede1df13c5` and an operator-selected model revision;
the live receipt must separately identify actual installed model/codec/tokenizer
contents, offline environment and watermark cache. Resource presence does not
measure or prove the weights. No model download or arbitrary browser
path/reference/model control is part of this route. Source/config presence with
`runtime_verified=false` is not a successful GPU receipt. Activation uses the owning
server manager/GPU authority; native Speech does not borrow Image attestation.

An actual official MCP SDK loopback checked Generation/Studio descriptor parity,
six boundary builds and rejection of ten invalid parameter sets, three Image-only
attestation preconditions and source/bound drift. Missing resources stayed disabled
with `runtime_verified=false`. This verifies the source protocol: it submitted
no job, loaded no model and produced no real audio.

1. Discover/build/submit one bounded no-reference job and inspect actual completion.
   Use text within 512 characters/2048 UTF-8 bytes, seconds 0.5–30 and steps 1–80.
   Require one `audio` role/index-0 WAV, at most 8 MiB, valid mono 8–96 kHz audio
   and at most 31 seconds. Download, decode and play the actual output.
2. From two Studio accounts check Speech execution/result/cancel ownership and
   player/download privacy. Native players have controls and do not autoplay.
   Verify real seek/codec playback separately from transport integrity.
3. For audio/video routes request `bytes=0-3`, `bytes=3-` and `bytes=-4` when size
   permits: require 206, exact Content-Range/Length and matching downloaded bytes.
   Invalid/unsatisfiable/multiple ranges yield 416 when If-Range is absent or
   matches. Matching strong If-Range ETag retains the range; mismatched/weak/date
   validators ignore Range and yield bounded full 200.
   Isolated ranges prove chunk/representation identity, not a whole-file rehash.
4. Exercise the shared Generation reservation, cancellation of only the owned
   process group and confirmed wait. Induced unknown responses/restart must retain
   reservation/fences until reconciliation; navigating away/back or refreshing
   must preserve the uncertain attempt without a new submission. Account changes
   must reject late private replies. No global interruption or automatic retry.

Run other WAV/MP3/MP4 tests only for genuine qualified provider contracts. Safe
MIDI/JSON/PSD/unknown outputs remain attachments with retained metadata, without
guessed renderers, Image reference or Use settings. CI fixture bytes do not prove
real codec playback, speech quality, Music or Video readiness.

## Optional Agent service, then Studio

Apply Agent DB migrations 002/003, exact principal grants and bounded owner
maintenance; Studio requires migration 05 and private account mappings. Follow
[SCOPED_ASSISTANT.md](SCOPED_ASSISTANT.md). Shared catalog is exactly `health`,
`sessions.open`, `ask_scoped`, `ask_availability`; fixed mode is `health`, `ask`.
Verify exact Hub parity and explicitly attested `local_only` Intelligence transport
for all personality/history/context. Discovery alone cannot establish locality.

1. Open two granted principal tuples and require fresh scoped availability. Stale,
   busy or `not_checked` dependencies must not dispatch. Disabled grants, foreign
   handles and expired/revoked sessions fail closed.
2. Send one bounded text turn and one immutable metadata-only Image context.
   Instruction-like text stays untrusted; URLs/paths/binaries/unknown fields and
   oversized combined input reject before inference. Require safe public provenance
   and no Generation job, runtime activation or hidden action.
3. Check continuation isolation, durable duplicate request-UUID denial across
   sessions/restart, timeout/ambiguous dispatch and incomplete output. No inference
   replay, remote fallback or fabricated complete answer is allowed. Retention
   maintenance preserves history/request fences; do not reset them to free capacity.
4. Repeat through two Studio account panels. Fresh availability enables send;
   unavailable observations retain the unsent question. Revoking login/changing
   mapping during ask withholds the answer while preserving the durable fence.
   Account/session changes clear private advice; Start new conversation clears the
   old question. Studio stores references, not question/draft/answer transcripts.

## Remaining gates and acceptance record

| Tracker/boundary | Remaining evidence or scope |
| --- | --- |
| Studio #20 transfer foundation | Bounded source implementation is delivered; the large real asset/hash/browser receipts above qualify deployment separately |
| Generation #30 managed inputs | Image/WAV source contracts are delivered; actual Reference and future audio consumers require their own installed/provider/retention receipts. No-reference Speech does not consume WAV inputs |
| Generation #19/#42 registered/reference Image | Installed graph/node export, trusted mutation authority, upload retention and exact automatic live verification |
| Generation #25/#31 provider media | Real deployed Music/SheetSage contracts and sanitized output/cancel/restart receipts; no-reference Speech is a separate first slice, not all multimedia completion |
| Generation #45 foundation | Source foundation is closed; production multimodal composition/profile acceptance is not implied by that closure |
| Runtime #19 | Native Runtime/model expansion deferred. Private bridges are implemented; authenticated transport, physical settlement and complete result publication remain separate production work |
| Agent #18/#24 | Operator maintenance/mappings and deployed two-principal acceptance; remote targets, new contexts and proposals are separate slices |
| Studio #39 | Remaining profile-specific Music/Video/structured contexts and proposals, plus the operator receipts above; delivered Image/Agent/import slices are not absent |

Use one record per scenario: UTC; deployed source/image/catalog/DB revisions;
expected and actual observation; pass/fail/blocked; sanitized evidence reference;
owning tracker. Identify a missing operator prerequisite separately from a source
defect. Do not close an acceptance-gated umbrella solely because CI or one builtin
Image smoke passed.
