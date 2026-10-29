# Reference-upload failure recovery (Studio #99)

Image and Video share the same private reference picker. Uploads remain limited to
one PNG/JPEG/WebP of at most 8 MiB, with finite user/global count and byte quotas.
The editor keeps the draft and any previous attachment when a replacement fails.
It does not automatically repeat an upload or generation request.

## Image generation feedback (Studio #101)

The Image badge says **Service ready** when discovery finds the service available;
this is not a statement that the draft is complete or an attestation of GPU/model
quality. Generate remains clickable when the draft is incomplete, invalid or
waiting for a reference. An explicit click or keyboard submission displays and
focuses a named explanation for the relevant prompt, model, template, reference,
numeric setting or LoRA. Browser-native form validation is disabled so it cannot
silently intercept this explanation; the existing payload validators and all
backend account/input checks still gate actual submission. Prompt length bounds
are checked explicitly for automatic Image drafts.

No request is submitted for an invalid draft, and editing a value never submits
or retries automatically. Correct the indicated settings and press Generate
again. An in-flight Image submission alone disables this button. Reference
preparation is explained on click, and Clear inputs dismisses the explanation.
The header's PHASE 1A development marker is removed from the product UI.

The reported historical Ready-but-disabled condition is not attributed to a
specific saved setting. Current Controller metadata advertises both Image and
Video available; a synthetic busy flag does not disable Studio Image discovery.
This presentation change does not change GPU scheduling or qualify yesterday's
browser session.

## User-visible failures

The picker distinguishes a known quota response from admission busy (both HTTP
409), oversized requests (413), unsupported types (415), incomplete upload,
generation rejection and receipt verification (422), concurrent uploads (429),
authentication/authorization (401/403), and unconfirmed network/server outcomes.
Only allowlisted reasons enter this presentation. Arbitrary provider messages,
validation-input arrays, private identifiers and proxy HTML are not displayed.

The reported intermittent 422 is not yet attributed to a specific check. The new
messages identify the failing boundary; existing MIME, digest, identity, size and
expiry verification remain unchanged. A later successful upload does not prove
that intermittent failures are resolved.

## Reservation accounting

An upload reserves its full original bytes and thumbnail allowance before remote
I/O. After a failed upload, the exact pre-recorded upstream identity must be
confirmed deleted before any byte reservation is released. Under the quota and
row fences, Studio rechecks that no execution references it, confirms thumbnail
cleanup, and zeroes its byte charge. The owner-scoped row/count tombstone remains.
Uncertain upstream deletion or local cleanup keeps the existing charge.

At startup and during the existing hourly maintenance pass, Studio examines at
most ten older revoked upload reservations in at most thirty seconds. It skips
retained previews and any historical or active execution reference, positively
reconfirms upstream deletion outside the transaction, then rechecks mapping and
references under lock before releasing bytes. Live inputs, ambiguous creates,
generated Assets and their ownership/history are not reclaimed by this repair.
Ordinary expiry/pruning and count limits remain in force.

## Capacity defaults and existing deployments

Studio 1.2.1 raises the default reference reservation budget from 32 MiB to
2 GiB per user, and from 128 MiB to 8 GiB across Studio. Row defaults increase
from 128 to 1,024 per user and from 1,024 to 4,096 globally, allowing the larger
byte budget to be useful with multi-megabyte images. The first reached byte or
row bound still rejects admission. Failed-upload tombstones still count as rows.

These budgets cover uploaded original bytes plus thumbnail allowances and
snapshot thumbnail reservations; they do not preallocate memory/disk or set
generated-result storage limits. Controller owns the actual reference originals
and retains its independent capacity limits. An increased Studio budget does
not change GPU/VRAM use or certify Controller capacity.

The existing `STUDIO_INPUT_USER_BYTES`, `STUDIO_INPUT_GLOBAL_BYTES`,
`STUDIO_INPUT_USER_ROWS` and `STUDIO_INPUT_GLOBAL_ROWS` variables remain
operator overrides. Existing explicit values are preserved during update; to
adopt the new defaults, remove those overrides or set the values in
`deploy/studio.env.example` and recreate the service through the deployment's
configuration workflow. Restarting an existing Docker container alone does not
reload its environment. Byte settings accept 1 through 8 GiB inclusive; row
settings accept 1 through 4,096. Zero does not mean unlimited. No database
migration is needed, and existing records are not deleted by this change.

Source tests and hosted PostgreSQL checks are distinct from operator browser
acceptance. This change does not alter ComfyUI, GPU runtime settings, image/video
recipes, nginx configuration, or the separate Capture/MCP connection lifecycle.
