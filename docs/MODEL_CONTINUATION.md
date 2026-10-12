# Assistant conversation model switching

Accepted Studio #66, matched with Agent #43 (scope: Agent #42), introduces
`POST /api/assistant/switch`. Source is merged; live migrations and provider
acceptance remain pending. The authenticated browser sends
`sessionKey`, `requestId`, `expectedModelId`, and `modelId`.
Studio keeps its logical conversation key and previous request handle, while
Agent's internal HTTP continuation creates immutable model/principal lineage.
The assistant's answer, personality snapshot and unsent question survive switching.
The raw Intelligence form remains a one-shot inference surface.

The selector remains usable after a completed turn. Studio 1.0.4 (#68) removes
the remote-transmission consent text and checkbox. The user explicitly selects
a model from the Agent-authorized catalog; Studio derives the existing Agent
`remote_consent` wire value from that model's remote/local policy. Agent continues
to enforce membership, model/export grants and complete-context authorization.
No additional browser approval, setting or Agent upgrade is required. An uncertain switch
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

## Compatibility in Studio 1.0.4

The legacy strict-boolean browser field `remoteConsent` is still accepted, but it does not override the allowed-model policy for a new start or switch. Existing session rows and internal response fields are retained. A stored handoff reuses its original Agent payload and remote flag, including legacy local handoffs; the upgrade never rewrites a pending request or replays it automatically. Completed duplicates retain their result. No schema migration or conversation restart is required.
