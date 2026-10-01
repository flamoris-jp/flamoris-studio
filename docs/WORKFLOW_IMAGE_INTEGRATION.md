# Studio Workflow selection and managed Image inputs

Status: proposed design-only implementation plan, 2026-10-01.
Tracks #21/#30 plus the Workflow discovery child issue created for this plan.

The canonical Generation schema/semantic contract is
[WORKFLOW_SYSTEM_DESIGN.md](https://github.com/flamoris-jp/flamoris-generation-mcp/pull/43).
Keep this companion focused on Studio; do not create another workflow authority.

## 1. Baseline and scope

Reviewed Studio main: e89e35e380627c9d4b14d0f59db5acf3cae47745.
Generation main: cd5d6011bc0e35c274e8a9d7c1780cdd42100b53.
Hub main: fcbe0243c2e54d77e7ea44dc073c883e90d38cb4.

Current gateway discovers capability template IDs and models but not
workflows.list descriptors. The submit route chooses the builtin template
based on whether LoRAs are present. ImageRequest always includes fixed basic
fields, so forwarding it wholesale to a registered definition would send
undeclared parameters.

Already implemented: Styles CRUD and ownership, per-user width/height/steps/CFG
preferences, automatic concrete seed snapshots, ordered optional LoRAs,
sampler/scheduler/denoise, result settings/Use settings, and Assets details.
Existing tests cover these paths. Preserve them; do not rebuild them.

Missing: descriptor selection, supported-field validation, input ownership
records/endpoints, reference picker/preview, version-aware snapshots/restoration.
The static Reference Image notice is unconditional; adding a graph alone
cannot enable it.

This scope uses existing generated assets belonging to the signed-in Studio
user. File chooser/drag-and-drop upload of local files is not supported by
Generation inputs.create and is a separate future feature. Do not show an
upload button that cannot submit safely.

External ChatGPT-created asset import remains Hub #25, Generation #41, Studio
#35. No filesystem scan, global generation catalog import, or inferred ownership
is included here.

## 2. Discovery and dedicated Image UI

Gateway reads health/capabilities/models plus workflows.list, validates bounded
descriptors, and returns normalized Studio DTOs. Never return graph/node/input
binding, provider filenames, built/saved upstream IDs, or private diagnostics.

Add workflows to the existing discovery response while preserving templates,
checkpoints and loras for compatibility. A Workflow option includes ID, version,
kind, name, description, image mode/resize semantics, public parameter rules,
and availability plus a safe unavailable reason. Provider identity may remain
diagnostic; it does not choose the primary editor.

Stable availability reasons include metadata_upgrade_required,
unsupported_image_profile, unsupported_parameter, provider_unavailable,
managed_input_not_ready. A malformed entry must not break compatible entries;
a malformed entire envelope produces safe discovery unavailable. Bound number
of entries, parameters, strings, and metadata response size. Reject conflicting
IDs/duplicate roles and invalid constraints. Never render upstream markup.

V1 definitions lacking roles/Image metadata are listed but disabled for this
editor until a reviewed higher-version metadata upgrade. Do not infer features
from the workflow ID, JANKU name, parameter key or ComfyUI node.

Builtin descriptors retain no-LoRA and ordered-LoRA behavior. Existing requests
with no selected workflow retain the current builtin selection compatibility
path. New UI chooses a descriptor explicitly. LoRAs are shown only when that
descriptor advertises them. Preserve an unsupported LoRA draft on switching,
but require the user to clear/resolve it before submitting; never silently
discard it or inject it into a scalar-only definition.

Known parameter roles drive the existing dedicated controls. Additional supported
scalar/enum parameters may use a bounded Advanced section, not a universal
schema form. Unknown required types/profiles make the option unavailable.
Supported required values must be collected; optional defaults come from
metadata. Only declared fields are submitted.

Preserve prompt and unrelated drafts on workflow/model change. Validate against
the selected descriptor before Generate. Do not silently clamp a preference
outside that workflow's range; show a validation message. Fixed dimensions are
displayed as fixed, with no editable width/height sent. Size presets only select
values accepted by the descriptor. Keep preferences separate from Styles and
workflow defaults. Explicit draft/Use settings values win over initial
preferences; preferences initialize supported fields only when valid, then use
workflow defaults as fallback.

Reference control label: Initial image (img2img). Explain that it transforms the
whole image and uses center crop/resize to output dimensions. Denoise controls
how much it changes; it is not a promise to retain a character identity.

Enable the picker only when all hold:

1. Workflow declares the supported img2img/initial_image contract.
2. Required image media type and remaining parameters are supported.
3. Provider is available and managed-input rollout readiness is on.
4. Studio has deployed its ownership mapping/API.
5. An authorized, unexpired input is available for Generate.

No input attachment for txt2img; do not send an old hidden reference when
switching modes. Preserve the local draft separately for switching back.
Unknown readiness, input expiry or upstream failure must fail closed.

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

Use an Alembic migration after the current migration head. Never cascade-delete
an immutable snapshot merely because its source catalog entry is removed.
The persisted owner is its authorization authority after creation; a source
digest/ID alone grants no access.

Proposed endpoints under /api/generation/image/inputs:

- POST with {assetId: StudioAssetUUID}: create from an owned, available generated
  image. Verify Asset and Execution ownership locally before any upstream call.
- GET /{inputId}: owned metadata; validate upstream existence/expiry.
- DELETE /{inputId}: revoke/delete only owned input, preserving CSRF.
- GET /{inputId}/thumbnail: owned, bounded thumbnail; works after source deletion.

Responses contain only Studio input/source handles, MIME/size, expiry, display
name and the authorized thumbnail URL. Never expose the raw upstream input
ID, source upstream ID, digest as an authorization token, or runtime filename.

Picker uses the existing /api/assets collection, filtered to supported images,
then calls input create. One initial image at a time. Replace only swaps the
draft attachment after a successful create. Remove clears it; deleting a stored
snapshot is an explicit lifecycle action, not an accidental side effect of
switching workflows. A snapshot shared by an execution/settings record is not
implicitly destroyed.

Generate thumbnails from the source bytes whose digest matches the created
snapshot; use the existing bounded image/thumbnail and transfer infrastructure.
Persist a thumbnail independent of the source so a valid immutable snapshot can
still be shown after source deletion. If preview retrieval fails, show safe
missing-preview state without falsifying input validity. Retain mapping and
expiry checks; no new large binary store is required.

If snapshot creation succeeds but DB commit fails, attempt bounded best-effort
compensating deletion using the returned input ID; never grant access before
the row is durable. If input creation response is ambiguous, do not invisibly
retry or invent an ownership mapping; upstream TTL/pruning handles unknown
orphans. Local quota and timeout/busy handling must remain bounded.

Every get/delete/thumbnail/submit/restore resolves id AND owner_user_id before
upstream calls. Known raw Generation IDs are not accepted as substitutes.
Cross-user and unknown handles return the same 404 shape; no prompt/source
leaks. Recheck upstream expiry/media under normal submit validation and staging.
Handle in-use deletion as a retryable conflict, not successful deletion.
Only mark the row revoked/deleted after the appropriate durable local decision;
if upstream deletion fails, retain enough state for a safe owned retry.
Generation remains responsible for file TTL/staging, not Studio's database.

## 4. Submit DTO and execution snapshot

Keep the legacy ImageRequest path. Add an explicit request shape for selected
workflows, with workflowId, workflowKind, definitionVersion (definitions only),
typed Image role values, bounded additional scalar parameters, and optional
referenceInputId (Studio UUID only).

Example conceptual request:

```json
{
  "workflowId": "janku-reference-image",
  "workflowKind": "definition",
  "definitionVersion": 1,
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

The model name and UUID above are placeholders, not runtime IDs.
Client-supplied workflowKind/version/profile are untrusted: verify them against
fresh server-side discovery. Never accept raw graph, node patches, model paths,
URLs, Generation input IDs, or managed_input values hidden in additionalParameters.

Ordering:

1. Validate local shape and authorize every submitted Studio reference before
   contacting the shared upstream, including discovery.
2. Read/validate the selected descriptor and version. Apply supported metadata
   constraints and validate required/mutually incompatible fields.
3. Resolve Studio input handle to the mapped upstream input and revalidate
   expiry/MIME. Map Image roles to declared parameter keys.
4. Assign concrete cryptographic auto seed if that role is supported and blank;
   explicit zero remains zero. Restrict to browser-safe integer range.
5. Build using explicit definition_version for definitions; use builtin
   compatibility mapping for existing requests.
6. Validate normalized recipe result. Persist Studio execution/request snapshot
   before the non-idempotent jobs.submit.
7. Submit once. Preserve busy/submission_unknown behavior and no automatic retry.
8. Synchronize returned assets through existing owner-scoped catalog flow.

Snapshot version 2 retains selected workflow kind/ID/version, normalized public
scalar parameters, resolved concrete seed, Image role settings, referenceInputId,
and safe thumbnail/expiry presentation context. Upstream input bindings and
digests may be stored only as internal provenance; redact them from browser
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
definition version was replaced, preserve the old settings visibly but require
explicit selection/revalidation of the current version; never silently upgrade.

A stale build/version failure maps to workflow_changed, refreshes discovery,
keeps the draft and requires a new Generate click. Upstream submission_unknown
must not present a retry action that automatically resubmits.

Definition-update, input-delete/expiry and source-delete races require tests.
Generation stage lease is the final file-use boundary; Studio cannot make an
input immortal through a database row.

## 6. File/commit plan and tests

| Commit | Existing layers to extend |
| --- | --- |
| 1 | gateway.py descriptor DTOs/normalization; api.ts discovery types |
| 2 | db.py, new Alembic migration, owned input helpers/endpoints |
| 3 | app.py selection/version/role mapping and snapshot v2 |
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

Frontend tests must exercise actual components and mocked API calls for
workflow selection, metadata-dependent controls, picker attach/replace/remove,
preview failure/expiry, preserving prompt and LoRA drafts, disabled unsupported
workflows, and Use settings. Existing helper-only tests are insufficient for
this integration. Keep no Batch Count until the upstream contract supports it.

Run current PostgreSQL-backed pytest, migration chain checks, npm ci,
npm run build, npm test, and the production Docker package/static-asset smoke
defined in CI. Ordinary CI needs no GPU, private model, tunnel or live ComfyUI.

## 7. Rollout/acceptance

Deploy schema/signature/backend/UI support through current repository procedures;
do not guess service commands. Keep production reference controls off until
the explicit real-runtime smoke and provider-input retention policy are evidenced.
Per-definition trusted runtime registration remains restart/release-free.

Acceptance: choose a reviewed registered Image definition; select an owned
generated image; create a snapshot; submit img2img; view the normal Studio
result/Asset; inspect exact settings; restore the still-valid reference;
reject another user's equivalent operations; prove source deletion and snapshot
expiry have distinct behavior. Offline acceptance and live acceptance must be
reported separately.

This design PR does not close #21/#30, run product changes, or merge itself.
