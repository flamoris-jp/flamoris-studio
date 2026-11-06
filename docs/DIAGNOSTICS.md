# Operational diagnostics and Logs

Every Studio HTTP response includes a server-generated `X-Request-ID`; caller IDs
are ignored. Failed requests, handled schema/HTTP rejections and Generation,
Agent and Intelligence upstream failures produce structured JSON records. They
include time, route template, method, component, operation, phase, normalized
reason, elapsed time and exception class/code locations. Upstream status is
recorded before public error normalization; absent status means no response was
observed. For example, a Generation HTTP 411 may become a Studio 502, with both
records sharing the ID. This example is not attribution of a production failure.

Creative editors keep their existing messages and show an error reference with
**View logs**. **Logs** is also beside Account settings. Filter by reference,
severity and service, inspect exception locations, or select **Related events**.
Pages are bounded to 100 events and an exclusive older-event cursor. Refresh is
explicit; the viewer does not poll or replay generation. Times use the browser's
local zone. Logs from previous versions cannot be reconstructed.

Browser network/script/unhandled-rejection reports send only a fixed failure
kind, never messages, stacks, page URLs or form contents. An HTTP failure lacking
a valid Studio response ID can also report its observed HTTP status separately,
so a proxy rejection is distinguishable from a Studio/upstream response. These use a separate
report request ID and are limited to ten reports per account per minute per
worker. If Studio cannot be reached, there is no server log/reference for that
report. The creative operation is never automatically retried.

## Access and privacy

Authenticated accounts see only records attributed to that verified Studio user.
A known request ID grants no access. Ownerless events, including authentication
and CSRF rejection before account validation, require operator access.
Set `STUDIO_DIAGNOSTICS_OPERATORS` to a JSON array of existing Studio user UUIDs
in operator configuration to enable **All accounts** for those users. The default
is `[]`; malformed configuration grants no operator access. Authorization is
rechecked after reads. This is read-only diagnostics access, not access to
another user's assets, inputs or conversations. There is no browser self-promotion,
arbitrary filesystem reader or raw container/journal endpoint. Responses are
private/no-store.

Diagnostics exclude prompts, media, cookies, credentials, raw upstream bodies,
raw exception messages/locals, query strings, concrete IDs in paths, provider URLs
and absolute filesystem paths. Code locations contain source filename, line and
function only. Framework route templates replace concrete request paths. Existing
provider privacy filters remain active. Proxy/service logs are separate sources.

## Storage and operation

A SQLite event store is separate from PostgreSQL business data; no database
migration is required. Default: `diagnostics/events.sqlite3` beneath
`STUDIO_THUMBNAIL_DIR`, using the existing Compose/Updater persistent mount.
Thumbnail cleanup deletes owned `.webp` files and leaves this subdirectory alone.
Override with `STUDIO_DIAGNOSTICS_DIR` pointing to an operator-provisioned writable
persistent directory. If neither directory is configured, process logs still work
and the viewer reports storage unavailable.

The store retains at most 5,000 events with a seven-day window. Writes prune
expired/excess records; reads prune expired records. SQLite reuses freed pages
and its file may not shrink. New files use mode 0600 and directories mode 0700;
protect the parent mount and backups. SQLite locks coordinate workers and use a
200 ms busy timeout. This diagnostic store is never a job/execution authority.

Structured JSON also goes to the `flamoris.studio` process logger independently
of viewer storage. Operators can collect bounded `docker logs --since ...
--tail ...` output and search the reference. Disk/locking failures emit a fixed
storage-unavailable notice without changing the creative request outcome. Failed
log reads return 503. Existing admission, authentication, ownership, unknown and
no-replay protections remain unchanged. Source/CI acceptance is separate from
rollout, reverse-proxy headers and actual-provider acceptance.
