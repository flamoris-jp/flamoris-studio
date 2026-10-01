# Studio Workflow selection and managed Image inputs

Status: proposed design-only implementation plan, 2026-10-01. Tracks #21/#30/#36. This
design and its existing-Asset slice do not close #30.

The canonical Generation schema/semantic contract is
[WORKFLOW_SYSTEM_DESIGN.md](https://github.com/flamoris-jp/flamoris-generation-mcp/pull/43).
Keep this companion focused on Studio; do not create another workflow authority.

## 1. Baseline and scope

Reviewed Studio main: e89e35e380627c9d4b14d0f59db5acf3cae47745. Generation main:
cd5d6011bc0e35c274e8a9d7c1780cdd42100b53. Hub main:
fcbe0243c2e54d77e7ea44dc073c883e90d38cb4.

Current gateway discovers capability template IDs and models but not workflows.list
descriptors. The submit route chooses the builtin template based on whether LoRAs are
present. ImageRequest always includes fixed basic fields, so forwarding it wholesale to
a registered definition would send undeclared parameters.

Already implemented: Styles CRUD and ownership, per-user width/height/steps/CFG
preferences, automatic concrete seed snapshots, ordered optional LoRAs,
sampler/scheduler/denoise, result settings/Use settings, and Assets details. Existing
tests cover these paths. Preserve them; do not rebuild them.

Missing: descriptor selection, supported-field validation, input ownership
records/endpoints, reference picker/preview, version-aware snapshots/restoration. The
static Reference Image notice is unconditional; adding a graph alone cannot enable it.

This scope uses existing generated assets belonging to the signed-in Studio user. File
chooser/drag-and-drop upload of local files is not supported by Generation inputs.create
and is a separate future feature. Do not show an upload button that cannot submit
safely. Reference Image v1 is exactly: Studio-owned existing generated Asset -> managed
input snapshot -> JANKU img2img. No PC local file picker or drag/drop upload is
implemented in this slice. #30 retains its broader "choose or drag/drop an image"
requirement; local upload -> authorized managed input is follow-up scope, and #30 must
stay open even when this slice passes offline and live acceptance.

External ChatGPT-created asset import remains Hub #25, Generation #41, Studio #35. No
filesystem scan, global generation catalog import, or inferred ownership is included
here.

## 2. Discovery and dedicated Image UI

Gateway reads health/capabilities/models plus workflows.list, validates bounded
descriptors, and returns normalized Studio DTOs. Never return graph/node/input binding,
provider filenames, built/saved upstream IDs, or private diagnostics.

Add workflows to the existing discovery response while preserving templates, checkpoints
and loras for compatibility. A Workflow option includes ID, version, kind, name,
description, image mode/resize semantics, public parameter rules, Generation-computed
readiness and availability plus a safe unavailable reason. For definitions include
definition_version/canonical definition_digest and bounded safe verification metadata,
never the private attestation evidence. Provider identity may remain diagnostic; it does
not choose the primary editor.

Stable availability reasons include metadata_upgrade_required,
unsupported_image_profile, unsupported_parameter, provider_unavailable,
managed_input_not_ready, workflow_not_ready, readiness_mismatch. A malformed entry must
not break compatible entries; a malformed entire envelope produces safe discovery
unavailable. Bound number of entries, parameters, strings, and metadata response size.
Reject conflicting IDs/duplicate roles and invalid constraints. Never render upstream
markup.

Definition readiness is registered -> static validation -> validated -> bounded
real-runtime verification -> ready. Successful workflows.register is durable, static and
restart-free; it does not enable production selection. Generation's separate
workflows.verify uses the ordinary JobStore reservation, staging and asset APIs, returns
a job_id, and automatically persists attestation after
compatibility/build/submit/completion/declared-output verification. Studio is not an
attestation authority and adds no Approve action or browser verify tool.
ChatGPT/Work/runtime-enabled CI/operations tools can register -> verify -> poll without
a per-definition human step. Definitions cannot self-declare ready.

Normal Studio selection requires readiness.state=ready for the exact descriptor
ID/version/canonical digest. Version OR digest changes invalidate the old attestation.
Registered/validated/failed candidates may appear disabled with safe diagnostics, but
cannot be selected, restored as usable, or submitted. Missing or mismatched readiness
fails closed; retry verification stays upstream. Never derive readiness from a workflow
ID, JANKU name, raw graph, registration success, or global managed-input flag. Builtins
retain the Generation-owned builtin_compatibility ready basis; it does not certify an
external Definition. No raw attestation/prompt/source/runtime evidence reaches the
browser.

V1 definitions lacking roles/Image metadata are listed but disabled for this editor
until a reviewed higher-version metadata upgrade. Do not infer features from the
workflow ID, JANKU name, parameter key or ComfyUI node.

Builtin descriptors retain no-LoRA and ordered-LoRA behavior. Existing requests with no
selected workflow retain the current builtin selection compatibility path. New UI
chooses a descriptor explicitly. LoRAs are shown only when that descriptor advertises
them. Preserve an unsupported LoRA draft on switching, but require the user to
clear/resolve it before submitting; never silently discard it or inject it into a
scalar-only definition.

Generation must validate each advertised Image role against its effective
declared-output path and the reviewed node/input semantics. An unused width,
seed or steps binding, or a binding to the wrong input, must not become an
editable advertised role merely because its type/range is valid. Non-default
dimension/seed values must affect the active resize/latent/sampler inputs;
effective smoke budgets also include fixed graph literals and batch/output
count. Studio consumes this validated graph-free contract and never duplicates
provider graph validation.

Known parameter roles drive the existing dedicated controls. Additional supported
scalar/enum parameters may use a bounded Advanced section, not a universal schema form.
Unknown required types/profiles make the option unavailable. Supported required values
must be collected; optional defaults come from metadata. Only declared fields are
submitted.

Preserve prompt and unrelated drafts on workflow/model change. Validate against the
selected descriptor before Generate. Do not silently clamp a preference outside that
workflow's range; show a validation message. Fixed dimensions are displayed as fixed,
with no editable width/height sent. Size presets only select values accepted by the
descriptor. Keep preferences separate from Styles and workflow defaults. Explicit
draft/Use settings values win over initial preferences; preferences initialize supported
fields only when valid, then use workflow defaults as fallback.

Reference control label: Initial image (img2img). Explain that it transforms the whole
image and uses center crop/resize to output dimensions. Denoise controls how much it
changes; it is not a promise to retain a character identity.

Enable the picker only when all hold:

1. Workflow declares the supported img2img/initial_image contract.
2. Required image media type and remaining parameters are supported.
3. Exact Workflow ID/version/digest has Generation-owned ready attestation.
4. Provider is available AND managed-input infrastructure readiness is on.
5. Studio has deployed its ownership mapping/API.

An existing input is not a picker prerequisite. With no attachment, or after
expiry/revocation/removal, keep selection and replacement available when those five
conditions hold. If no eligible owned Asset exists, show an empty picker with guidance
to generate one; do not substitute an unauthorized upstream Asset. Invalid scalar drafts
block Generate, not selection of a supported initial image.

Generate additionally requires an authorized, unexpired, non-revoked input and valid
remaining parameters. Recheck authorization/expiry on the server and during staging; a
usable picker does not grant permission to submit.

No input attachment for txt2img; do not send an old hidden reference when switching
modes. Preserve the local draft separately for switching back. Unknown/mismatched
Workflow or infrastructure readiness, or provider failure, blocks selection and
submission. Input expiry/revocation blocks submission while permitting authorized
reselection.

## 3. Ownership model and proposed API

Add Studio-owned managed_inputs with:

| Field | Meaning |
| --- | --- |
| id | Browser-facing opaque Studio UUID |
| owner_user_id | Authenticated Studio user; indexed |
| upstream_input_id | Server-only Generation reference; unique per input |
| source_asset_id | Studio source ID, nullable/SET NULL on deletion |
| source provenance | Ownership verified at create, bounded internal upstream source/digest/MIME/size |
| created_at / expires_at | Snapshot lifecycle from validated upstream metadata |
| revoked_at | Local access revocation/tombstone |
| thumbnail_locator | Studio-owned thumbnail independent of source Asset lifetime |

Use an Alembic migration after the current migration head. Never cascade-delete an
immutable snapshot merely because its source catalog entry is removed. The persisted
owner is its authorization authority after creation; a source digest/ID alone grants no
access.

Proposed endpoints under /api/generation/image/inputs:

- POST with {assetId: StudioAssetUUID}: create from an owned, available generated
  image. Verify Asset and Execution ownership locally before any upstream call.
- GET /{inputId}: owned metadata; validate upstream existence/expiry.
- DELETE /{inputId}: revoke/delete only owned input, preserving CSRF.
- GET /{inputId}/thumbnail: owned, bounded thumbnail; works after source deletion.

Responses contain only Studio input/source handles, MIME/size, expiry, display name and
the authorized thumbnail URL. Never expose the raw upstream input ID, source upstream
ID, digest as an authorization token, or runtime filename.

Picker uses the existing /api/assets collection, filtered to supported images, then
calls input create. One initial image at a time. Replace only swaps the draft attachment
after a successful create. Remove clears it; deleting a stored snapshot is an explicit
lifecycle action, not an accidental side effect of switching workflows. A snapshot
shared by an execution/settings record is not implicitly destroyed.

Generate thumbnails from the source bytes whose digest matches the created snapshot; use
the existing bounded image/thumbnail and transfer infrastructure. Persist a thumbnail
independent of the source so a valid immutable snapshot can still be shown after source
deletion. If preview retrieval fails, show safe missing-preview state without falsifying
input validity. Retain mapping and expiry checks; no new large binary store is required.

If snapshot creation succeeds but DB commit fails, attempt bounded best-effort
compensating deletion using the returned input ID; never grant access before the row is
durable. If input creation response is ambiguous, do not invisibly retry or invent an
ownership mapping; upstream TTL/pruning handles unknown orphans. Local quota and
timeout/busy handling must remain bounded.

Every get/delete/thumbnail/submit/restore resolves id AND owner_user_id before upstream
calls. Known raw Generation IDs are not accepted as substitutes. Cross-user and unknown
handles return the same 404 shape; no prompt/source leaks. Recheck upstream expiry/media
under normal submit validation and staging. Handle in-use deletion as a retryable
conflict, not successful deletion. Only mark the row revoked/deleted after the
appropriate durable local decision; if upstream deletion fails, retain enough state for
a safe owned retry. Generation remains responsible for file TTL/staging, not Studio's
database.

## 4. Submit DTO and execution snapshot

Keep the legacy ImageRequest path. Add an explicit request shape for selected workflows,
with workflowId, workflowKind, definitionVersion and definitionDigest (definitions
only), typed Image role values, bounded additional scalar parameters, and optional
referenceInputId (Studio UUID only).

Example conceptual request:

```json
{
  "workflowId": "janku-reference-image",
  "workflowKind": "definition",
  "definitionVersion": 1,
  "definitionDigest": "sha256:<canonical-definition-digest>",
  "positivePrompt": "a quiet garden",
  "negativePrompt": "",
  "checkpoint": "catalog-selected-checkpoint",
  "width": 512,
  "height": 512,
  "steps": 25,
  "cfg": 4,
  "seed": null,
  "sampler": "euler",
  "scheduler": "normal",
  "denoise": 0.5,
  "referenceInputId": "11111111-1111-4111-8111-111111111111",
  "additionalParameters": {}
}
```

The model name, digest and UUID above are placeholders, not runtime identities.
Client-supplied workflowKind/version/digest/profile are untrusted: verify them against
fresh server-side discovery. Never accept raw graph, node patches, model paths,
URLs, Generation input IDs, or managed_input values hidden in additionalParameters.

Ordering:

1. Validate local shape and authorize every submitted Studio reference before
   contacting the shared upstream, including discovery.
2. Read/validate the selected descriptor and exact version/digest ready
   attestation. For reference use require infrastructure readiness too; no browser
   flag can bypass either gate. Apply supported metadata
   constraints and validate required/mutually incompatible fields.
3. Resolve Studio input handle to the mapped upstream input and revalidate
   expiry/MIME. Map Image roles to declared parameter keys.
4. Assign a concrete cryptographic auto seed if that role is supported and blank,
   using the canonical descriptor-constrained seed domain below. Explicit values,
   including zero, are preserved and validated; never silently replace them.
   Revalidate all resolved values after automatic assignment, before build.
5. Build using explicit definition_version, definition_digest and server-set
   require_ready=true for definitions; the normalized recipe pins identity and
   ready requirement. Use builtin compatibility mapping for existing requests.
   Candidate verification's trusted require_ready=false path is never exposed
   by the Studio API; reject injected readiness policy flags.
6. Validate normalized recipe result. Persist Studio execution/request snapshot
   before the non-idempotent jobs.submit.
7. Submit once. Generation rechecks the pinned ready attestation under normal
   JobStore admission before provider await, including revocation/update races
   and current infrastructure readiness for production img2img.
   Preserve busy/submission_unknown behavior and no automatic retry.
8. Synchronize returned assets through existing owner-scoped catalog flow.

### Descriptor-constrained automatic seed

For a declared integer seed role, the legal domain is the intersection of
0..Number.MAX_SAFE_INTEGER with its minimum, maximum, typed enum, and multiple_of.
Absent bounds do not enlarge Studio's safe range. Apply fractional bounds with
ceil/floor; exclude booleans and coerced numeric strings. The canonical rules
are in Generation's Workflow design; the descriptor remains the authority.

Sample cryptographically from the legal values directly: use a bounded filtered
enum, or derive an exact integer progression and sample its index. Do not draw
from the full browser-safe range and retry until validation happens to pass.
V2 multiple_of is positive integer only on integer parameters. Generation and
Studio use the same integer remainder/progression semantics; width/height use 8.
Reject nonpositive/non-integer divisors, booleans and numeric strings in metadata;
never round an invalid seed into the domain. Decimal steps for number parameters
need a future separate contract without float tolerance. An empty/unsupported domain makes
the workflow unavailable with unsupported_parameter before build/submit.
The frontend Randomize seed action follows the same domain; the backend checks
the result independently. Preserve the legacy builtin seed behavior.

Revalidate generated/explicit seeds against the descriptor, then persist the
concrete normalized value before jobs.submit. A stale descriptor/version still
follows workflow_changed handling; seed resolution cannot authorize an upgrade.
If no seed role exists, omit it rather than injecting a seed parameter.

Snapshot version 2 retains selected workflow kind/ID/version/canonical digest,
normalized public
scalar parameters, resolved concrete seed, Image role settings, referenceInputId,
and safe thumbnail/expiry presentation context. The canonical Definition digest
is public contract identity; upstream input bindings and input-byte digests
may be stored only as internal provenance; redact them from browser
settings. Keep execution ownership and original snapshot authoritative for
Use settings, not provider filenames or reconstructed PNG metadata.

Expose updated settings and workflow summary through existing owner-scoped
asset details. Old snapshots have no v2 envelope: restore through the builtin
legacy path. Do not reinterpret an old snapshot as a newly registered workflow.

## 5. Restore and races

Use settings first checks Asset/Execution owner and selected workflow availability.
An img2img restore must also check input mapping owner, revocation and upstream
expiry. Source deletion alone does not invalidate a copied snapshot.

If the reference expired/revoked, restore compatible prompt/scalars with a
clear reselect-reference message and keep Generate blocked. If the selected
definition version or canonical digest was replaced, preserve the old settings visibly but require
explicit selection/revalidation of a current ready identity; never silently
upgrade. If attestation was revoked or reverify failed, retain an unavailable
draft and require renewed ready discovery before Generate.

A stale build/version failure maps to workflow_changed, refreshes discovery,
keeps the draft and requires a new Generate click. Upstream submission_unknown
must not present a retry action that automatically resubmits.

Definition/version/digest-update, attestation-revocation, input-delete/expiry and
source-delete races require tests.
Generation stage lease is the final file-use boundary; Studio cannot make an
input immortal through a database row.

## 6. File/commit plan and tests

| Commit | Existing layers to extend |
| --- | --- |
| 1 | gateway.py ready-only descriptor DTOs/normalization; api.ts discovery types |
| 2 | db.py, new Alembic migration, owned input helpers/endpoints |
| 3 | app.py exact ready identity/role mapping, require_ready policy and snapshot v2 |
| 4 | App.tsx dedicated Workflow selector and Asset reference picker |
| 5 | App.tsx/api.ts/Gallery.tsx settings/reference restoration |
| 6 | tests, README, architecture cross-link and operational notes |

Small workflow/input contract helpers may be extracted from app.py/gateway.py
if needed; do not refactor unrelated authentication or transfers.
No GPU control, identity binding, local uploads, or workflow authoring UI.

Backend tests: metadata normalization and no raw graph leakage, supported/
unsupported/legacy descriptors, arbitrary public key role mapping, exact defaults
and constraints, unknown parameters, raw upstream input rejection, source
ownership rejection with zero upstream calls, source-deleted snapshot reuse,
cross-user get/delete/thumbnail/submit/restore, expiry/revocation/in-use conflict,
DB failure/orphans, CSRF, stale versions, auto/zero seed snapshots, migrations/
restart, and existing txt2img/LoRA/Style/preferences/assets/download regressions.
Seed cases include a 32-bit maximum, nonzero minimum, typed enum, positive integer
multiple_of, invalid divisors, a singleton/empty safe domain, invalid explicit zero,
missing seed role, and normalized snapshot/Use settings round trips.
Readiness cases include register-only/validated exclusion, missing/mismatched
attestation, version OR digest replacement, failed/retried verify, both independent
readiness gates, revoke-between-build-and-submit, self-claimed readiness and
browser policy bypass rejection. Test no local file picker/drag-drop/upload
button, and document #30's unresolved local upload scope.

Include a descriptor normalization/submit regression preserving non-default
dimension/seed values under arbitrary public parameter keys. Upstream
unused/wrong-semantic bindings must be rejected by Generation, not repaired by
Studio. Mixed-schema discovery failure must preserve drafts and never replay
submission; verify legacy/new discovery after paired rollout/rollback.

Frontend tests must exercise actual components and mocked API calls for
workflow selection, metadata-dependent controls, picker attach/replace/remove,
initial selection with no existing input, reselection after expiry/revocation/
removal, empty eligible-Asset list, Generate blocked until valid attachment,
descriptor-constrained Randomize seed, preview failure/expiry, preserving prompt
and LoRA drafts, disabled unsupported
workflows, and Use settings. Existing helper-only tests are insufficient for
this integration. Keep no Batch Count until the upstream contract supports it.

Run current PostgreSQL-backed pytest, migration chain checks, npm ci,
npm run build, npm test, and the production Docker package/static-asset smoke
defined in CI. Ordinary CI needs no GPU, private model, tunnel or live ComfyUI.

## 7. Rollout/acceptance

Follow Generation's canonical coordinated schema rollout/rollback. Hub compares
configured input schemas exactly before every Generation call. Adding optional
build arguments breaks the old catalog and blocks unchanged health/status/asset
calls too; optional argument compatibility is not mixed-deployment compatibility.

Pause Studio Generate and all other submitters, drain/reconcile active provider
work while the old pair matches, then replace the Generation singleton and
update/restart the deployed Hub catalog during the same maintenance window.
Preserve deployment-specific endpoint/authentication settings. Verify full schema/
annotation parity and legacy/new calls on a fresh connection before deploying
this Studio backend/migration/UI and reopening ingress. Expect discovery failure
during the mixed-schema interval; do not present a retry that replays submission.

If rollback is needed, keep ingress paused and reconcile provider work. Return
Studio to a compatible code/DB state through documented procedures, then restore
the matching Generation code and Hub catalog pair plus compatible backed-up
persistence. Preserve newer definitions/data for recovery; older code must not
be started against unreadable v2 definitions, recipes, attestations or migrations.
Recheck ownership, catalog parity, singleton/provider state and readiness before
reopening. The one-time maintenance window does not add a release/restart step
for later Definition registrations or verification.

Deploy schema/signature/backend/UI support through current repository procedures;
do not guess service commands. Keep production reference controls off until
infrastructure smoke/retention policy AND per-definition automatic ready
attestation are evidenced. The global flag alone never enables a Definition.
Per-definition register -> verify -> poll -> ready remains restart/release-free
and needs no human Approve. Failed verification stays registered/validated,
unavailable in production and explicitly retryable through upstream tools.

Acceptance: choose an automatically attested exact ready Image definition; select an owned
generated image; create a snapshot; submit img2img; view the normal Studio
result/Asset; inspect exact settings; restore the still-valid reference;
reject another user's equivalent operations; prove source deletion and snapshot
expiry have distinct behavior. Offline acceptance and live acceptance must be
reported separately.

This design PR does not close #21/#30/#36, run product changes, or merge itself.
Completion of this existing-Asset Reference Image v1 slice must not close #30.
Its local upload -> authorized managed input requirement remains follow-up scope;
do not advertise or render an upload control before that contract is implemented.
