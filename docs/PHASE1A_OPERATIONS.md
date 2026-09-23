# Phase 1A development and deployment

Phase 1A has a React/Vite frontend and one ASP.NET Core .NET 10 backend.
PostgreSQL stores Identity records and private Studio catalog metadata. Generation MCP
owns the current job state and generated media. This document contains no production
hostnames, credentials, or tunnel IDs.

## Local development

Configure a dedicated PostgreSQL database and user. Keep the connection string in
environment variables or .NET user secrets, never in `appsettings.json`:

```sh
export ConnectionStrings__Studio='Host=127.0.0.1;Database=flamoris_studio;Username=studio_app;Password=...'
export Generation__Endpoint='http://127.0.0.1:8765/mcp'
export Assets__ThumbnailDirectory='/path/to/private/studio-thumbnails'
cd web && npm ci && npm run dev
```

In another terminal, from the repository root:

```sh
dotnet restore src/Flamoris.Studio.Server/Flamoris.Studio.Server.csproj --configfile NuGet.Config
dotnet run --project src/Flamoris.Studio.Server --urls http://localhost:5087
```

`Flamoris.Logging` is a GitHub Packages dependency. Supply a token with package
read access as the `GITHUB_TOKEN` environment variable for restore. In CI,
the Studio repository must be added under **Flamoris.Logging → Package settings →
Manage Actions access** with **Read** permission. Do not commit a token.

Run EF migration with a separate migration role that has DDL permission:

```sh
dotnet tool install --global dotnet-ef --version 10.0.0
dotnet ef database update --project src/Flamoris.Studio.Server --startup-project src/Flamoris.Studio.Server
```

The initial migration creates Identity, execution and asset tables; it stores
no generated media binary. The application account needs only ordinary
table/data permissions after migration. The initial migration was hand-authored,
so the EF pending-model warning is temporarily suppressed. Before the next
schema change, generate and review the EF model snapshot, then remove that
suppression; a missing snapshot otherwise blocks EF Core 10 migration commands.

In Development, self-registration is enabled. In production it is disabled by
default. Provision accounts through a controlled HTTPS registration window or a
future administrative workflow; turn registration off again after provisioning.
No default credentials are shipped. Passwords require at least 12 characters,
uppercase and digit. Login is rate limited and locked after repeated failures.

## Production

Run `npm ci && npm run build` in `web/` before `dotnet publish`. The publish
project includes `web/dist` in `wwwroot`. Set a durable directory for
`Identity__DataProtectionDirectory` with private filesystem permissions so
session and antiforgery keys survive restarts. Provide the PostgreSQL connection,
Generation MCP endpoint, and `Assets__ThumbnailDirectory` through deployment
configuration. Serve Studio via HTTPS. Keep the unauthenticated Generation MCP
endpoint inside its intended network boundary.

The thumbnail directory defaults to a maximum 1 GiB total and 256 KiB per file;
retrieved image bytes have a hard upper cap of 64 MiB. Paths in the Studio DB are
internal metadata and are never returned by the HTTP API. Store DB and thumbnails
on durable volumes if result cards must survive a Studio restart.

**Upstream lifecycle caveat:** current Generation MCP keeps jobs in process memory.
Studio catalog handles survive a Studio restart, but a Generation MCP restart can
make old job IDs and asset IDs unavailable even when Studio metadata remains.
Restoring those outputs requires a future durable upstream retrieval contract.
Studio does not pretend the cached status can recover upstream media.

## Verification

```sh
dotnet restore tests/Flamoris.Studio.Server.Tests/Flamoris.Studio.Server.Tests.csproj --configfile NuGet.Config
dotnet test tests/Flamoris.Studio.Server.Tests/Flamoris.Studio.Server.Tests.csproj --no-restore
cd web && npm ci && npm run build && npm test
```

Backend tests use a disposable PostgreSQL database through
`ConnectionStrings__Studio` and a fake Generation gateway. Do not point the tests
at production data. CI provisions a PostgreSQL service, without LIME, GPU, or
ComfyUI. A live Generation MCP smoke test remains a deployment check.
