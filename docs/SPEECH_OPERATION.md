# Native Speech operation

Studio's optional Speech editor supports one no-reference Japanese recording
through Generation's native `speech-no-reference` template. It accepts text and
an optional voice description (each at most 512 Unicode characters and 2048
UTF-8 bytes), output length 0.5–30 seconds, integer steps 1–80 and a browser-safe
integer seed. Blank seed resolves once to a persisted random seed. Reference
audio, voice cloning, Music, Video and model/runtime controls are not part of
this slice.

## Enable the matched contract

Apply migration `20261003_09` after the existing migrations. Configure the
backend-only Generation endpoint/token and optional Hub namespace as for Image.
Set `STUDIO_SPEECH_ENABLED=true` only with the matching Generation native Speech
source and its explicitly configured local Irodori resources. The flag defaults
to false, and disabled discovery performs no upstream call.

Studio requires a healthy Generation service, an available `speech.generate`
capability from `irodori`, and exactly one schema-4 native descriptor with the
reviewed scalar bounds, no input roles and one `audio` output. The descriptor and
build proof must identify `irodori-no-reference-v1` and Irodori source revision
`89f9d8fbd4d51ea019867ee1197725ede1df13c5`, with `reference_audio=false` and
`candidates=1`. The descriptor explicitly reports `configured-local-resources`
and `readiness.status=not-attested`. Image v1/v2/v3 attestation is not reused.

The Hub must forward existing `system.health`, `capabilities.list`,
`workflows.list`, `workflows.build`, `jobs.submit/status/cancel`, `assets.list`,
`assets.prepare/read/get/delete` tools under the configured namespace. No new
Hub argument schema is needed. Credentials and provider-local paths remain on
the backend; results are synchronized from bounded Asset metadata, without
calling `jobs.result`.

## Submission and recovery

The authenticated CSRF-protected submit route accepts a browser-generated UUID.
Studio atomically stores an owned execution, normalized parameters, seed and
request digest before build or the single non-idempotent submit. A repeated UUID
with identical parameters returns the same execution without another build or
submit; different parameters return 409. Concurrent repeats share the same
durable record. UUID recovery is authenticated and owner-scoped.

Unknown submit outcomes return the persisted owned execution as
`submission_unknown`; they never trigger automatic resubmission. The browser
stores only the UUID in account-scoped session storage, retains it across Image
navigation and reload, and clears it on a known terminal outcome or explicit
logout. A lost HTTP receipt keeps another Speech submission disabled while
**Check previous request** performs a read-only lookup. A missing receipt record
does not prove that submitting again is safe. Do not delete request fences to
retry an unknown outcome; inspect the Generation authority first. The migration
downgrade refuses to erase nonempty request fences.

Session and configured-route changes are checked again after build and before
submit. Status, result and recovery callbacks are fenced when the account leaves
the page or logs out. Speech retains its own execution when another editor
submits a job. Completed results must contain exactly one `audio/wav` Asset with
the `audio` port/role/index-0 metadata before cataloging. Normal owner-only
preview, range download and deletion apply.

## Deployment acceptance

Source tests and configured availability do not attest model execution or audio
quality. Before enabling this route for users, perform a real recording with the
operator's pinned offline resources, decode the WAV, verify mono/sample-rate/
frame metadata and output digest, listen to the Japanese speech, and check
owner A preview/download/range access plus owner B denial. Verify restart
recovery and cancellation under the actual service. Keep Runtime/model expansion
and genuine Music/Video provider work deferred until their independent gates
are satisfied. Roll back by disabling `STUDIO_SPEECH_ENABLED`; keep durable
request records and the migration in place while uncertain outcomes exist.


## Resident Irodori HTTP transport (1.0.8)

The matched Controller 1.0.4 / Generation 1.0.7 can use an already running Irodori API instead of spawning the CLI. Configure the Generation host with `FLAMORIS_IRODORI_URL` (an exact private HTTP origin) and optional model/token settings, remove any conflicting CLI configuration, and set `STUDIO_SPEECH_ENABLED=true` in the existing private Studio environment. Updater preserves current environment files during update; enabling this feature is a separate explicit configuration change. No DB migration or new service is needed.

Studio accepts the exact `irodori-server-no-reference-v1` proof, server contract revision `61012c760f22f7b4a6c21c5c5f8f9e148120b6f9`, no references and one candidate. Qualification is `configured-http-contract`; readiness remains `not-attested`. The profile describes a reviewed API contract, not verified running source/model identities. Existing CLI profiles remain supported. A configured but stopped server makes Speech unavailable. Activate it through the existing runtime manager, then use Check Again.

The submitted execution records the selected profile; the build must return that same proof. UUID digest identity remains stable across transport configuration changes so a previously admitted request is recovered without replay. Owner preview/download/range controls remain unchanged. The HTTP API has no targeted cancel: Cancel requests keep observing the original recording, which may complete. Lost or interrupted outcomes stay unknown/reserved. See [Controller HTTP transport](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/IRODORI_HTTP.md) for configuration, uncertainty and live acceptance.


## Reference audio (Controller #21)

The matched Controller/Generation packages also expose the separate
`speech-reference` schema-9 profile, `irodori-server-reference-v1`.
When discovery confirms the exact descriptor and a reference-capable loaded model,
Speech offers one optional WAV upload and a local audio preview. Remove reference
returns to ordinary no-reference generation. A failed upload blocks generation
until it is retried or explicitly removed.

Use one PCM16 mono/stereo WAV at 16/22.05/24/44.1/48/96 kHz, at most 30 seconds
and 8 MiB. The authenticated raw-upload endpoint forwards bytes through the
existing Controller input-upload contract. Studio stores only the owner-scoped
opaque mapping and metadata; WAV originals remain generation-owned and have no
image thumbnail. The browser previews its selected local file via a temporary
object URL and revokes that URL when replaced or unmounted.

Reference submission verifies owner, state, expiry and upstream digest before
resolving the input. The execution protects the selected input and records its
opaque handle, checksum and exact profile. Replays keep the original request
fence; another account cannot select or inspect this input. The underlying
reference input ID is never accepted directly from a browser. Reference failure
never silently invokes no-reference synthesis.

The resident HTTP adapter uploads a private temporary voice rather than sending
a browser/server-local path. Existing CLI no-reference generation stays supported.
Current reference support is limited to the reviewed v4-Small/v4.1-Small model
identities. Unknown server work and temporary-voice cleanup follow the
[Controller reference contract](https://github.com/flamoris-jp/flamoris-generation-controller/blob/main/docs/REFERENCE_SPEECH.md).
Source/CI checks do not attest perceptual similarity or a deployed runtime.
