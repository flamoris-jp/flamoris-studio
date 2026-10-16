# Automatic speech duration — 2026-10-17

Prepares Studio 1.0.10 for #93 with the matched Controller 1.0.7 descriptor.
Speech starts with blank output length and serializes it as null (automatic);
explicit numeric durations remain fixed. Strict reference/no-reference build
proofs, request identity, account/CSRF checks, leases and no-replay fences remain.
No DB schema or runtime/GPU configuration changes. Local checks: 270 Python
tests pass (179 PostgreSQL-dependent cases skipped here), 128 web tests pass,
TypeScript/Vite and wheel-from-sdist build pass. PostgreSQL/container CI,
publication and actual output quality remain pending.

# Reference-image continuation — 2026-10-13

Integrated current main into PR #80 and prepared Studio 1.0.7 for updates from 1.0.6. Preserves linked external gallery, YuE2 media and Assistant model preferences. Code CI passed, including PostgreSQL migration/server tests, frontend build/tests and both architecture disposable Updater installations. Actual shared-input/model setup and live generation remain pending. Earlier source targets below are historical.

# Production reference editor — 2026-10-12

Studio #79 implements current registered ComfyWorkFlow selection and digest pins, img2img and distinct appearance/style/control pickers, all-reference Use settings restoration and durable execution/input links (Alembic 20261012_13). Controller #13/#14 and matched Generation MCP are required. The app-owned migration is part of the existing install/update path; the Owner reports recognized old/new schema rather than fabricating a migration. Source target 1.0.4 / update from 1.0.3 is release preparation only. Local web/server checks and PostgreSQL CI evidence are attached to the PR. No release publication, live service/config change or actual inference occurred.

---

# Studio 1.0.5 published and updated — 2026-10-12 (#83)

