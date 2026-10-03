"""Private bounded Agent MCP transport. No Generation or direct inference fallback."""

import asyncio
import json
import logging
import os
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CATALOG = {"health", "sessions.open", "ask_scoped", "ask_availability"}
SETTINGS_TOOLS = {
    "models.allowed",
    "personality.get",
    "personality.history",
    "personality.save",
}
PRIVATE = ContextVar("studio_agent_transport", default=False)


class AgentError(Exception):
    def __init__(self, code="unavailable"):
        super().__init__("Assistant unavailable")
        self.code = code


class PrivateLog(logging.Filter):
    def filter(self, record):
        if PRIVATE.get():
            record.msg, record.args, record.exc_info, record.stack_info = (
                "Agent transport event",
                (),
                None,
                None,
            )
            record.exc_text = None
        return True


for name in ("mcp.client.streamable_http", "mcp.client.session", "httpx2"):
    logging.getLogger(name).addFilter(PrivateLog())


class BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, source, limit=256 * 1024):
        self.source = source
        self.limit = limit

    async def __aiter__(self):
        size = 0
        async for part in self.source:
            size += len(part)
            if size > self.limit:
                raise AgentError()
            yield part

    async def aclose(self):
        await self.source.aclose()


class AgentTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner=None, *, limit=256 * 1024):
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0)
        self.limit = limit

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if (
            300 <= response.status_code < 400
            or response.headers.get("content-encoding", "identity") != "identity"
            or response.is_stream_consumed
            and len(response.content) > self.limit
        ):
            await response.aclose()
            raise AgentError()
        response.stream = BoundedStream(response.stream, self.limit)
        return response

    async def aclose(self):
        await self.inner.aclose()


def canonical(value):
    try:
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AgentError() from None
    return value


def timestamp(value):
    if type(value) is not str or len(value) > 64:
        raise AgentError()
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise AgentError() from None
    if stamp.tzinfo is None:
        raise AgentError()
    return stamp.astimezone(timezone.utc)


