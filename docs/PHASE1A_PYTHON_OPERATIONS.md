# Phase 1A Python deployment and development

The Studio server is Python/FastAPI. The browser app is React/TypeScript/Vite. The
PostgreSQL catalog stores account and ownership metadata, never image bytes.

## Local setup

Use a dedicated PostgreSQL database and least-privilege application role. Run
Alembic using a separate migration role that can change schema. Never commit a
production connection string or MCP endpoint.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
export STUDIO_DATABASE_URL='postgresql+psycopg://studio:password@127.0.0.1/studio'
export STUDIO_GENERATION_ENDPOINT='http://127.0.0.1:8765/mcp'
export STUDIO_THUMBNAIL_DIR='/private/studio-thumbnails'
.venv/bin/alembic upgrade head
STUDIO_DEV_INSECURE_COOKIE=1 STUDIO_ALLOW_REGISTRATION=1 \
  .venv/bin/uvicorn flamoris_studio.app:production_app --factory --port 5087
```

In another terminal run `cd web && npm ci && npm run dev`. The development Vite
server proxies `/api` to port 5087. Cookies without `Secure` are permitted only
when `STUDIO_DEV_INSECURE_COOKIE=1`; never set that variable in production.

## Production

Run `npm ci && npm run build` under `web/`. Set `STUDIO_WEB_DIST` to the absolute
path of `web/dist`, and serve FastAPI behind HTTPS using trusted proxy headers.
Set `STUDIO_DATABASE_URL`, `STUDIO_GENERATION_ENDPOINT`, and
`STUDIO_THUMBNAIL_DIR` through private deployment configuration. Registration is
off unless `STUDIO_ALLOW_REGISTRATION=1`; open it only in a controlled window to
provision users. No credentials or user accounts are shipped.

The random session token lives in an HttpOnly, Secure, SameSite cookie. Its SHA256
hash and expiry are stored in PostgreSQL. Writing APIs require an additional CSRF
token. Login has per-account lockout and process-local IP throttling; configure
edge rate limits if using more than one process. Set thumbnail storage on a private
durable volume. Generation MCP retains its own job/asset authority; if it loses
in-memory records on restart, Studio metadata alone cannot reconstruct a lost
output. Submission timeouts are never retried automatically.

## Validation

With a disposable PostgreSQL database in `STUDIO_DATABASE_URL`:

```sh
.venv/bin/alembic upgrade head
.venv/bin/pytest -q
cd web && npm ci && npm run build && npm test
```

CI uses disposable PostgreSQL and a fake Generation gateway. A live MCP/GPU
smoke test remains a deployment check.
