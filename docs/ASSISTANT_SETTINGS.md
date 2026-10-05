# Assistant models and Agent personality

Owner: #56; upstream Agent#34/#35, Intelligence#8, coordination AI#17.

Apply the current Alembic chain through `20261005_12` (after settings revision
`20261004_11`), then set `STUDIO_AGENT_SETTINGS_ENABLED=1` only with Agent's matched
versioned HTTP settings and `sessions/continue` contract, including Agent
migration 005 after 002–004. These are operator rollout steps, not applied changes. Core-only HTTP
deployments remain compatible with the default flag=0; `/mcp` endpoints are rejected. The private Agent endpoint/token and exact
account UUID → Human/Agent/Project bindings remain operator managed. Hub is not
required: this uses the existing reviewed private Studio-backend → Agent path.

The Assistant panel exposes only the Agent-authorized public model list. Choose
Internal LLM or OpenAI API, then explicitly Start selected model. API selection
requires consent covering personality, previous turns, question and explicitly
attached draft. Explicit [model switching](MODEL_CONTINUATION.md) preserves the
logical conversation key, previous request handle, answer, original personality
snapshot and unsent question through immutable Agent child-session lineage.
An uncertain switch blocks new questions; explicit Check model switch reuses the
same handoff ID. Start new conversation explicitly clears the parent. Settings mode
requires explicit start when no valid session exists. The active conversation model
is displayed separately from a proposed selection. There is no hidden local/API
fallback, no GPU switch and no inferred permission from a provider key.

Personality editing is available only after Agent's independent read/edit grants.
Load the current revision, edit display name and ordered titles/Markdown bodies,
add/remove/reorder sections, confirm shared impact and Save. The editor displays
revision and unsaved changes. Text is escaped; Markdown is not executed as HTML.
The personality is Agent-owned and may be shared by multiple principals. Save
changes **new conversations**, preserving existing snapshots. Credentials, tools,
model selection and runtime policy are not part of the editable body.

Concurrent saves require the expected revision. Conflict preserves the draft and
requires explicit reload/reconciliation; it never force-overwrites. Uncertain save
freezes the exact UUID/body; an explicit retry uses that same identity. There is
no background retry and no second transcript/personality database. Agent returns
the original revision for an identical duplicate. History loads one full revision
per page to bound response size. Restore copies a historical version into the
current draft and requires a new ordinary expected-revision save.

Studio stores only an owner-scoped principal session reference/model ID/consent and
its existing conversation/request references and metadata-only durable switch fences.
Agent retains the transcript and immutable lineage. All reads and writes recheck login,
account binding and owner session after private I/O; saves also check immediately
before dispatch. Revoked session/account/configuration results are withheld.
CSRF protects every POST. Requests and private transport responses are bounded;
two settings calls per process are admitted, with no hidden work queue.

Rollout order: Intelligence PR → Agent migration/implementation/import/grants →
Studio migration/implementation/flag. Back up both DBs first and test restores.
Agent migration/import and secret/grant provisioning are explicit operations;
see its [settings contract](https://github.com/flamoris-jp/flamoris-ai-agent/blob/main/docs/ASSISTANT_SETTINGS_V1.md).
Do not delete original persona files until migration comparison and rollback have
been verified. Production migration/API calls are not performed by these PRs.

Acceptance: two accounts, explicit read/edit separation and shared scope, CSRF,
model/export grant revocation, old session refusal, conflict/duplicate/uncertainty,
login loss during I/O, escaped sections/order/history, and existing Image/Assistant
flows. CI validates contracts and disposable PostgreSQL; live API/GPU coexistence
is a separate operator-authorized smoke with synthetic text.
