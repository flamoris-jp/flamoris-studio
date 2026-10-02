# Real-machine acceptance handoff

Tracking: Studio [#39](https://github.com/flamoris-jp/flamoris-studio/issues/39),
Generation [#45](https://github.com/flamoris-jp/flamoris-generation-mcp/issues/45),
Runtime [#19](https://github.com/flamoris-jp/flamoris-ai-runtime/issues/19),
Agent [#18](https://github.com/flamoris-jp/flamoris-ai-agent/issues/18) /
[#24](https://github.com/flamoris-jp/flamoris-ai-agent/issues/24).

This is an operator acceptance procedure, not evidence of deployment or a live
pass. CI fixture media, in-process MCP tests and compiler tests establish
different claims from real-provider generation, codec playback and GPU scheduling.
Keep those evidence classes separate. No production activation is performed by
publishing this document.

## Source and deployment receipts

The reviewed source baselines for this batch are:

| Repository | Source baseline | Delivered boundary |
| --- | --- | --- |
| Generation | `63022b2dc6e1564f68b4b51e07cc9ca6b1702d10` ([#47–50](https://github.com/flamoris-jp/flamoris-generation-mcp/pull/50)) | Immutable workflow/composition contracts, explicit provider bindings, output roles, internal single-provider graph lowering |
| Agent | `e3e05a71d3627bfa3dabb65ecc520e5dacbb003d` ([#26–31](https://github.com/flamoris-jp/flamoris-ai-agent/pull/31)) | Persisted principals, authenticated scoped HTTP, local-only Intelligence MCP, Image context/availability and explicit owner binding retirement |
| Intelligence | `043b39b064fbedf9ed9a3e9e9eb57c6856efbb5c` | Existing synchronous inference/discovery contract inspected for the Agent adapter |
| Runtime | `4db0147165c6c0493806c6b0c0d0a8c688f9288b` ([#20](https://github.com/flamoris-jp/flamoris-ai-runtime/pull/20)) | Reviewed composition bridge design; no executable bridge delivered |
| Studio | `bfc93db0ebb2e76a957df9451e55c5890d036f00` ([#41](https://github.com/flamoris-jp/flamoris-studio/pull/41)) plus [#44](https://github.com/flamoris-jp/flamoris-studio/pull/44), record its final merged SHA | Multimedia retrieval plus optional account-scoped text/Image advice |

For every live run, record UTC time, deployed commit/container digest, clean or
dirty checkout, database schema/migration versions, exact public MCP catalogs and
the operator-selected test configuration. A source merge is not a deployment
receipt. Read-only repository inventory on 2026-10-02 still observed Generation
`cd5d6011bc0e35c274e8a9d7c1780cdd42100b53`; this does not certify that a running
process uses that checkout or includes the new batch. Obtain actual service
version/catalog receipts before testing new boundaries.

Keep credentials, private endpoints/paths, prompts, cookies and raw private logs
out of public acceptance reports. Store only sanitized observation identifiers,
byte counts/hashes, status codes and selected failure classifications.

## Operator preflight

Follow [DEPLOYMENT.md](DEPLOYMENT.md) for the selected Studio host, migration and
runtime role separation, persistent thumbnail storage, HTTPS and registration
policy. Verify current private configuration before replacing a running service.
The multimedia result change needs no new Studio SQL migration; existing
Alembic migrations must already be applied. Take the established backups before
any schema operation. Use two dedicated test accounts with distinct cookie jars.

Use the owning Generation MCP for discovery, workflows, jobs and assets. A
healthy HTTP process is not a ready workflow, materialized file or loaded model.
Verify actual capability/provider/workflow profile/revision and readiness before
submission. Do not point Studio at provider paths or fabricate an output/job row
to qualify an unsupported provider. Generation/Intelligence endpoints and keys
remain backend-only. Choose small outputs within configured byte/time limits.

Agent checks require its separate operator DB migration, exact principal grants,
private authenticated HTTP configuration and an approved Intelligence target.
Apply `db/migrations/002_principal_sessions.sql` through the Agent's established
owner operations path; it provisions no grants. Then apply 003 and configure
explicit bounded owner maintenance following Agent PRINCIPAL_RETENTION.md.
Apply Studio migration 20261003_05 and review [SCOPED_ASSISTANT.md](SCOPED_ASSISTANT.md)
for the exact direct Agent endpoint/token and Studio-account mapping. Follow Agent
[PRINCIPAL_SESSIONS.md](https://github.com/flamoris-jp/flamoris-ai-agent/blob/52cc30d5eaacd443a0d7d7fc47058a01c7bf035a/docs/PRINCIPAL_SESSIONS.md),
[INTELLIGENCE_MCP.md](https://github.com/flamoris-jp/flamoris-ai-agent/blob/52cc30d5eaacd443a0d7d7fc47058a01c7bf035a/docs/INTELLIGENCE_MCP.md)
and [STUDIO_CONTEXT_V1.md](https://github.com/flamoris-jp/flamoris-ai-agent/blob/52cc30d5eaacd443a0d7d7fc47058a01c7bf035a/docs/STUDIO_CONTEXT_V1.md).
Verify exact Hub catalog parity before shared mode. Never infer locality from
model discovery: the configured target must explicitly attest `local_only`,
including all transmitted personality/history/context. Remote data flow is absent.

## Existing Image regression, then result transport

1. With Generation unavailable, log in and inspect the catalog. Unavailability
   preserves rows and cannot trigger an implicit submission or provider switch.
2. With a real ready Image workflow, submit one small image from account A.
   Observe actual completion, thumbnail, full preview and downloaded bytes.
   Check LoRA, Styles, preferences, reference selection and Use settings on a
   native Image output. Repeat discovery after restart without guessing readiness.
3. From B, request A's execution status/result/cancel and asset
   content/thumbnail/download/delete using A's opaque handles. All must refuse
   access without exposing A's metadata or dispatching its upstream operation.
   Inspect that A's own references still work. Use valid CSRF for write checks;
   a generic CSRF failure alone does not prove resource ownership enforcement.
4. Materialize A's output through the owning Generation service. Restart only
   through the selected operator procedure, then retrieve the retained materialized
   asset. An unmaterialized old provider result is a separate expected limitation.
5. For an actually qualified provider returning WAV/MP3 or MP4, inspect cataloged
   MIME, output-role metadata (if assigned by the adapter), byte count and download
   link before opening playback. Native players require explicit controls and do
   not autoplay. Check seek/range behavior and real codec playback independently.
6. On the authorized asset route request `Range: bytes=0-3`, `bytes=3-` and
   `bytes=-4` when size permits. Verify 206, exact Content-Range/Length and returned
   bytes against the downloaded file. Invalid/unsatisfiable/multiple ranges yield
   416. Repeat with a matching strong If-Range ETag, then a mismatched/weak/date
   validator: the latter yields bounded full 200. Never mix receipts/digests.
7. Full download must match Generation's prepared whole-file SHA-256. An isolated
   range checks chunk integrity and trusted representation identity; it does not
   independently rehash the whole file. Report those claims separately.
8. For actual MIDI/JSON/PSD/unsupported safe MIME outputs, verify metadata persists
   and content is an attachment, with no guessed image/iframe/code renderer or
   Image reference/Use settings admission. Do not claim notation/PSD editing.
9. Throttle an owned transfer and revoke its login or delete the owned asset using
   the normal authorized operation. Further segments must cease at the next
   authorization boundary. A late streaming error terminates the response; headers
   already published cannot become a fabricated successful full download. Check
   that later authorized transfers can obtain slots and metadata remains intact.
10. Confirm a preview failure, transport timeout or size limit does not erase the
    catalog or retry generation. Explicit reload is allowed. An uncertain submit
    must not be automatically replayed with a new request identity.

Only run steps 5–8 when genuine provider outputs/contracts exist. Otherwise record
them as blocked. Ten-byte CI fixtures are not live audio/video or quality evidence.

## Agent service acceptance, then Studio account integration

The delivered Agent HTTP catalog in shared mode is exactly `health`,
`sessions.open`, `ask_scoped`, `ask_availability`; fixed mode remains `health`,
`ask`. Do not expose the delegator Bearer credential to a browser. Studio's actual
account mapping/context panel has a delivered optional text/Image slice, but direct
service acceptance alone cannot be reported as end-to-end Studio assistance.

1. Open two exactly granted Human/Agent/Project tuples through the trusted test
   delegator. Missing/disabled grants and another caller's UUID must fail closed.
   Verify distinct immutable bindings and independent request-local Agent contexts.
2. Query availability with a current scoped UUID. Require fresh real prerequisite
   observations. `health` with dependencies `not_checked`, stale observation or
   direct transport alone cannot enable the assistant. Busy must not dispatch.
3. Submit one bounded text turn using an approved local Intelligence MCP target.
   Verify public alias/capability/execution provenance and approved DB identity,
   with no runtime activation, Generation job or hidden tool action.
4. Attach one valid Image context v1 snapshot containing metadata only. Include
   instruction-like text to verify it remains untrusted user context. Reject
   unknown fields/media, URLs/paths/binaries and oversized combined input before
   conversation creation or provider execution. Confirm captured revision is fixed.
5. Test same-session closed-parent continuation, other principal/session denial,
   durable duplicate UUID denial across new sessions, expiry/revocation and
   restart. No cached-answer/recovery guarantee is provided by duplicate denial.
6. Test unavailable/timeout/ambiguous dispatch and length-terminated output. No
   automatic inference retry, remote fallback or completed partial answer should
   appear. Keep the original request identity for an uncertain result.

Sessions are retained at most 128 globally / 32 per delegator, including expired
and revoked bindings. Migration 003 supplies explicit owner-only retirement after
expiry and closed-runtime grace; it preserves all history/request fences. There
is no automatic GC/refresh. Use a disposable acceptance database and the guarded
procedure; never reset history/fences or force uncertain lifecycle completion to
make capacity tests pass. Maintenance cadence remains an operator deployment gate.

After the service scenarios pass, configure two existing Studio accounts to
distinct exactly granted principal tuples. In each private browser session open
the Image assistant or Intelligence view, verify fresh availability, send one text
turn and explicitly attach a small Image draft. Confirm safe public provenance,
escaped advice and no Generation job. Cross-account continuation handles must
fail; unavailable/stale observations preserve the unsent question and disable
send. During a throttled ask revoke the login or change the private mapping;
the answer must be withheld and the durable request fence retained. Refresh must
not replay an uncertain turn with a new request UUID. Explicit Start new
conversation clears the old question before a new send. On account/session change
private advice/parent views must clear. Verify the Studio references contain no
question/draft/answer transcript and survive a Studio restart for duplicate denial.

## Open gates before complete multimodal acceptance

| Tracker | Required next evidence/implementation |
| --- | --- |
| Generation #25/#31 | Exact deployed YuE2/SheetSage/Irodori upstream revision, real HTTP/CLI request/result contract and sanitized actual smoke; qualified adapters remain absent |
| Generation #45 | Real exported/validated Comfy graphs and approved bindings; managed non-image inputs and production lowering/execution integration remain pending |
| Runtime #19 | Executable Generation–Runtime bridge, authoritative lifecycle/cancel/restart/admission observations and live acceptance; reviewed design alone cannot run it |
| Agent #18/#24 | Actual operator maintenance/mapping setup and deployed two-principal acceptance; remote targets, asset/Workflow contexts and proposals remain separate slices |
| Studio #39 | Ready profile-specific Music/Speech/Video editors, managed inputs, structured result presentation and runtime/composition integration |

For each blocker, record the missing contract or implementation and its owning
tracker. Do not close an umbrella issue because one foundation PR passed CI.

## Acceptance record

Use one record per scenario: UTC time; exact deployed revisions and catalog
revision; scenario; expected observation; actual observation; pass/fail/blocked;
sanitized evidence reference; owning tracker for a discrepancy. Mark unavailable
source/version/provider contracts as blocked rather than inferred passes. End with
the covered boundaries and remaining gates, so a successful Image regression does
not imply all multimedia generation or shared-user Studio assistance is complete.
