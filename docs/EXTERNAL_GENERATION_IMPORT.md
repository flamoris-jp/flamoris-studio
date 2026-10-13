# External generation catalog import

Studio can explicitly import completed external Generation jobs into the signed-in
user's normal Assets gallery. Apply migration `20261003_08` and deploy matching
Hub authenticated external provenance and Generation job/asset provenance source.
The Generation endpoint and Bearer token remain backend-only.

## Identity and trust

The operator grants one existing Studio account an exact `{issuer, subject}` in
the private `STUDIO_EXTERNAL_BINDINGS` JSON array:

```json
[{"user_id":"00000000-0000-4000-8000-000000000001","issuer":"hub.production","subject":"client-001"}]
```

Replace the example account UUID with an existing account after verifying the
external credential holder. The grant is account-specific, one-to-one and bounded
to 128 rows; duplicate users/subjects or malformed grants disable import. Subject
and issuer are non-secret stable credential identities, each limited to 128 ASCII
letters/digits and `._:-`. A transient MCP session ID and a shared deployment
token do not identify a person. Do not assign an external person identity to a
Studio backend credential shared by several Studio accounts.

Hub authenticates the external credential and signs bounded internal provenance;
Generation verifies it before submission and retains immutable provenance on jobs
and assets. Studio accepts only that trusted Generation response. Browser headers,
caller owner fields, prompts, provider paths and upstream IDs alone never grant
ownership. Public tool arguments do not carry Studio identity.

This first slice uses operator provisioning, as the scoped Agent integration does.
Self-service credential linking/proof and a user-managed binding screen remain
follow-up work; entering a job ID does not create or change an identity grant.
To revoke future imports, remove the grant from private configuration and recreate
Studio. Already imported catalog ownership remains with its original Studio user.
Reassigning an external identity cannot transfer existing job/asset claims.

## Explicit import

When configured for the signed-in account, Assets shows **External generation job**
and **Import results**. Copy the completed Generation job ID from the external
client's receipt. The authenticated CSRF-protected
`POST /api/generation/external-import` accepts only `{jobId}`. Availability returns
only a Boolean and never exposes the private grant, endpoint or credential.

Studio checks completed `jobs.status` with the exact job ID and provenance,
lists at most 64 asset metadata rows, requires every asset's job ID/provenance to
match, validates the existing role/media metadata contract, then rechecks terminal
job provenance. No `jobs.result`, preview, provider materialization or binary read
is required to create gallery rows. Generation's validated archived status supports
retained completed metadata after a restart; missing/invalid archives fail closed.
Unmaterialized bytes can still be unavailable after their original provider mapping
is lost, without removing the imported gallery entry.

The browser session and configuration are checked again after remote metadata
reads. PostgreSQL globally unique job/asset claims and a short shared catalog
transaction serialize imports across workers and reject conflicts with ordinary
Studio owners. Metadata reads allow at most two concurrent imports per process
and a 100-second overall deadline. Contention returns 429 for explicit retry.
No non-idempotent generation submission is replayed.

Imports are idempotent and restart-safe. A successful import returns only Studio
execution/asset handles and safe catalog metadata. It stores no external prompt,
provider path, credential or Agent transcript. Imported filenames use the normal
persisted Studio naming scheme. The existing owner checks govern detail, preview,
download, range, input snapshots and deletion. Imported outputs show **External**;
they have no fabricated Image settings to apply.

Completed imported output sets are frozen at catalog registration. Ordinary result
refresh cannot add a later unverified upstream row. Deletion remains a tombstone,
and durable ownership claims prevent reimport or reassignment of deleted outputs.

## Acceptance

CI covers linked/unlinked accounts, absent/foreign/partial/changed provenance,
job/asset mismatch, CSRF, owner spoofing, session/config revocation during reads,
cross-user downloads, immutable snapshots, concurrent imports, ordinary owner
conflicts and restart durability. Non-image rendering uses the existing bounded
media catalog; this import does not assert that new generation providers/editors
are ready. Real external credential → Hub → Generation → Studio import and two-user
download/deletion remain deployment acceptance checks.


## Automatic registration and gallery filters (1.0.6)

With a matching Generation MCP 1.0.5 / Controller 1.0.2 and the operator binding above, Assets discovers completed results automatically while the gallery is visible. It polls every 15 seconds (1 second between nonfinal bounded pages), stops polling when unmounted, pauses when hidden and keeps saved Assets available if synchronization fails. Synchronization has a 100-second backend deadline, an 8-job page limit, two sync slots and two shared import slots and a 110-second browser deadline. It never submits generation or downloads media.

The authenticated CSRF-protected `POST /api/generation/external-import/sync` accepts only an optional numeric cursor. Studio supplies the exact actor privately, validates the bounded response, and reuses the existing import checks for completed status, every output's provenance, current session/configuration and durable ownership claims. Browser responses expose only import count/cursor, never upstream Job IDs or private actor/configuration fields. Repeated visits/imports are idempotent; deleted claims do not resurrect Assets.

Discovery retains at most 1,024 recent completed external identities across restart. Jobs predating that index or outside the window remain explicitly importable under **Import older results**. Shared anonymous credentials are still not identities; historical anonymous jobs are not retroactively assigned. Refresh the gallery after an operator changes the binding. Account data already imported remains owned even if the external grant is subsequently removed.

Hub keeps credential identity and routing only; Controller owns the discovery receipt and generation authority; Studio owns the account catalog. No Hub-to-Studio callback, browser actor field, second JobStore, new external tool, or media folder migration is introduced.
