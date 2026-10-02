# Workflow Image operation

Apply the `20261001_04` migration before starting this version. Deploy matching
Generation MCP and Hub schemas first, with ingress paused and provider work
drained/reconciled; a mixed schema rejects the entire Generation connection.
Generation's trusted runtime mutation authority and automatic bounded verification
establish Definition readiness. Studio never registers a graph or overrides it.
The independent managed-input infrastructure gate must also be ready for img2img.
No merge, deploy or live acceptance is implied by repository tests.

## Selection and restoration

Discovery normalizes bounded graph-free Image-v1 metadata. Builtins retain their
compatibility basis. Only a ready Definition with exact version/digest is selectable.
Arbitrary public parameter keys map by declared roles, with additional scalar
controls rendered from metadata. Unsupported LoRA drafts block submission until
removed or a compatible Workflow is selected. Seeds use cryptographic randomness
within the exact bounded integer, enum and multiple-of domain. Manual values are
checked in Studio and Generation, including the JavaScript safe integer ceiling.

Submit refreshes readiness and identity, resolves an owned input, builds with
`require_ready=true`, then calls `jobs.submit` once. An uncertain submission is not
automatically replayed. Stale selection preserves the draft and requires explicit
selection of the current version. Snapshots record public Workflow identity,
normalized non-input parameters and the Studio input UUID. Raw upstream input IDs
stay in the server. **Use settings** restores the same reference when still available;
an expired, revoked or pruned reference requires choosing another owned Asset.

## Owned references

The picker paginates existing generated image Assets owned by the signed-in user.
There is no local file upload; #30 records the original requirement. Its closed
issue state does not establish local-upload acceptance. Create accepts only a Studio
Asset UUID. Create/get/delete/thumbnail/submit check ownership before upstream calls.
Mutation requires CSRF. The immutable Generation input is independent of subsequent
source-Asset deletion. A missing thumbnail does not make an otherwise valid input
unusable. Detach only removes the draft attachment; replace swaps after successful
creation and leaves the previous immutable input to its bounded lifetime.

Input thumbnails live separately in `STUDIO_THUMBNAIL_DIR/inputs`. They are at most
512 pixels and 256 KiB. PostgreSQL reserves the only possible temporary/final locator
before writing files. Failed or ambiguous upstream creates remain charged; confirmed
compensation marks a terminal row. An uncertain delete remains unavailable until an
explicit retry or expiry reconciliation. Active/uncertain executions protect mappings.

## Quota and retention

The global PostgreSQL transaction advisory lock serializes reservations, deletion
and admission. Contention rejects immediately, before `inputs.create`. Row and byte
quotas count all states, including pending cleanup and unknown creates. A worst-case
256 KiB reservation precedes upstream or filesystem work and is reduced only after
confirmed thumbnail publication or cleanup.

| Environment variable | Default |
| --- | ---: |
| `STUDIO_INPUT_USER_ROWS` | 128 |
| `STUDIO_INPUT_GLOBAL_ROWS` | 1024 |
| `STUDIO_INPUT_USER_BYTES` | 33554432 |
| `STUDIO_INPUT_GLOBAL_BYTES` | 134217728 |

Configuration must be positive, at most 4096 rows or 1 GiB. Maintenance starts with
the application and runs hourly. Indexed batches promote at most 100 expirations
and prune at most 100 eligible rows per pass. Before pruning, maintenance observes
at most 100 expired-reference executions with known Generation job IDs, with a
60-second total budget and five seconds per status call. No DB transaction or
quota lock spans remote IO. A rotating execution cursor prevents unavailable jobs
from starving later rows. Only matching terminal Generation responses update the
cached status; unknown IDs, malformed responses and unavailable jobs remain
protected. Concurrent mapping changes or terminal observations are preserved.
Browser polling is not required to release completed-job reference retention. Expired/revoked mappings have a 24-hour
grace. Protected rows are excluded before the batch limit. Cleanup first persists a
pending-delete marker, removes the recorded final and temporary thumbnail files,
then deletes the mapping. File failures preserve quota accounting for a later pass.
The nullable execution FK uses `ON DELETE SET NULL`; request snapshots, executions
and generated Assets are retained. There is no unbounded directory scan or cleanup
requirement delegated to a manual operator.

## Acceptance still required

Keep #21 and #36 open for the real installed Workflow/runtime smoke, coordinated
catalog rollout, multi-user acceptance, expiry/reselection, independent preview
after source deletion, quota rejection and retention evidence. Local upload remains
follow-up scope, regardless of #30's issue state. Repository CI covers migration
upgrade/downgrade, ownership, exact identity, seed domains, quota contention,
ambiguous outcomes, cleanup retry,
frontend picker/restore behavior, builds and the packaged container.


Workflow submissions omit inactive steps, CFG, seed, sampler, scheduler and denoise
controls without changing the preserved draft. Selected requests allow those
fields to be absent; the descriptor alone determines required controls and
optional defaults. Snapshots record normalized advertised values, including
resolved defaults, rather than inventing values for graph-internal constants.
Legacy automatic builtin requests retain their existing required-field contract.

Scalar `number` enums accept equivalent JSON integer/float values consistently
with Generation, including CFG and denoise values normalized by the API. Integer
parameters remain strict, and booleans/strings are not numeric candidates. The
browser validates advertised string patterns as complete matches, including the
builtin sampler/scheduler token constraints, before enabling submission.

Selected Workflow build responses must include a canonical opaque Workflow ID.
Missing or malformed IDs produce a controlled validation error and a failed
Studio execution before `jobs.submit`; no provider submission is attempted.
