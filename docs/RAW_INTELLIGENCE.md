# Raw Intelligence editor

Owning issue: #2; independent of the Agent conversation assistant in #39/#24.
The Intelligence workspace now sends one synchronous inference request
through `IIntelligenceGateway` and the shared non-MCP `flamoris_intelligence`
provider adapter and displays text, partial-output status, safe
model provenance, duration and optional provider-reported token usage. The separate
Assistant workspace retains Agent-owned conversations and scoped Image advice.

## Configuration and rollout

The optional backend configuration is fail-closed:

| Variable | Meaning |
| --- | --- |
| `STUDIO_INTELLIGENCE_ENDPOINT` | Protected local llama.cpp/vendor HTTPS base URL, or trusted loopback HTTP tunnel; no `/mcp` path |
| `STUDIO_INTELLIGENCE_TOKEN` | Backend-only proxy Bearer token; required for HTTPS, optional for protected loopback |
| `STUDIO_INTELLIGENCE_DATA_FLOW` | Must be `approved-local` after the operator verifies the owning service's actual local provider configuration |
| `STUDIO_INTELLIGENCE_MODELS` | JSON array of 1–16 exact public model/provider/context/output pins |

Example model pin (use the actual served vendor model alias and reviewed operator resource limits):

```json
[{"model_id":"gpt-oss-20b","provider_id":"llamacpp","context_tokens":32768,"max_output_tokens":4096}]
```

The local-data-flow setting is an operator configuration assertion, not discovery
attestation. Verify the actual inference endpoint, logging and retention before enabling it.
`STUDIO_INTELLIGENCE_TOKEN` is a vendor/proxy credential, not an MCP credential.
The model pin ID is the exact served vendor alias; limits are operator configuration,
not invented metadata from `/v1/models`. Old `/mcp` endpoint settings fail closed.
This source change does not change live settings or clear request-fence tables.
This delivery supports the reviewed llama.cpp contract only. It has no remote
export/fallback policy, provider selection, runtime activation or API credentials
in the browser. All authenticated Studio accounts may use the configured local
target allowlist. Individual target grants require a later explicit policy.

Apply migration `20261003_06` using the existing backup/migration-role procedure
before starting the new code, even if the optional editor remains disabled.
The runtime role requires the same DML grants for the new `intelligence_requests`
table. Use one Studio process for the process-owned admission bound. Downgrade
refuses to erase nonempty request fences. Normal rollout does not clear old data.

## Request and result boundaries

`GET /api/intelligence/discovery` is authenticated and returns approved aliases,
limits and bounded provider reachability. It is not proof that each configured
model is loaded. Discovery performs no inference or runtime switching.
`POST /api/intelligence/execute` requires authentication/CSRF and accepts one
request UUID, model/capability, prompt, explicit system instruction, temperature
and max output tokens. Text/code attachments are explicitly selected in the
browser, decoded as bounded UTF-8, and included in the prompt only on Run. The
server never reads attachment paths/URLs. Combined prompt/instruction/attachments
are limited to 16 KiB; request wire body is 36 KiB and received within five seconds.
The selected model's conservative context/output bounds are checked again.

The gateway wraps the shared non-MCP adapter from the existing Intelligence package.
It checks the configured model pin and the vendor's actual served alias immediately
before Studio admission. The adapter enforces final model/context limits, bounds
vendor I/O and normalizes results. The bounded 120-second inference is sent once.
There is no MCP negotiation, Hub routing or new intelligence network service.
Credentials stay in the backend.
The browser additionally bounds response receipt (including the body) to 170
seconds. A stalled connection ends in the same uncertain locked state; this
timeout does not claim that remote GPU work stopped or permit automatic replay.

Migration adds only owner-scoped correlation UUIDs, binding/request digests,
timestamps and presentation observations. No prompt, attachment, answer or raw
provider error is stored in these rows. Before dispatch the unique
`(user_id,request_id)` fence is committed as uncertain. Duplicate IDs cannot replay
work after result loss or restart. There is no upstream polling job, result
retrieval API, request queue or automatic new-ID retry. Fence retention needs an
explicit later policy; downgrade must not erase these records silently.

Login and configuration are checked immediately before dispatch and before
publication. Revocation/rotation withholds the answer; it cannot undo inference
already admitted. Reaching a token limit is shown as partial, including the valid
empty-final-text case. All answer text is escaped, independently bounded to 64 KiB,
and never executed as code/HTML. Response wire is limited to 1 MiB. Redirects,
compressed payloads, unsafe endpoints, private diagnostics and retries are refused.

## Acceptance

Normal tests use bounded HTTP vendor fixtures, the shared non-MCP adapter and
the PostgreSQL two-account fixture. They cover exact served aliases/operator pins, stale
configuration, combined input bounds, partial output, no replay, CSRF, owner
isolation, logout during inference and escaped UI output. The UI retains its draft
and in-flight snapshot across workspace changes for the same account; logout/account
remount clears them. Communication uncertainty locks the attempt until the user
explicitly starts a new request.

Actual deployment acceptance remains separate: verify authenticated reachability,
approved local model pins, one real inference and two-account isolation. Ordinary
CI never activates a GPU or requires models/private credentials.
