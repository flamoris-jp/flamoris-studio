# Decopon container deployment

Studio runs on decopon as one FastAPI container serving the built React app.
The existing PostgreSQL service holds only Studio account/catalog metadata.
Thumbnails use a persistent private bind mount. Generation MCP remains on LIME.

This is a deployment template, not an automatic deployment. Check the current
server state and existing nginx/PostgreSQL configuration before applying it.

## 1. Database roles

Create a dedicated database owned by a migration role and a separate runtime
role. Use the PostgreSQL administrative connection appropriate to the existing
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

## 2. Private configuration and storage

Install Docker Compose on decopon. Copy `deploy/studio.env.example` and
`deploy/migrate.env.example` to `/etc/flamoris-studio/studio.env` and
`/etc/flamoris-studio/migrate.env`. Replace placeholders, set permissions to
`0600`, and restrict the containing directory to administrators. The DB host
must be reachable from inside the container; `127.0.0.1` there means the
container, not the host. Use the current PostgreSQL connection route.

Prepare `/srv/disk1/flamoris-studio/thumbnails` with owner UID/GID 10001,
mode `0700`. The container runs as that unprivileged UID and has a read-only
root filesystem. Keep this directory in the backup plan. PostgreSQL contains
thumbnail locators, but the thumbnails themselves live here.

`STUDIO_GENERATION_ENDPOINT` may remain empty for the first login test.
To connect LIME later, use a TLS-protected HTTPS MCP endpoint with access
control, or forward the LIME loopback listener through an authenticated local
tunnel to decopon's localhost. With a local tunnel, use
`http://127.0.0.1:<local-port>/mcp`. The gateway intentionally refuses
plain HTTP to a remote LAN host. Restrict the tunnel or TLS endpoint to the
Studio service and keep unauthenticated MCP off public networks. Check LIME's
current Generation MCP service/port and firewall before choosing values.

## 3. Build, migrate, start

From the Studio checkout on decopon:

```sh
docker compose build studio
docker compose --profile migrate run --rm migrate
docker compose up -d studio
docker compose ps
curl -fsS http://127.0.0.1:5087/api/system/status
```

The migration job uses `migrate.env`; the long-running app uses
`studio.env`. The migration must finish successfully before starting the
app. The local status endpoint checks the HTTP process, not database or MCP
availability. Check application logs and a real login separately.

## 4. HTTPS reverse proxy and first account

Copy `deploy/nginx-studio.conf.example` into the existing nginx site
configuration after setting the real DNS name and certificate paths.
Validate with `nginx -t` and reload nginx. The container's published port
is bound to `127.0.0.1:5087`; nginx is the only intended external entry point.
The proxy supplies the public Host and HTTPS scheme for CSRF origin checks.
Do not expose container port 5087 to the LAN or set
`STUDIO_DEV_INSECURE_COOKIE=1` in production.

For initial account creation, temporarily change
`STUDIO_ALLOW_REGISTRATION=1` in the private runtime env file and recreate the
app (`docker compose up -d --force-recreate studio`). Register over HTTPS,
then restore `STUDIO_ALLOW_REGISTRATION=0` and recreate it again. Confirm a
fresh `/api/session` response reports registration closed. Keep the window
short and restrict access at the edge during provisioning if needed.

## 5. Image generation smoke test

Before turning on generation, verify the Studio page and account login work
while Generation MCP is unavailable. Once LIME Generation MCP is actually
running, configure the protected endpoint and recreate Studio. Verify discovery,
submit one small image, wait for completion, open its thumbnail/preview and
download it. Confirm catalog rows in Studio PostgreSQL, a thumbnail file under
the bind mount, and the actual generated image in Generation MCP's storage.

If Generation MCP loses its own in-memory job/asset state on restart, Studio
metadata alone cannot restore those outputs; plan long-term asset retention
separately. Do not point Studio at a provider-local image directory.

## Updating and rollback

Pull/review the desired commit, build the image, run migrations, then recreate
the app. Take a database backup before migrations. Alembic schema migrations
are not automatically rolled back when an image is reverted; check migration
compatibility before reverting an app image. Store private env files and
thumbnails outside the checkout.
