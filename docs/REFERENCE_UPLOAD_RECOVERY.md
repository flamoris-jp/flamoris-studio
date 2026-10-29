# Reference-upload failure recovery (Studio #99)

Image and Video share the same private reference picker. Uploads remain limited to
one PNG/JPEG/WebP of at most 8 MiB, with existing user/global count and byte quotas.
The editor keeps the draft and any previous attachment when a replacement fails.
It does not automatically repeat an upload or generation request.

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
Ordinary expiry/pruning and count limits remain in force. No quota is increased.

Source tests and hosted PostgreSQL checks are distinct from operator browser
acceptance. This change does not alter ComfyUI, GPU runtime settings, image/video
recipes, nginx configuration, or the separate Capture/MCP connection lifecycle.
