"""Private bounded Agent MCP transport. No Generation or direct inference fallback."""

import asyncio
import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import UUID

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

CATALOG = {"health", "sessions.open", "ask_scoped", "ask_availability"}
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
    def __init__(self, source):
        self.source = source

    async def __aiter__(self):
        size = 0
        async for part in self.source:
            size += len(part)
            if size > 256 * 1024:
                raise AgentError()
            yield part

    async def aclose(self):
        await self.source.aclose()


class AgentTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner=None):
        self.inner = inner or httpx2.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if (
            300 <= response.status_code < 400
            or response.headers.get("content-encoding", "identity") != "identity"
            or response.is_stream_consumed
            and len(response.content) > 256 * 1024
        ):
            await response.aclose()
            raise AgentError()
        response.stream = BoundedStream(response.stream)
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
        self.transport_factory = transport_factory or AgentTransport

    async def call(self, name, request, timeout=20, *, before_dispatch=None):
        if len(json.dumps(request, ensure_ascii=False).encode()) > 36 * 1024:
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
                                or {x.name for x in tools.tools} != CATALOG
                                or len(tools.tools) != 4
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

    async def open(self, keys):
        data = await self.call("sessions.open", keys)
        if (
            type(data.get("principal_revision")) is not int
            or data["principal_revision"] != 1
        ):
            raise AgentError()
        return canonical(data["session_id"]), timestamp(data["expires_at"])

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
            timestamp(data["observed_at"]),
            timestamp(data["expires_at"]),
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
