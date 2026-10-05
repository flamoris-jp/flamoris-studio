# Assistant conversation model switching

Accepted Studio #66, matched with Agent #43 (scope: Agent #42), introduces
`POST /api/assistant/switch`. Source is merged; live migrations and provider
acceptance remain pending. The authenticated browser sends
`sessionKey`, `requestId`, `expectedModelId`, `modelId`, and `remoteConsent`.
Studio keeps its logical conversation key and previous request handle, while
Agent's internal HTTP continuation creates immutable model/principal lineage.
The assistant's answer, personality snapshot and unsent question survive switching.
The raw Intelligence form remains a one-shot inference surface.

The selector remains usable after a completed turn. API selection requires the
complete-context consent already shown in the assistant. An uncertain switch
stores a durable request fence before dispatch and disables new questions. An
explicit "Check model switch" action reuses the same handoff identity; polling
never retries it. Unknown/inflight turns cannot be inherited. "Start new
conversation" explicitly creates a new logical session and clears the parent.

Alembic revision 20261005_12 adds metadata-only switch fences; it stores no
transcript, prompt, credential or provider configuration. The matching Agent
build and migration 005 are required when assistant settings are enabled.
Authenticated users, CSRF, current principal bindings, permissions and result
publication checks remain required. No live migration or deployment is included.
Agent bounds history to 12 messages/64 KiB, with existing authorization expiry
and session capacity. Downgrade refuses to silently erase handoff fences.
