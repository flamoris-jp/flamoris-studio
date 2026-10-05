# Studio container deployment

FLAMORIS Studio can run on any Linux host with Docker Compose and network access
to PostgreSQL and the explicitly configured backend services.

The production container serves the built React application from FastAPI.
PostgreSQL stores Studio account/catalog metadata. Thumbnails use a persistent
private bind mount. The Controller API is optional during initial setup and may run
on the same host, another trusted host, or behind a protected proxy/tunnel.

Raw Intelligence uses the shared non-MCP llama.cpp adapter; Agent Support uses
Agent HTTP `/api/v1`. Generation uses Controller HTTP `/api/v1/generation`,
co-hosted by the external Generation MCP process around the same runtime. Follow
[architecture cleanup](ARCHITECTURE_CLEANUP.md), [raw Intelligence](RAW_INTELLIGENCE.md)
and [assistant settings](ASSISTANT_SETTINGS.md) for their exact configuration.

This is a deployment template, not an automatic deployment. Review the target
host, database, reverse proxy, storage, and network policy before applying it.

## 1. Database roles

Create a dedicated database owned by a migration role and a separate runtime
role. Use the PostgreSQL administrative connection appropriate to your
installation. Enter passwords interactively with `\password`, and keep them
out of command history and repository files. The example assumes the default
`public` schema is dedicated to Studio:

```sql
CREATE ROLE studio_migrate LOGIN;
CREATE ROLE studio_app LOGIN;
CREATE DATABASE flamoris_studio OWNER studio_migrate;
\password studio_migrate
\password studio_app
\connect flamoris_studio
GRANT CONNECT ON DATABASE flamoris_studio TO studio_app;
GRANT USAGE ON SCHEMA public TO studio_app;
ALTER DEFAULT PRIVILEGES FOR ROLE studio_migrate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO studio_app;
```

Apply the default privileges **before** the first migration. For an existing
database, also grant DML on existing tables. Keep the migration credential out
of the running Studio container.

The database host must be reachable from inside the container. `127.0.0.1`
inside the container refers to the container itself, not the Docker host.

## 2. Private configuration and storage

Copy the example env files to private runtime files:

```sh
cp deploy/studio.env.example deploy/studio.env
cp deploy/migrate.env.example deploy/migrate.env
chmod 0600 deploy/studio.env deploy/migrate.env
```

These runtime `.env` files are ignored by Git. You may keep them elsewhere
instead by setting Compose interpolation variables before running Docker:

```sh
export STUDIO_ENV_FILE=/secure/path/studio.env
export STUDIO_MIGRATE_ENV_FILE=/secure/path/migrate.env
```

Create a writable thumbnail directory for container UID/GID 10001. The default
Compose path is `./data/thumbnails`, but any host path may be supplied through
`STUDIO_THUMBNAILS_DIR`:

```sh
mkdir -p data/thumbnails
sudo chown 10001:10001 data/thumbnails
chmod 0700 data/thumbnails
```

For a different location:

```sh
export STUDIO_THUMBNAILS_DIR=/path/to/private/studio-thumbnails
```

Keep the thumbnail directory in the backup plan. PostgreSQL contains thumbnail
locators, but the thumbnail files themselves live in this storage.

`STUDIO_GENERATION_ENDPOINT` may remain empty for the first login test. To
enable generation, use a protected HTTPS Controller endpoint or a trusted local tunnel
to a loopback-only matched Generation listener. With a local tunnel, an endpoint
such as `http://127.0.0.1:<local-port>/api/v1/generation` is appropriate. The gateway
intentionally refuses plain HTTP to a remote LAN host.

Configure the host's private `FLAMORIS_CONTROLLER_TOKEN` and the same backend-only
`STUDIO_GENERATION_TOKEN` (32–512 printable ASCII credential characters), and leave
`STUDIO_GENERATION_NAMESPACE` empty. External MCP/Hub ingress credentials are
separate. Legacy MCP routes are rejected by Studio with no fallback. Drain or
reconcile and stop the previous generation authority before activating this matched
version; do not delete active journals or the lifetime ownership lock. See
[the generation contract](GENERATION_CONTROLLER.md).

## 3. Build, migrate, start

From the Studio checkout:

```sh
docker compose build studio
docker compose --profile migrate run --rm migrate
docker compose up -d studio
docker compose ps
curl -fsS http://127.0.0.1:${STUDIO_BIND_PORT:-5087}/api/system/status
```