class AgentGateway:
    def __init__(self, endpoint, token, *, transport_factory=None):
        if type(endpoint) is not str or len(endpoint) > 2048 or type(token) is not str:
            raise AgentError()
        try:
            url = urlsplit(endpoint)
            port = url.port
        except ValueError:
            raise AgentError() from None
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or port is not None
            and not 1 <= port <= 65535
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.scheme == "http"
            and url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,512}", token)
        ):
            raise AgentError()
        self.endpoint, self.token = endpoint, token
        self.settings_enabled = os.getenv("STUDIO_AGENT_SETTINGS_ENABLED") == "1"
        self.transport_factory = transport_factory or AgentTransport

    async def call(self, name, request, timeout=20, *, before_dispatch=None):
        if len(json.dumps(request, ensure_ascii=False).encode()) > (
            72 * 1024 if name == "personality.save" else 36 * 1024
        ):
            raise AgentError("invalid_input")
        marker = PRIVATE.set(True)
        try:
            async with asyncio.timeout(timeout):
                async with httpx2.AsyncClient(
                    transport=self.transport_factory(),
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx2.Timeout(timeout),
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Accept-Encoding": "identity",
                    },
                ) as http:
                    async with streamable_http_client(
                        self.endpoint, http_client=http, terminate_on_close=False
                    ) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            tools = await session.list_tools()
                            if (
                                tools.next_cursor is not None
                                or {x.name for x in tools.tools}
                                != (
                                    CATALOG | SETTINGS_TOOLS
                                    if self.settings_enabled
                                    else CATALOG
                                )
                                or len(tools.tools)
                                != (8 if self.settings_enabled else 4)
                            ):
                                raise AgentError()
                            expected = {
                                "sessions.open": {"human", "agent", "project"},
                                "ask_availability": {"session_id"},
                                "ask_scoped": {
                                    "session_id",
                                    "request_id",
                                    "text",
                                    "previous_conversation_id",
                                    "context",
                                },
                            }
                            if self.settings_enabled:
                                expected["sessions.open"] |= {
                                    "model_id",
                                    "remote_consent",
                                }
                                expected.update(
                                    {
                                        "models.allowed": {"human", "agent", "project"},
                                        "personality.get": {
                                            "session_id",
                                            "before_revision",
                                        },
                                        "personality.history": {
                                            "session_id",
                                            "before_revision",
                                        },
                                        "personality.save": {
                                            "session_id",
                                            "request_id",
                                            "expected_revision",
                                            "display_name",
                                            "sections",
                                        },
                                    }
                                )
                            for tool in tools.tools:
                                if tool.name == "health":
                                    continue
                                schema = tool.input_schema.get("properties", {}).get(
                                    "request", {}
                                )
                                if (
                                    schema.get("type") != "object"
                                    or schema.get("additionalProperties") is not False
                                    or set(schema.get("properties", {}))
                                    != expected[tool.name]
                                ):
                                    raise AgentError()
                            if before_dispatch is not None:
                                await before_dispatch()
                            result = await session.call_tool(
                                name, {"request": request}, read_timeout_seconds=timeout
                            )
                            data = result.structured_content
                            if (
                                result.is_error
                                and type(data) is dict
                                and data.get("ok") is True
                            ):
                                raise AgentError()
                            if result.is_error and not isinstance(data, dict):
                                raise AgentError()
                            if (
                                type(data) is not dict
                                or len(json.dumps(data, ensure_ascii=False).encode())
                                > 128 * 1024
                            ):
                                raise AgentError()
                            if data.get("ok") is not True:
                                code = (
                                    data.get("error", {}).get("code")
                                    if type(data.get("error")) is dict
                                    else None
                                )
                                raise AgentError(
                                    code
                                    if code
                                    in {
                                        "busy",
                                        "principal_unavailable",
                                        "invalid_input",
                                        "input_too_large",
                                        "conversation_unavailable",
                                        "duplicate_request",
                                        "revision_conflict",
                                        "personality_forbidden",
                                        "personality_unavailable",
                                        "personality_capacity",
                                        "update_identity_mismatch",
                                        "model_forbidden",
                                        "model_selection_changed",
                                        "remote_consent_required",
                                    }
                                    else "uncertain"
                                )
                            return data
        except AgentError:
            raise
        except Exception:
            raise AgentError() from None
        finally:
            PRIVATE.reset(marker)

    async def models(self, keys):
        data = await self.call("models.allowed", keys)
        rows = data.get("models")
        if type(rows) is not list or len(rows) > 16:
            raise AgentError()
        seen = set()
        for row in rows:
            if type(row) is not dict or set(row) != {"id", "display_name", "data_flow"}:
                raise AgentError()
            if (
                type(row["id"]) is not str
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", row["id"])
                or row["id"] in seen
                or type(row["display_name"]) is not str
                or not 1 <= len(row["display_name"]) <= 128
                or row["data_flow"] not in {"local_only", "remote_authorized"}
            ):
                raise AgentError()
            seen.add(row["id"])
        default = data.get("default_model_id")
        if default is not None and (type(default) is not str or default not in seen):
            raise AgentError()
        return {"models": rows, "defaultModelId": default}

    async def personality(self, operation, payload, *, before_dispatch=None):
        data = await self.call(
            "personality." + operation, payload, before_dispatch=before_dispatch
        )
        if operation == "save":
            if (
                type(data.get("revision")) is not int
                or not 1 <= data["revision"] <= 256
                or type(data.get("duplicate")) is not bool
            ):
                raise AgentError("uncertain")
            return {"revision": data["revision"], "duplicate": data["duplicate"]}
        rows = data.get("versions") if operation == "history" else [data]
        if type(rows) is not list or len(rows) > 20:
            raise AgentError()
        normalized = []
        for row in rows:
            if (
                type(row) is not dict
                or type(row.get("revision")) is not int
                or not 1 <= row["revision"] <= 256
                or type(row.get("can_edit")) is not bool
                or row.get("scope") != "shared_agent"
                or type(row.get("display_name")) is not str
                or not 1 <= len(row["display_name"]) <= 128
                or type(row.get("sections")) is not list
                or not 1 <= len(row["sections"]) <= 16
            ):
                raise AgentError()
            size = 0
            for section in row["sections"]:
                if (
                    type(section) is not dict
                    or set(section) != {"title", "content"}
                    or type(section["title"]) is not str
                    or not 1 <= len(section["title"]) <= 80
                    or type(section["content"]) is not str
                    or not section["content"].strip()
                ):
                    raise AgentError()
                size += len(section["content"].encode())
            if size > 32768:
                raise AgentError()
            timestamp(row.get("updated_at"))
            normalized.append(
                {
                    k: row[k]
                    for k in (
                        "revision",
                        "display_name",
                        "sections",
                        "can_edit",
                        "scope",
                        "updated_at",
                    )
                }
            )
        before = data.get("before_revision")
        if before is not None and (type(before) is not int or not 1 <= before <= 257):
            raise AgentError()
        return (
            {"versions": normalized, "beforeRevision": before}
            if operation == "history"
            else normalized[0]
        )

    async def open(self, keys):
        data = await self.call("sessions.open", keys)
        if (
            type(data.get("principal_revision")) is not int
            or data["principal_revision"] != 1
        ):
            raise AgentError()
        return canonical(data.get("session_id")), timestamp(data.get("expires_at"))

    async def availability(self, session_id):
        data = await self.call("ask_availability", {"session_id": session_id})
        if (
            data.get("state")
            not in {"ready", "busy", "offline", "unavailable", "unknown"}
            or type(data.get("available")) is not bool
            or type(data.get("principal_revision")) is not int
            or data["principal_revision"] != 1
            or data.get("context_revisions") != [1]
            or type(data["context_revisions"][0]) is not int
            or data.get("proposal_revisions") != []
        ):
            raise AgentError()
        observed, expires = (
            timestamp(data.get("observed_at")),
            timestamp(data.get("expires_at")),
        )
        now = datetime.now(timezone.utc)
        if not (
            0 < (expires - observed).total_seconds() <= 5 and observed <= now < expires
        ):
            raise AgentError()
        if data["available"] is not (data["state"] == "ready"):
            raise AgentError()
        return {
            "available": data["available"],
            "state": data["state"],
            "expiresAt": expires.isoformat(),
        }

    async def ask(self, request, *, before_dispatch=None):
        data = await self.call(
            "ask_scoped", request, timeout=150, before_dispatch=before_dispatch
        )
        if (
            set(data)
            != {
                "ok",
                "request_id",
                "session_id",
                "conversation_id",
                "text",
                "provenance",
            }
            or data["request_id"] != request["request_id"]
            or data["session_id"] != request["session_id"]
            or type(data["text"]) is not str
            or not data["text"].strip()
            or len(data["text"].encode()) > 65536
            or type(data["provenance"]) is not dict
        ):
            raise AgentError("uncertain")
        canonical(data["conversation_id"])
        provenance = data["provenance"]
        if set(provenance) - {"provider", "model", "execution_id"} or any(
            type(provenance.get(k)) is not str
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", provenance[k])
            for k in ("provider", "model")
        ):
            raise AgentError("uncertain")
        if "execution_id" in provenance:
            canonical(provenance["execution_id"])
        return data
