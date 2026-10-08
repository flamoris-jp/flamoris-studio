# Progress

## Updater adoption — 2026-10-08

Entry release target: 1.0.0. Source adds durable admission and an independent
mTLS Owner with application-owned schema/resource inspection. Review/testing
and matched dependency wiring are in progress. No release or live update is
claimed. See [Updater contract](docs/UPDATER.md).


## Updater entry review checkpoint

Version 1.0.0 adds independent Owner validation and complete-request admission
(including streaming and background maintenance) through the separate Core SDK.
Updater management UI is not embedded in Studio. The Owner retains account/grant
and request state, rejects unreviewed Alembic history and blocks uncertain work.
Local full suite: 232 passed, 147 real PostgreSQL integration tests skipped;
those tests run against a disposable CI database after dependency installation.
SDK, Intelligence, Agent test and Controller test dependencies are pinned to
matched adoption commits. Changes to existing application code preserve its
formatting to keep the review focused.

Current CI blocker across adoption: Updater is a private repository. Anonymous
SDK archive retrieval returns HTTP 404 before tests run. Cross-repository
authenticated dependency access or an explicit publication decision is required;
no visibility or credential setting has been changed.
No release, live change, trust/profile provisioning or enrollment occurred.
