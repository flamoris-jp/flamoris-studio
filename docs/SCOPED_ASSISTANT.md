# Authenticated Image assistant

Owning issue: [#43](https://github.com/flamoris-jp/flamoris-studio/issues/43),
part of #39 and Agent #18/#24. This delivery adds text advice in the Image panel
and standalone Intelligence view. It does not apply edits, run Generation, switch
models, activate runtimes, fetch assets or execute proposals.

## Account and service authority

The authenticated Studio account UUID is selected from the current server-side
login, never from request identity fields. The operator privately configures:

| Setting | Contract |
| --- | --- |
| STUDIO_AGENT_ENDPOINT | Direct protected Agent MCP HTTPS endpoint or trusted loopback tunnel; no URL userinfo/query/fragment or remote plain HTTP |
| STUDIO_AGENT_TOKEN | Backend-only Bearer token, 32–512 ASCII letters/digits/underscore/hyphen |
| STUDIO_AGENT_BINDINGS | JSON array of at most 128 exact `{user_id,human,agent,project}` records |

Each record uses a canonical existing Studio UUID and safe Agent stable keys.
Human and Agent keys are 1–64 ASCII letters/digits/underscores/hyphens. Project
keys additionally permit dots separating nonempty segments, such as
`example.project`, with the same 64-character total limit. Paths, whitespace,
leading/trailing dots and empty segments are rejected. Keys remain exact;
no normalization, project renaming or extra delegation grant is implied.
Duplicate account UUIDs and duplicate complete principal tuples are refused; two
Studio users cannot silently share the same principal/session. No email/name
heuristic, environment principal fallback, browser identity override or runtime
setup/grant provisioning exists. Empty/missing/invalid mapping disables assistance
without disabling Image generation. Resolve account IDs through the operator's
private Studio administration path and provision the matching exact delegation
grants through Agent's owner operations; a service token alone is insufficient.

Session mappings are bound to the exact selected tuple, endpoint and credential
configuration through a private digest. Rotation/configuration changes require a
new authorized session and invalidate old continuation handles. Private keys,
endpoints, upstream session/conversation UUIDs and Agent credentials are absent
from browser DTOs. Account UUID configuration stays server-side.

## Protocol and readiness

The direct Agent service must expose exactly `health`, `sessions.open`,
`ask_scoped`, `ask_availability`, with the expected bounded request fields. Every
connection negotiates the actual SDK catalog. Fixed `health`/`ask`, an aggregate
Hub catalog, schema mismatch or pagination fails closed; there is no legacy ask or
direct Intelligence fallback. Review deployed Hub/service parity separately.

`POST /api/assistant/availability` requires login and CSRF, opens/reuses an Agent
principal session and asks Agent-owned availability. At most two probes per Studio
process, 40-second total probe bound; cached valid sessions avoid repeated opens.
Race losers reuse the winning DB mapping; extra unused upstream bindings remain
subject to Agent's retention policy. Studio holds no row transaction over remote
I/O. There is one current Studio mapping per account, surviving Studio restart.

Ready requires Agent's real prerequisite observation, integer principal revision
1, supported context revision 1, no proposals, bounded UTC freshness of at most
five seconds and available=true/state=ready consistency. Health/liveness alone
never enables send. Browser checks while the panel is open, preserves unsent
questions while offline/busy and disables stale observations. Opening a panel can
establish a short-lived binding but performs no inference or activation.

Default direct Agent execution cannot produce reviewed assistant readiness. Use
Agent's explicitly approved local-only Intelligence MCP target and configuration
from its owning [INTELLIGENCE_MCP.md](https://github.com/flamoris-jp/flamoris-ai-agent/blob/main/docs/INTELLIGENCE_MCP.md).
Discovery is not loaded-model/locality attestation. Agent still validates actual
approved DB/model identity and full prompt budgets before its own writes/dispatch.
There is no implicit remote export/fallback or API-target selection in this UI.

## One-shot advice and persistence

`POST /api/assistant/ask` accepts only requestId, current opaque Studio sessionKey,
text, optional owned previousHandle, optional Image scalar draft and draftRevision.
It requires current login/CSRF and current configured mapping/session. Owner-scoped
completed handles translate privately to Agent closed-parent UUIDs; another user,
changed session/configuration or incomplete attempt is refused before inference.

Question UTF-8 is at most 16 KiB. Request body is at most 36 KiB, read within five
seconds. Optional context includes only prompts, reviewed numeric Image settings,
server-selected opaque context identity, revision and empty asset selection. The
entire UTF-8 serialized context is at most 16 KiB. Model/LoRA selection, Workflow
metadata, asset descriptors, paths/URLs/binaries and unknown fields are not accepted
in this slice. The UI requires explicit Attach current Image draft, initially OFF,
and explains that the snapshot becomes Agent conversation content. Attachment is
not long-term Memory promotion. Agent owns personality/history/context trust and
combined export/budget enforcement; text advice grants no editing/tool authority.

Studio freezes the payload in memory before any remote ask. Migration
`20261003_05` adds owner-scoped session/reference tables and a unique
`(user_id,request_id)` fence. It stores only reference UUIDs, binding/request
digests, timestamps and presentation observations; no question, draft, answer,
transcript, credential or private endpoint is persisted there. Agent remains
conversation authority. These metadata fences survive restart and are not
automatically deleted; separate reviewed reference retention is needed before
removing them. DB downgrade refuses to silently erase these durable fences.

Fresh Agent availability is checked on send. Before the paid tool call, current
Studio login/mapping/session are checked again after MCP negotiation. A durable
uncertain marker is committed before dispatch; the one process-wide ask slot
prevents accidental concurrent local sends. A valid response binds exact request
and upstream session IDs and safe public provenance. Login/config/session are
rechecked before recording completion or publishing the answer. Later revocation
may race an already authorized admitted turn; its result is withheld when the
current Studio authorization has changed. Backend response text is bounded to
64 KiB and rendered as escaped plain text, never HTML/Markdown tool actions.

Failure, cancellation, logout or a lost result keeps the original fence/snapshot
identity. No automatic ask retry, new UUID, cached answer or request-recovery API
exists. A duplicate does not prove success. The UI locks an uncertain send while
preserving the question; explicit Start new conversation clears it before a new
question. A fresh session cannot continue an old scoped parent. Studio observations
are not an Agent job state machine or fabricated Intelligence polling job.

## Transport and rollout

Each call opens/closes its own official MCP SDK transport/task groups. Metadata
calls are bounded to 20 seconds, ask to 150 seconds; no proxy environment,
HTTP redirects, compressed responses or HTTP transport retry. Wire responses are
bounded to 256 KiB and structured replies to 128 KiB. Private SDK/HTTP diagnostics
are redacted within these calls; upstream error strings are never forwarded.
All assistant responses use private/no-store and nosniff.

Apply Studio migration `20261003_05` with the existing migration-role/backup
procedure before starting this code, even if the optional assistant remains off.
Deploy Agent's scoped contract and migrations 002/003, exact grants, local-only
Intelligence target and verified catalog separately. Configure explicit bounded
Agent owner maintenance using
[PRINCIPAL_RETENTION.md](https://github.com/flamoris-jp/flamoris-ai-agent/blob/main/docs/PRINCIPAL_RETENTION.md).
This source delivery does not run migrations, provision mappings/credentials or
enable that maintenance. Use the established one-process service deployment;
process-local concurrency is not a distributed inference scheduler.

## Acceptance boundaries

Tests cover actual in-process MCP negotiation/Bearer transport, fixed-catalog
refusal, private malformed responses/no retries, question/context bounds, real
PostgreSQL two-user mapping/continuation/duplicate fences, in-flight logout and
configuration changes, no Generation dispatch, explicit attachment, stale/offline
UI, escaped advice and account-view clearing. Fake inference and fixture DB tests
are separate from deployed multi-user/model acceptance.

Remaining #39/#24: selectable approved Agent/target policies, authorized Workflow
and asset metadata attachments, other media contexts, proposals/diff/Apply,
additional media editors and Runtime composition integration. Actual operator
configuration, coordinated rollout, retention cadence and two-principal live
acceptance remain necessary. See [REAL_MACHINE_ACCEPTANCE.md](REAL_MACHINE_ACCEPTANCE.md).