- PR [#84](https://github.com/flamoris-jp/flamoris-studio/pull/84) merged as `a7650fdaf73961351e0d258547903e3a403bdcdd`; Issue #83 closed.
- [Studio v1.0.5](https://github.com/flamoris-jp/flamoris-studio/releases/tag/v1.0.5) published from that source with amd64/arm64 images, platform/combined catalogs and SHA256SUMS. Update edge: 1.0.4 -> 1.0.5.
- Final PR CI and merged-source CI passed: 412 server tests against PostgreSQL, 115 browser tests, web/production container builds, and disposable actual-Updater installation/start on both architectures. Merged-source runs: [application CI](https://github.com/flamoris-jp/flamoris-studio/actions/runs/38228320209), [distribution](https://github.com/flamoris-jp/flamoris-studio/actions/runs/38228320192).
- Remembers the explicitly selected/confirmed Assistant model ID per stable account key in the current browser. Assistant and Image share it across navigation/reloads; other devices do not synchronize. Restore revalidates the allowed catalog without starting a session, switching models or invoking inference.
- decopon Updater job `job-bce6226b4683afe1a0532fbf78562693` succeeded for 1.0.4 -> 1.0.5. Digest verification, existing-container checks, health and `verify_running` completed. Recorded environment/configuration, DB/account references and thumbnail mount are retained. Server Manager independently observed the new Studio container running.
- Non-blocking Updater follow-up: post-success cleanup returned `unsafe_storage` with `cleanup_pending=true`. The stopped prior containers remain; no manual deletion or forced cleanup was attempted. Runtime update remains succeeded.
- Live browser interaction remains operator acceptance; no provider inference was performed. Agent, DB schema, credentials and provider/runtime configuration were not changed. Reference-workflow PR #80 remains separate.

# Remember Assistant model selection — 2026-10-12 (#83)

- Prepares Studio 1.0.5 and its app-owned Updater catalog for updates from 1.0.4. Publication and real-host acceptance remain separate.
- Remembers the explicitly chosen model ID per stable account key in this browser; Assistant and Image share it across navigation and page reloads.
- Restores only models in the currently authorized catalog. Removed models stay unselected; discovery failures retain the saved preference. With no saved choice, the active authorized conversation model takes precedence over the catalog default.
- Restoring the selector performs no session start, model handoff or inference. Explicit switching, immutable pending payloads and existing account/session fences remain unchanged.
- Frontend suite: 115 passed; TypeScript/Vite build and five packaging tests passed. Initial GitHub server/web/container CI passed. The distribution check caught a stale Docker version label; Docker and Owner version metadata are corrected to 1.0.5, with final-head CI pending. No Agent, backend behavior, DB schema, dependency or configuration changes. GitHub CI, merge, release and real-host update remain pending.

# Studio 1.0.4 published — 2026-10-12 (#68)

- Accepted PR [#81](https://github.com/flamoris-jp/flamoris-studio/pull/81), source `4f5552abef4c8e33c005430182a5fb3ba02053f3`; Issue #68 is closed.
- [Studio v1.0.4](https://github.com/flamoris-jp/flamoris-studio/releases/tag/v1.0.4) is published with amd64/arm64 images, per-platform/combined Updater catalogs and SHA256SUMS. Update compatibility is 1.0.3 -> 1.0.4.
- Final PR and merged-source CI passed: 412 server tests against PostgreSQL 17, 107 browser tests, web/production container builds and actual disposable Updater installation/start for both architectures. Published catalog bytes and seven published checksums match GitHub Release asset digests; tag source matches the accepted merge.
- This release implements #68 only. Explicit permitted-model selection replaces the extra consent control; Agent grants, owned sessions/history and immutable handoff/request fences remain enforced. No Agent change, new setting or DB migration is required.
- Reference-workflow PR #80 remains unmerged. Real-host update and provider inference were not performed. The following entries preserve earlier preparation checkpoints.

# Assistant consent removal — 2026-10-12 (#68)

- Prepares Studio 1.0.4 from current main for #68 only. Reference-workflow PR #80 is separate and unmerged.
- Removed browser consent text/checkbox and consent-based button gates. Start/switch/continuation use explicit permitted-model selection and existing Agent model/export authorization.
- Legacy browser fields remain accepted; existing session rows and pending/completed handoff payloads are retained without replay. No Agent change, new setting or DB migration.
- Browser tests (107), TypeScript/Vite build and wheel/sdist passed. First PostgreSQL CI passed the 411 application/contract tests and exposed one outdated release-test predecessor assertion; it is corrected to 1.0.3. Both amd64/arm64 actual-Updater installation jobs passed. Final-head CI remains required before merge/publication. No real-host rollout or provider calls occurred.

# Repository-owned distribution preparation — 2026-10-11

The operator explicitly requested preparing this repository for individual Updater installation. Release 1.0.1 adds an app-owned installer definition, per-platform packaging, direct Release URLs and typed/profile validation. No centralized app distribution, live deployment, DB/data/credential change or provider invocation is performed. CI/publication and actual-host acceptance will be reported separately. This is a new-install recipe; no unverified compatibility edge is declared.

---

# Progress

## Updater adoption — 2026-10-08

Entry release target: **1.0.0**. Independent mTLS Owner and durable admission
source are implemented; source review/fixes and CI integration are complete.
PR [#69](https://github.com/flamoris-jp/flamoris-studio/pull/69) is prepared for human
review. Release publication and real-host adoption remain pending.

## Verification

Local full suite: **232 passed, 147 database tests skipped locally**. All seven application wheel-from-sdist builds and
declared Owner entrypoint/module checks passed. The operator made Updater public,
resolving the initial SDK download 404. SDK source remains pinned to
`d9f010a92ff6e8a1e7a3b7fad8817850bdfb72cd` (Updater PR #6).

[CI run 37766780241](https://github.com/flamoris-jp/flamoris-studio/actions/runs/37766780241):
379 passed against disposable PostgreSQL 17; server, web and container jobs succeeded. Full request/stream/background admission and preserved request/history/account state were reviewed. Updater management remains in the dedicated Updater Web UI.
These results precede this progress-only commit; package/source dependency pins
are unchanged. Cross-repository findings and exact evidence are recorded in
Updater [ADOPTION_REVIEW.md](https://github.com/flamoris-jp/flamoris-updater/blob/feat/application-entry-v1/docs/ADOPTION_REVIEW.md).

## Operational boundary

The entry path preserves already current application schemas and retained data;
unsupported schemas/resources and unknown outcomes remain blocked. No data/schema
initialization, private profile/trust provisioning, release publication, live
provider call, real-host update, enrollment or automatic merge occurred. Native
deployment overlays and matched dependencies remain deployment-owned.
See [Updater contract](docs/UPDATER.md).

## Pre-deployment retained-resource correction

The private inventory review confirmed that thumbnail bytes live outside
PostgreSQL while the database retains their locators. The Owner contract now
requires the `thumbnails` tree together with `configuration` and `database`, and
rejects profiles that omit it. Linux verification: **233 passed, 147 skipped**
plus targeted Ruff check/format. The repository-wide Ruff baseline still has
pre-existing findings outside this change. Dependencies are fixed to merged
Updater `797d6f4e7bd4089e7c162fa50c10a0afae68370a`, Controller
`eacf1086e28ca3eeb13b4297d4343fdec1163429`, Intelligence
`f791bf236342e7c831e4d0291709bacb2f0e54bc`, and Agent
`8ac1e9555355a5cbb255e8939f43ad041a0d221c`. PR review and CI at this exact
revision remain pending; no live service or data was changed.

## 2026-10-13 — linked gallery source and app update accepted

Studio #87 is merged and v1.0.6 published. Final CI passed 427 PostgreSQL tests, 121 web tests, frontend/container builds and both CPU Updater installations. Adds bounded automatic registration of an operator-linked external client's completed results, category filters and WAV cards with owned ABC/MIDI/metadata downloads in detail. Session/binding reauthorization and permanent claims/tombstones are preserved; no DB migration or output relocation.

The operator-authorized app update from 1.0.5 succeeded and the current container is observed running. Matched Generation 1.0.5 / embedded Controller 1.0.2 is deployed. Individual Hub credential/signing configuration, exact Studio account bindings and real signed-generation/browser acceptance remain pending; #86 stays open for that gate. The available MCP update operation preserves settings and cannot provision those grants. A private operational record contains the verified app receipts and read-only next-session preflight. Post-success previous-executable cleanup remains pending; it is not a failed runtime update and protected data must not be force-deleted.
