# Multimodal result catalog and bounded retrieval

Owning issue: [#39](https://github.com/flamoris-jp/flamoris-studio/issues/39).
This delivery extends result presentation/retrieval over existing Generation
executions. It does not enable new Music/Speech/Video generation operations,
unqualified Workflow profiles or shared-user Agent assistance.

## Additive catalog contract

Current Asset DTO adds source=generation, previewKind (image/audio/video/file), and
optional outputRole={port,role,index}. Execution observations add their existing
source/category/operation fields. Upstream IDs, file paths, provider URLs and
receipt internals stay server-side. Ownership is checked before every lookup,
status/result/cancel/preview/download/delete operation. Unsupported execution
sources cannot accidentally dispatch through Generation's job/asset gateway;
raw Intelligence still has no fabricated upstream polling job.

Adapter-assigned port/role/index are copied only after whole-manifest validation:
all three fields, safe bounded identifiers, index 0–127, no duplicate role/index or
conflicting port/role association. Filename/order never creates a role. Legacy
outputs remain unclassified. The mapping persists in existing Asset JSONB metadata
under output_role, so no SQL migration/backfill or duplicate catalog is needed.
Legacy rows read null; old clients ignore additive fields. Existing mappings/MIME
cannot change during result synchronization. Deletion tombstones retain mapping
and cannot be resurrected by a stale upstream listing.

Metadata is synchronized before any binary/thumbnail operation. WAV/MP3, MP4,
MIDI, JSON and PSD remain cataloged using current Generation media/MIME contracts.
Unknown/mismatched but safely formatted MIME metadata is retained as an unknown
file with attachment-only retrieval; it never earns a renderer from its filename.
MIME/header injection and malformed/duplicate manifests fail closed before commit.
Size may remain unknown; previews/transfer failure do not erase catalog rows.

PNG/JPEG/WebP keep the established Image thumbnail/preview/Use settings behavior.
Audio/video get controlled native players with preload=metadata and no autoplay,
only for exact supported kind/MIME pairs. Failure offers explicit reload/download
without deleting the entry. Gallery tiles do not eagerly fetch audio/video. MIDI,
JSON, PSD, HTML/SVG and other files show metadata/download only; no notation/code,
embedded browser document, MIDI synthesizer, PSD layer editor or JSON execution is
introduced. Text labels are React-escaped. Image reference pickers retain exact
native Image MIME/kind filtering. LoRA/Styles/preferences/reference behavior stays.

## Immutable representation retrieval

Non-native-image content/download always uses Generation assets.prepare/read;
there is no whole-file base64 fallback. The prepared authenticated receipt binds
asset ID, exact MIME, positive bounded size, whole-object SHA-256 and chunk ceiling.
Each cursor-free read pins that digest and validates offset/size/next_offset/eof,
actual decoded byte count and chunk_sha256. Requests stay behind Studio ownership
and login checks; expiry/deletion/source/MIME/upstream-ID changes refuse further
publication. Authentication/ownership are rechecked before and after each remote
read and before each emitted segment, including retries. No generation replay.

Per process there are two shared transfer slots and one existing prepare slot.
Actual total transfer is capped by STUDIO_MAX_TRANSFER_BYTES (hard ceiling 1 GiB).
New non-image transfers have a 300-second whole-operation deadline, including
preparation/reads/retries. Only transport-unavailable cursor-free reads retry at
the same offset (maximum 3 attempts); corrupt/auth failures do not retry. Failure,
cancellation, timeout and even header-send failure before streaming release the
slot. Existing Image transfer/preview contracts keep their established limits.

All responses use private/no-store, nosniff and restrictive sandbox CSP. Unknown,
MIDI/JSON/PSD/other files force safe attachment disposition even on /content;
/download always forces attachment. No provider path is a local storage locator.

## Single audio/video HTTP Range

Existing owner-scoped /content and /download routes accept one bytes range for
native audio/video: closed, open-ended or suffix forms. Unsatisfiable, malformed,
multi-range or repeated Range headers yield 416 with Content-Range: bytes */size,
after authorization. Valid ranges yield 206 with exact Content-Range/Length and
Accept-Ranges: bytes. Out-of-bounds ends clamp to representation size. No multipart
range responses. Non-player files keep bounded attachment/full retrieval.

ETag is the strong quoted sha256-<prepared digest> representation identity. Matching
If-Range honors the range; mismatched/weak/date/ambiguous validators fall back to
bounded full 200 under a new prepared receipt. Changes cannot mix representations.
The first verified chunk is obtained before response publication; subsequent
verification/authorization failure terminates the stream without claiming success.

An isolated range verifies each returned chunk and relies on Generation's trusted
prepared whole-object/file-identity receipt. It **does not independently rehash the
whole object from partial bytes**. A full-object transfer additionally verifies its
whole SHA-256 before emitting the final segment. This distinction is deliberate;
a chunk hash is not a proof against the whole-file hash without that trusted receipt.

## Acceptance and remaining work

Tests cover manifests/legacy roles, unsupported formats without guessed renderers,
actual PostgreSQL two-user ownership/source gates, durable role conflict/tombstone
behavior, metadata independent of previews, controlled players/file fallback,
single/invalid ranges/If-Range, no base64 fallback, chunk/full integrity distinctions,
login revocation during first read, bounded deadline and slot cleanup. Normal tests
use small fixture bytes and fake Generation gateways; they do not qualify codecs,
media quality or live providers. CI still runs existing Image regressions, frontend
build/tests, real PostgreSQL migrations/tests and packaged container checks.

Remaining #39: ready profile-driven dedicated generation editors/DTOs and managed
audio/video inputs, bounded structured/score/PSD presentation where specified,
Studio-account Agent mapping/context UI and session retention, proposal Apply,
Runtime/composition event integration and actual deployed media/two-user acceptance.
Provider contracts/exported graphs and exact catalog readiness are owning-service
gates, not permission to guess them from these result renderers. No deployment or
GPU/runtime activation is performed by this change.
