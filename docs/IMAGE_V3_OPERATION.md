# Opt-in pinned Image v3 operation

Advances #36/#39 and FLAMORIS AI #15 using Generation #51 and Hub #30. Current
reviewed source/deployment receipt requirements are in
[REAL_MACHINE_ACCEPTANCE.md](REAL_MACHINE_ACCEPTANCE.md). Studio
consumes Generation's reviewed `image-generate-v1` revision 1 through its existing
Image editor. It does not author/register graphs, attest candidates, switch a
runtime or implement another scheduler. The initial execution profile supports
one audited txt2img leaf inside nested pinned pass-through wrappers.

Set `STUDIO_GENERATION_V3_ENABLED=true` only after deploying a compatible Generation
with `FLAMORIS_WORKFLOW_V3_ENABLED=true` and replacing the Hub's runtime Generation
catalog using `_generation-v3.example.yaml`. The Studio flag defaults to false;
legacy Image discovery/build remains unchanged. An enabled but unavailable or
incompatible v3 catalog fails closed without falling back to a different route.
For a direct Generation endpoint, leave `STUDIO_GENERATION_NAMESPACE` empty.
For a Hub endpoint, set it explicitly to `generation`; all Generation tool names,
including job/asset transfers, then use the Hub's advertised namespace. Set the
private backend `STUDIO_GENERATION_TOKEN` for an endpoint requiring Bearer auth.
It is never returned to the browser. HTTPS or a trusted loopback HTTP tunnel is
required; credential-bearing URLs and redirects are rejected. No namespace or
endpoint fallback/replay occurs after failure. The Hub token authenticates Studio's
backend client group; per-user Execution/Asset authorization stays in Studio.
No new DB migration is needed: existing source-discriminated execution and immutable
request snapshots retain the v3 Workflow kind, expected schema/compiler/adapter/profile
revisions and exact root version/digest. These are checked against the build reply;
Generation retains the complete immutable plan and invocation receipt.

## Paired rollout and rollback

Pause Studio/direct/automation ingress and drain or explicitly reconcile existing
provider work before replacing the Generation singleton. Back up the compatible
images/private configuration, Studio DB and Generation definitions/recipes/journals/
outputs. Replace the Hub's **deployed** Generation YAML with the matching v3 catalog,
preserving its private endpoint/authentication; underscore-prefixed example files
are not active configuration. Restart Hub and compare the entire tool schema and
annotation set on a fresh connection before deploying/enabling Studio. Mixed
Generation/Hub schemas block all Generation forwarding, including health/status.
Do not switch catalogs while requests remain active.

Rollback requires a compatible Studio/DB, Generation/Hub catalog pair and persistent
state while retaining newer data and immutable version/request fences. Do not
disable Generation v3 while an active v3 reservation needs reconciliation: startup
rejects an incompatible journal. Unknown provider acceptance is not a no-send
receipt and cannot be repaired by replay or deleting a reservation.

## Selection and dispatch

The backend translates only the reviewed graph-free descriptor revision 3 into
bounded Image presentation metadata. Known role/schema/cardinality/format domains,
exact canonical root identity, current whole-root readiness, reviewed scalar domain
and the measured checkpoint are required. Unknown/malformed profiles reject;
unqualified roots remain disabled. Fixed-size v3 roots are disabled because this
catalog does not publish effective fixed dimensions; Studio never guesses a size
from a graph, model or Workflow name. Editable width/height remain paired bounded
multiples of eight. Text retains its UTF-8 JSON byte ceiling in browser and backend.

Studio uses `v3:<root-id>` presentation IDs and the `v3` Workflow kind to distinguish
v3 roots from legacy Definitions with the same name/id. This is a catalog identity,
not an authorization grant. The picker and Use settings reuse existing Image role
controls; stale restored versions preserve the prompt and require explicit selection.
No reference, LoRA, sampler or scheduler control is fabricated for the v3 profile.

Submission refreshes discovery and exact version/digest/current readiness before
reserving an owner-scoped Studio Execution. It calls `workflows.v3.build` with the
exact upstream root and server-set `require_ready=true`. The gateway verifies the
returned root, normalized parameters, schema/compiler/adapter revisions, canonical
closure/structural/invocation pins and bounded opaque workflow handle before calling
ordinary `jobs.submit` once. A malformed build cannot cause job submission. Generation
then independently rechecks retained pins, revocation and runtime/model evidence.

Generation alone owns immutable version history and parent qualification. Studio
has no ready override, child-to-parent approval rule or provider-health shortcut.
Operators register and automatically smoke the exact root through Generation's
reviewed tools before it can be selected. Unknown submission results retain the
original Studio execution/request for reconciliation and never automatically replay.

Native Runtime/model expansion is not required to use the existing builtin Image
route. Registered v3 qualification still requires the separate trusted provider-host
mutation authority described in Generation's
[WORKFLOW_VERIFICATION.md](https://github.com/flamoris-jp/flamoris-generation-mcp/blob/main/docs/WORKFLOW_VERIFICATION.md):
fresh measured content identities, a never-reused provider/restart epoch and a
separate stable regular `<record>.lock` inode. Its exclusive mutation/shared admission
lock must cover all writers and restarts; record/lock/parent writes belong only to
the trusted authority.
Health, model names, `object_info`, stat data or manually authored evidence cannot
replace that authority. Its absence leaves v3 unavailable; it does not prevent a
legacy builtin Image regression. The managed-input readiness flag is independent
and does not qualify this txt2img profile.

## User isolation and results

Existing cookie authentication, CSRF and source/owner checks apply. Browser requests
cannot supply an upstream managed input, arbitrary graph, candidate bypass or provider
credential. Studio Execution/Asset UUID mappings remain user-owned. Status, result,
cancel, thumbnail, preview, download and deletion reuse the existing owner-checked
Generation route; upstream job/asset references stay server-side. Output roles and
media bounds use the existing result/transfer contract.

Normal tests include actual descriptor-shape fixtures, strict gateway reply checks,
ready/stale/profile/model/byte-domain fences, two-user PostgreSQL execution ownership,
real editor interactions and restoration. GPU/model installation is not required.
Repository CI is not deployment evidence. Enable this route only after real runtime
smoke and authenticated two-user acceptance on the deployed matched versions.
The minimal sequence is matched catalog discovery, exact-parent automatic
verification, Studio selection/build/submit, decoded `primary_image` output and
full-file hash, then cross-user status/result/cancel/download/deletion denial with
valid CSRF. Verify revocation/evidence expiry and uncertain-submit/restart fences
without replay. Complete builtin Image, large transfer and managed-input checks
using [REAL_MACHINE_ACCEPTANCE.md](REAL_MACHINE_ACCEPTANCE.md).

Multiple native components, Music/Speech/Video **within v3**, fixed-size v3
presentation metadata and production cross-service orchestration remain follow-up
scope. Generation #45's source foundation is closed; it does not establish these
live receipts or close Studio #39's broader acceptance scope.