The migration job uses the migration env file; the long-running app uses the
runtime env file. The migration must finish successfully before starting the
app. The status endpoint checks the HTTP process, not database or provider
availability. Check application logs and a real login separately.

The default published bind is `127.0.0.1:5087`. Override it only when your
network design requires a different bind:

```sh
export STUDIO_BIND_HOST=127.0.0.1
export STUDIO_BIND_PORT=5087
```

## 4. HTTPS reverse proxy and first account

The repository includes `deploy/nginx-studio.conf.example`. Copy it into your
nginx configuration after replacing the example DNS name and certificate
paths. Validate the nginx configuration before reloading it.

The default container port is loopback-only so a local reverse proxy can be the
external entry point. The proxy must pass the public Host and HTTPS scheme for
CSRF origin checks. Set `STUDIO_TRUSTED_PROXY_IPS` to the exact source IP or
reviewed CIDR from which the proxy connects **as seen by the container**. Host
nginx published through Docker commonly uses a bridge gateway IP; loopback
defaults alone will not trust that proxy. Empty disables forwarded headers;
wildcards and arbitrary hostnames are rejected. Keep direct access restricted.

Apply migration `20261003_07` before running this source. Login admission is
shared in PostgreSQL across workers and restarts: at most ten admitted attempts
per client address in a fixed five-minute window, with 10,000 active address
rows. Only expired rows are removed, in bounded batches. Capacity/lock contention
returns 429 and never resets another client's active protection. Address hashes
and counters are temporary admission metadata; passwords are not stored there.
`STUDIO_WORKERS` may be set from 1 to 16. Account failure/lockout updates are
serialized separately from the global admission transaction, and unknown,
known and locked accounts each perform one Argon2 verification. Responses remain
generic. Keep the example nginx edge rate limit as an additional boundary.

Do not expose port 5087 directly to untrusted networks or set
`STUDIO_DEV_INSECURE_COOKIE=1` in production.

For initial account creation, temporarily set
`STUDIO_ALLOW_REGISTRATION=1` in the private runtime env file and recreate the
app:

```sh
docker compose up -d --force-recreate studio
```

Register over HTTPS, then restore `STUDIO_ALLOW_REGISTRATION=0` and recreate
the app again. Confirm a fresh `/api/session` response reports registration
closed. Keep the registration window short and restrict access at the edge
during provisioning if needed.

## 5. Image generation smoke test

Before enabling the Controller API, verify that the Studio page and account login
work while generation is unavailable.

Once a matched authenticated Controller endpoint is running, configure
`STUDIO_GENERATION_ENDPOINT` and recreate Studio. Verify discovery, submit one
small image, wait for completion, open its thumbnail/preview, and download it.

Confirm:
- Studio catalog rows exist in PostgreSQL;
- a thumbnail exists in the configured thumbnail directory;
- the generated asset remains owned by Controller rather than by a
  provider-local path exposed to Studio.

If the Controller host cannot restore job/asset state after its own restart, Studio
metadata alone cannot restore those outputs. Plan durable asset retention at
the owning service. Do not point Studio directly at a provider-local output
directory.

## Upgrading an existing deployment

Earlier FLAMORIS deployments may have used fixed env-file and thumbnail paths in
`compose.yaml`. Before updating such a deployment, record those current paths.
Either move the private files to the new generic defaults or export the Compose
interpolation variables so the existing locations remain in use:

```sh
export STUDIO_ENV_FILE=/path/to/existing/studio.env
export STUDIO_MIGRATE_ENV_FILE=/path/to/existing/migrate.env
export STUDIO_THUMBNAILS_DIR=/path/to/existing/thumbnails
export STUDIO_BIND_HOST=127.0.0.1
export STUDIO_BIND_PORT=5087
```

Run `docker compose config` and verify the resolved paths and published bind
before recreating a live service.

## Updating and rollback

Review the desired commit, build the image, run migrations, then recreate the
app. Take a database backup before migrations. Alembic schema migrations are
not automatically rolled back when an image is reverted; check migration
compatibility before reverting an app image.

Keep private env files and thumbnail storage outside version control. Deployment
specific hostnames, IP addresses, credentials, and reverse-proxy configuration
belong in the operator's deployment configuration, not in the public defaults.
