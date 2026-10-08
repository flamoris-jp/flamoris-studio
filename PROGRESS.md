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
