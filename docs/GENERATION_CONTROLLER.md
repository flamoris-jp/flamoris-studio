# Studio generation contract

Studio's backend calls the matched Controller JSON/binary HTTP API directly. The external Generation MCP facade hosts one Controller runtime and both ingress adapters on its existing listener. Studio does not use MCP SDK values, Hub namespacing, a second JobStore or a fallback transport.

## Configuration and service permission

| Key | Exact meaning |
| --- | --- |
| `STUDIO_GENERATION_ENDPOINT` | Protected HTTPS or trusted loopback HTTP base with the exact path `/api/v1/generation`; no trailing slash, userinfo, query or fragment |
| `STUDIO_GENERATION_TOKEN` | Backend-only service credential matching host `FLAMORIS_CONTROLLER_TOKEN`, 32–512 printable ASCII characters |
| `STUDIO_GENERATION_NAMESPACE` | Empty; old `generation` Hub namespace is rejected |

Missing/invalid configuration makes generation unavailable before connection. Remote plain HTTP, redirects and environment proxies are refused. The private service credential grants the trusted backend the bounded operation surface; it is separate from external MCP/Hub credentials and Studio user identities. The host disables internal operations when its credential is unset. The optional external import still requires exact operator-provisioned issuer/subject grants and verified external provenance; headers/public arguments cannot invent that context.

Studio retains accounts, sessions, CSRF, opaque owner-scoped job/input/asset mappings, drafts/presets/history, request reservations and authorization checks before dispatch/publication. Raw upstream IDs and provenance subjects grant no user access. No DB migration or grant mutation is added by the transport change.

## Bounded operations and failures

`gateway.py` sends POST `/api/v1/generation/{operation}` with ordinary JSON arguments and reads bounded direct JSON objects. `assets.get` returns bounded image bytes; other media and larger/unknown-size images use immutable prepare/read transfers. Existing recipe/profile checks, acknowledgement checks, chunk ordering/digests, media decoding and result DTO validation remain at the consumer boundary.

The [Controller API](https://github.com/flamoris-jp/flamoris-generation-controller/blob/640a5bd48c76e4bf736e9a3589c216123ccd18b3/docs/API.md) defines 23 retained operations and strict request models. Core owns schemas 1/4/5/6 and generation constraints. Studio still applies browser-safe seed limits and exact trusted native profiles. Splitting transport does not enable reference-image generation, custom graph registration/versioning/v3/qualification or new providers.

Metadata reads are capped at 2 MiB; images at the consumer limit (up to 64 MiB); chunks remain 128 KiB for uploads / at most 256 KiB for asset reads. Normal reads use 45 seconds, image bytes 300 seconds and preparation 330 seconds. Upload uses one connection, ordered acknowledgements and a 75-second total deadline. Neither private errors nor tokens/payloads enter browser responses or logs.

409 `busy` maps to the existing safe busy result. Unknown acceptance, timeout, lost acknowledgement, failed publication or 503 never triggers a resubmit/fallback. Controller retains its durable reservation; Studio retains its separate owned request fence. A disconnect/restart does not prove release. Scoped cancellation and input-use protection still require terminal provider evidence.

## Source and operational acceptance

Direct transport tests cover authentication configuration, bounds, safe errors, no redirects/proxies/retry, binary results and uploads. `tests/test_internal_generation_contract.py` connects the real gateway to the real Controller HTTP adapter with a fake provider. Generation MCP tests contend internal HTTP and external MCP requests for the same runtime and retain providers after session disconnect. PostgreSQL/two-account tests retain Studio authorization and publication fences; browser tests cover the user flows.

The Controller test dependency is pinned to the exact matched implementation commit. Production Studio uses HTTP and does not import the Controller runtime. Source tests do not activate a provider, load weights or deploy. Operational cutover must drain/reconcile and stop the old pre-lock authority, preserve/back up records and update matched host/Studio configuration. Different output roots/hosts and old binaries are not constrained by the new local lifetime lock.
