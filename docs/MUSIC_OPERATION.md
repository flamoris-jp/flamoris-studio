# Native Music operation

The opt-in Music editor uses Generation's native `music-generate` and
`music-transcribe` templates. Studio never calls providers directly or switches
GPU runtimes. Generation owns execution, input snapshots and output binaries;
Studio retains authenticated execution, request and Asset references.

## Enable the matched contracts

Apply migration `20261003_10`. It renames the existing `speech_requests` table to
`generation_requests` without deleting rows or changing its owner/UUID and
execution uniqueness constraints. Speech and Music share this durable request
fence; a UUID already used by another operation cannot be replayed as Music.
Downgrading migration 10 preserves records by renaming the table back. Keep the
migration and replay records while outcomes remain uncertain.

Configure the backend Generation endpoint, protected token and optional Hub
namespace as for Image, then independently set `STUDIO_MUSIC_ENABLED=true`.
The default is false; disabled discovery makes no upstream call. Both operations
must pass their own exact available-capability and native-descriptor checks.
A healthy service alone, an Image attestation, an unreviewed template or a stub
provider cannot enable either operation.

Generation requires schema 5, `yue2-single-track-v1`, source revision
`decfe04c2ae2f8c73855832a56ddda0fce849407`, one track, full composition planning,
48 kHz stereo WAV16 output and `configured-http-contract` qualification.
Transcription requires schema 6, `sheetsage2-python-cpu-v1`, entrypoint SHA-256
`20e6b23c910bfdecf012a8ee8d45efcb31cb6cb21351da5921d95fb877e70de6`, CPU/FP32/offline
configuration and `configured-local-resources` qualification. Both descriptors
explicitly have `readiness.status=not-attested`. These configuration contracts do
not certify a live model run, output quality or current GPU availability.
Transcription additionally requires ready managed-input infrastructure.

The existing Hub tools cover these slices: `system.health`, `capabilities.list`,
`workflows.list/build`, `jobs.submit/status/cancel`, `inputs.create/get/delete`
and `assets.list/prepare/read/delete`. No Studio-only provider API or raw graph
is exposed to the browser. Signed external import remains separately configured
and explicitly requested; Music does not bypass its identity grants.

## Create music

The editor accepts style (1–1024 Unicode characters / 4096 UTF-8 bytes), optional
lyrics (at most 4096 characters / 16384 bytes), duration 1–120 seconds, sampling
steps 1–64 and independent audio/composition integer seeds between zero and
`2**53-1`. Each blank seed resolves once and is saved before submission. The
reviewed profile produces one WAV recording, one ABC score and replay metadata.
User-supplied ABC plans, audio references, alternative providers and model/runtime
controls are outside this profile.

## Transcribe audio

The editor selects an existing owned WAV from Generated Assets and explicitly
creates a managed input snapshot. Uploading local files or specifying local paths
is unavailable. The snapshot stays on Generation; Studio stores its opaque owned
handle and metadata, without copying the WAV or making an image thumbnail.
Existing input TTL, row quotas, compensation and cleanup apply. Snapshot metadata
must match the source MIME type and exact allowed media pair. An Image workflow
cannot use this audio snapshot as an image reference.

Transcription accepts maximum audio length 1–120 seconds and `melody_only`.
Only the bounded initial segment is analyzed. Submission rechecks ownership,
MIME, expiry and immutable upstream metadata; a pending/unknown execution
protects its reference from deletion. Explicit detachment and later terminal
cleanup use the existing input routes.

Results require a primary MIDI plus event and summary JSON Assets. Optional ABC,
annotation JSON and the four fixed MIDI part indices are accepted with their
exact roles and MIME types. Missing ABC is presented as **MIDI transcription
succeeded, but an ABC score was unavailable**; `melody_only` requires an ABC
score. Other MIME types, duplicate roles or incomplete required manifests fail
closed. ABC/MIDI/JSON use safe attachment downloads, never an executable inline
renderer. WAV playback and downloads reuse bounded owner-checked transfer/ranges.

## Submission, recovery and acceptance

The authenticated CSRF-protected submit uses a browser UUID shared across Music
operations. Studio atomically saves the execution, normalized parameters, resolved
seeds and request digest before the one non-idempotent upstream submission.
Identical repeats return the original owned execution without another build or
submit; changed parameters or operations return 409. Unknown receipts retain the
owned `submission_unknown` reference. The browser persists only the UUID in
account-scoped session storage, blocks another submission, and recovers through
an authenticated read-only request lookup. Signing out clears that browser fence;
the database fence remains. Account changes suppress late responses and clear
Music drafts through editor remounting. Session/configuration changes after build
prevent submission. Runtime status, cancellation and result authority stay upstream.

Before enabling Music for users, deploy the matching Generation/Hub source and
configured provider resources, then run real generation and transcription. Decode
WAV/MIDI, inspect ABC/JSON, verify sizes/digests, listen to the output, exercise
owner A preview/download/range versus owner B denial, cancel an active operation,
and repeat restart/uncertain-receipt recovery. Merged source and normal CI do not
prove this acceptance. Disable `STUDIO_MUSIC_ENABLED` for rollback while preserving
request/input/catalog records. Source initialization or model downloads remain
operator concerns.
