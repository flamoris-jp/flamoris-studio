# Internal connection cleanup and custom generation retirement

Authority: [AI #18](https://github.com/flamoris-jp/flamoris-ai/issues/18) and
[Studio #62](https://github.com/flamoris-jp/flamoris-studio/issues/62).
This source change does not deploy services, migrate databases, change grants or
credentials, delete stored data or run live/paid inference.

## Implemented paths

| Studio operation | Implemented backend boundary | Owner |
| --- | --- | --- |
| Raw Intelligence | `IntelligenceGateway` -> shared `flamoris_intelligence` library -> configured llama.cpp HTTP API | Provider executes; Studio owns admission, presentation and request correlation |
| Agent Support | `AgentGateway` -> Agent JSON HTTP `/api/v1` -> Agent's shared non-MCP provider adapter | Agent owns principals, personality, conversations and durable Agent request fences |
| Retained Image/native media and assets | Existing Generation MCP compatibility gateway | One existing generation job/input/asset authority |
| Host runtime/GPU lifecycle | No new Studio lifecycle route | GPU Node Manager |

Raw Intelligence and Agent Support do not use MCP, Hub or an external Intelligence
MCP listener. Agent is optional. The base Intelligence distribution is pinned to
the reviewed merged source; Studio imports only its neutral library namespace.
Production Studio does not import Agent's Python package. The pinned Agent test
extra runs the real versioned HTTP service and direct adapter in contract tests.

Agent requests first check the exact version-1 operation catalog. They then send
plain JSON DTOs to concrete `/api/v1` routes; there is no MCP handshake, wrapper,
discovery fallback or hidden resubmission. The existing endpoint/token variable
names remain; an old `/mcp` URL fails explicitly. Core and opt-in settings catalogs
must match on both sides. The token authenticates the backend delegator, while
Agent separately checks principal membership, grants and bound session identity.

For raw Intelligence, existing endpoint/model variables now configure the actual
local vendor API, exact served aliases and explicit operator resource limits.
`approved-local` is an operator assertion, not model discovery attestation.
This route has no remote export policy or provider fallback. The Agent route
retains its independent approved-target grants and complete-context export consent.

## Retired generation behavior

The custom/v3 Image normalization, discovery, composition and dispatch path is
removed together with `image_v3_contract.py` and its feature-only test fixture.
`STUDIO_GENERATION_V3_ENABLED` is removed from the public deployment example.
No Controller implementation replaces these components.

Image selection requires an original builtin `text-to-image` or
`text-to-image-lora` descriptor with a schema-1 build response, unchanged template
and parameter identity, no custom version/digest pins and no reference input.
The server and browser both reject a retired definition/v3 selection. Restoring
an old draft does not silently select another recipe or discard the draft.

Owned asset/input/upload APIs, immutable input identity, thumbnails and bounded
transfers remain. An attached input cannot run through the retained txt2img
recipes; the user must explicitly remove it. Historic custom executions and
uncertain submissions remain visible through retained owner-scoped records and
continue to prevent deletion of referenced inputs. No DB schema or data migration
is introduced. New reference-image development remains held.

Generation MCP still co-locates retained domain code with its external facade.
This cleanup does not claim a non-MCP generation cutover. Controller's future
contract and migration remain a separate decision, and must not recreate the
deleted ComfyWorkFlow subsystem or instantiate a second job authority.

## Retained application controls

- Login, CSRF, account bindings and ownership are checked independently of
  upstream UUIDs. Credentials and private topology stay in the backend.
- Principal/model/personality snapshots and full-context remote consent remain
  Agent-owned. Attached drafts are untrusted data, never system policy or grants.
- Studio commits its existing owner-scoped request fence before dispatch. Login
  and configuration are checked immediately before sending and before publishing
  results. Ambiguous work keeps its reservation and cannot be automatically replayed.
- Network/body/context/result limits, timeouts, strict endpoint policy, disabled
  redirects/compression/retries, safe errors and provenance validation remain.
- Generated data uses the existing asset authority and owner-checked preview,
  range/download/deletion paths. Source cleanup is not stored-data deletion.

## Verification and rollout boundary

`tests/test_internal_agent_contract.py` connects the real Studio gateway to the
real Agent HTTP app, Agent session and shared provider adapter using synthetic
principal/conversation stores and bounded provider HTTP. It checks two principals,
ordered trusted/untrusted message roles, request/session/conversation identity,
public provenance, revoked grants, refusal before dispatch and provider failure
without replay. Owning PostgreSQL suites separately verify durable state/fences;
the synthetic stores do not establish database guarantees.

Other tests retain two-account Studio isolation, CSRF, configuration/login
revocation, input/asset ownership, stale custom-draft rejection and generation
uncertainty. CI uses disposable PostgreSQL and a production container plus the
browser tests/build. No GPU, private weights, production DB or paid API is used.

An operational cutover must inventory old endpoint/mode settings and outstanding
requests, reconcile active custom/delegated generation with its previous matched
version, preserve all journals/leases/assets, and apply existing backup/migration
procedures only under separate authorization. Do not reset reservations or
retarget old sessions to make readiness succeed. CI and source merges are not
live provider qualification.

Previous contracts remain at the
[pre-cleanup source](https://github.com/flamoris-jp/flamoris-studio/tree/cdaa2f93b39fbdeae1909d45ee22c84cda6eeef7)
and in pinned Git history of the superseded Image/v3/multimodal documents. They preserve history, not
instructions to restore the retired direction.

## Follow-up audit on 2026-10-05

Current Image discovery/dispatch admits only the two original builtin txt2img
templates with parameter dimensions. Retired managed-input bindings, img2img
injection and custom fixed-dimension branches are removed. Historical selections
and reference handles remain readable; they cannot submit new work. Image admission
and response publication recheck the active Studio session and Generation route
after upstream awaits. Saved executions retain accepted or uncertain outcomes even
when a caller is no longer authorized to receive the late response.

Obsolete custom Image and v3 runbooks are removed from the current tree. Their
contracts and prior acceptance records remain available in pinned Git history:

- [Image integration](https://github.com/flamoris-jp/flamoris-studio/blob/abba5963a5e680f74b7fda717de5c7dc96a7d571/docs/WORKFLOW_IMAGE_INTEGRATION.md)
- [Image operation](https://github.com/flamoris-jp/flamoris-studio/blob/abba5963a5e680f74b7fda717de5c7dc96a7d571/docs/WORKFLOW_IMAGE_OPERATION.md)
- [Multimodal proposal](https://github.com/flamoris-jp/flamoris-studio/blob/abba5963a5e680f74b7fda717de5c7dc96a7d571/docs/MULTIMODAL_STUDIO.md)
- [Image v3 operation](https://github.com/flamoris-jp/flamoris-studio/blob/abba5963a5e680f74b7fda717de5c7dc96a7d571/docs/IMAGE_V3_OPERATION.md)

Use [current real-machine acceptance](REAL_MACHINE_ACCEPTANCE.md) for a separately
authorized operational handoff. Source review performs no deployment or provider call.
