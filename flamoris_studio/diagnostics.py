"""Bounded, payload-free request diagnostics; never an execution authority."""
import asyncio
import contextvars
import json
import logging
import os
import re
import sqlite3
import time
import traceback
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

from .auth import authenticated_user, current_user, database

TRANSPORT = contextvars.ContextVar("studio_diagnostic_transport", default=None)
CONTEXT = contextvars.ContextVar("studio_diagnostic_context", default=None)
MAX_EVENTS = 5000
RETENTION_SECONDS = 7 * 86400
COMPONENTS = {"studio", "generation", "agent", "intelligence", "browser"}
REASONS = {
    "busy", "unavailable", "validation", "upstream_failure", "asset_too_large",
    "transfer_unavailable", "invalid_input", "input_too_large", "uncertain",
    "principal_unavailable", "conversation_unavailable", "duplicate_request",
    "revision_conflict", "personality_forbidden", "personality_unavailable",
    "personality_capacity", "update_identity_mismatch", "model_forbidden",
    "model_selection_changed", "session_continuation_changed", "conversation_incomplete",
    "remote_consent_required", "invalid_request", "unknown_model", "unknown_capability",
    "context_limit", "provider_unavailable", "provider_timeout", "provider_rejected",
    "provider_unauthorized", "provider_busy", "invalid_provider_response", "response_limit",
    "internal_error", "contract_mismatch", "invalid_result", "forbidden", "not_found",
    "request_rejected", "cancelled", "exception", "network_failure", "script_error",
    "unhandled_rejection", "stream_interrupted", "image_template_unavailable", "reference_unavailable",
    "image_parameters_unsupported", "reference_quota", "reference_busy", "http_failure", "execution_error", "submission_unknown",
    "receipt_identity", "receipt_provenance", "receipt_media", "receipt_size",
    "receipt_digest", "receipt_timestamps", "receipt_lifetime", "receipt_clock", "receipt_expired",
    "upload_receipt_invalid", "upload_rejected", "incomplete_upload",
}


def bind_owner(request, owner):
    # Sync dependencies execute in thread copies of ContextVars. The ASGI scope
    # is shared, so bind the verified owner on this object, never from a header.
    state = request.scope.get("studio_diagnostics")
    if state is not None:
        state["owner"] = str(owner)


def operator(owner):
    # Exact UUIDs; empty/malformed configuration grants no cross-account access.
    try:
        values = json.loads(os.getenv("STUDIO_DIAGNOSTICS_OPERATORS", "[]"))
        if type(values) is not list or len(values) > 64:
            return False
        return str(owner) in {str(uuid.UUID(value)) for value in values}
    except (ValueError, TypeError, AttributeError):
        return False


def route_name(scope):
    route = scope.get("route")
    # Only framework-owned route templates; never a raw URL, ID or query.
    path = getattr(route, "path", None)
    return path if isinstance(path, str) and path.startswith("/api/") else "unmatched"


def exception_detail(exc):
    """Class and code locations only: exception strings/locals may contain secrets."""
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 4:
        seen.add(id(exc))
        frames = traceback.extract_tb(exc.__traceback__)[-8:]
        chain.append({"type": type(exc).__name__[:100], "frames": [
            {"file": Path(frame.filename).name[:100], "line": frame.lineno,
             "function": frame.name[:100]} for frame in frames
        ]})
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return chain


class DiagnosticStore:
    def __init__(self, directory):
        self.path = Path(directory) / "events.sqlite3" if directory else None

    def connect(self):
        if self.path is None:
            raise OSError("Diagnostic storage is not configured")
        self.path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise OSError("Invalid diagnostic storage")
        # Private file from creation, before sqlite can write any event.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        db = sqlite3.connect(self.path, timeout=0.2)
        try:
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, stamp REAL NOT NULL, owner TEXT, request_id TEXT NOT NULL, level TEXT NOT NULL, component TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS events_owner ON events(owner, id)")
            db.execute("CREATE INDEX IF NOT EXISTS events_request ON events(request_id, id)")
            return db
        except BaseException:
            db.close()
            raise

    def append(self, event, owner):
        with closing(self.connect()) as db, db:
            db.execute("INSERT INTO events(stamp,owner,request_id,level,component,data) VALUES(?,?,?,?,?,?)",
                       (time.time(), owner, event["requestId"], event["level"], event["component"], json.dumps(event)))
            db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - ? OR stamp < ?",
                       (MAX_EVENTS, time.time() - RETENTION_SECONDS))

    def read(self, owner, *, all_accounts=False, request_id=None, level=None, component=None, before=None, limit=50):
        clauses, args = ["stamp >= ?"], [time.time() - RETENTION_SECONDS]
        if not all_accounts:
            clauses.append("owner = ?")
            args.append(str(owner))
        for column, value in (("request_id", request_id), ("level", level), ("component", component)):
            if value is not None:
                clauses.append(column + " = ?")
                args.append(value)
        if before is not None:
            clauses.append("id < ?")
            args.append(before)
        db = self.connect()
        try:
            with db:
                db.execute("DELETE FROM events WHERE stamp < ?", (time.time() - RETENTION_SECONDS,))
            rows = db.execute("SELECT id, data FROM events WHERE " + " AND ".join(clauses) + " ORDER BY id DESC LIMIT ?", [*args, limit + 1]).fetchall()
        finally:
            db.close()
        more = len(rows) > limit
        items = [{**json.loads(raw), "id": key} for key, raw in rows[:limit]]
        return {"items": items, "nextCursor": items[-1]["id"] if more else None}


def emit(component, operation, phase, *, reason=None, status=None, elapsed=None, exc=None, level="ERROR"):
    context = CONTEXT.get()
    if context is None:
        return
    event = {
        "time": datetime.now(timezone.utc).isoformat(), "requestId": context["id"],
        "level": level, "component": component if component in COMPONENTS else "studio",
        "operation": operation, "phase": phase, "route": route_name(context["scope"]),
        "method": context["scope"]["method"] if context["scope"]["method"] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER",
    }
    if reason is not None:
        event["reason"] = reason if type(reason) is str and reason in REASONS else "exception"
    if type(status) is int:
        event["status"] = status
    if elapsed is not None:
        event["elapsedMs"] = max(0, round(elapsed * 1000))
    if exc is not None:
        event["exceptions"] = exception_detail(exc)
    logging.getLogger("flamoris.studio").log(getattr(logging, level), "Studio diagnostic", extra={"diagnostic": event})
    try:
        context["store"].append(event, context.get("owner"))
    except (OSError, sqlite3.Error, ValueError):
        # Never turn a full/unwritable diagnostic disk into a failed generation.
        logging.getLogger("flamoris.studio").warning("Diagnostic persistence unavailable")


def transport_status(status):
    state = TRANSPORT.get()
    if state is not None:
        state["status"] = status


@contextmanager
def upstream(component, operation):
    started = time.monotonic()
    state = {"phase": "connect", "status": None}
    marker = TRANSPORT.set(state)
    try:
        yield state
    except Exception as exc:
        emit(component, operation, state["phase"], reason=getattr(exc, "code", "exception"),
             status=state["status"], elapsed=time.monotonic() - started, exc=exc)
        raise
    finally:
        TRANSPORT.reset(marker)


class DiagnosticsMiddleware:
    def __init__(self, app, store):
        self.app, self.store = app, store

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        context = {"id": uuid.uuid4().hex, "scope": scope, "store": self.store}
        scope["studio_diagnostics"] = context
        marker = CONTEXT.set(context)
        started, status, sent = time.monotonic(), 500, False
        async def observed(message):
            nonlocal status, sent
            if message["type"] == "http.response.start":
                status, sent = message["status"], True
                message = {**message, "headers": [
                    (k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"
                ] + [(b"x-request-id", context["id"].encode())]}
            await send(message)
        try:
            await self.app(scope, receive, observed)
        except Exception as exc:
            emit("studio", "request", "stream" if sent else "handler", reason="stream_interrupted" if sent else "exception",
                 status=status, elapsed=time.monotonic() - started, exc=exc)
            if sent:
                raise RuntimeError("Studio response stream interrupted; reference " + context["id"]) from None
            await JSONResponse({"error": "internal_error", "message": "Studio could not complete this request."}, status_code=500)(scope, receive, observed)
        finally:
            if status >= 400:
                emit("studio", "request", "response", reason="request_rejected", status=status,
                     elapsed=time.monotonic() - started, level="ERROR" if status >= 500 else "WARNING")
            CONTEXT.reset(marker)


def mount_diagnostics(app):
    from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        reasons = {
            "Image template unavailable; select a ready template": "image_template_unavailable",
            "Reference unavailable; replace or remove it": "reference_unavailable",
            "Reference changed; replace or remove it": "reference_unavailable",
            "Image parameters are unsupported": "image_parameters_unsupported",
            "Reference storage quota reached": "reference_quota",
            "Reference storage is busy": "reference_busy",
        }
        reason = reasons.get(exc.detail, "request_rejected") if type(exc.detail) is str else "request_rejected"
        emit("studio", "request", "validation" if exc.status_code == 422 else "handler",
             reason=reason, status=exc.status_code, level="WARNING" if exc.status_code < 500 else "ERROR")
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not serialize exc.errors(): it includes original input values.
        emit("studio", "request", "schema_validation", reason="validation", status=422, level="WARNING")
        return await request_validation_exception_handler(request, exc)

    class ClientFailure(BaseModel):
        model_config = ConfigDict(extra="forbid")
        kind: Literal["network_failure", "script_error", "unhandled_rejection", "http_failure"]
        status: int | None = Field(default=None, ge=400, le=599, strict=True)
        area: Literal["image", "video", "music", "speech", "assistant", "intelligence", "assets", "account", "other"] = "other"

    @app.get("/api/diagnostics/logs")
    async def logs(request: Request, requestId: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}$"),
                   level: Literal["WARNING", "ERROR"] | None = None,
                   component: Literal["studio", "generation", "agent", "intelligence", "browser"] | None = None,
                   before: int | None = Query(default=None, ge=1, le=2**53 - 1),
                   limit: int = Query(default=50, ge=1, le=100),
                   scope: Literal["mine", "all"] = "mine",
                   db=Depends(database), user_id=Depends(authenticated_user)):
        is_operator = operator(user_id)
        if scope == "all" and not is_operator:
            raise HTTPException(403)
        try:
            result = await asyncio.to_thread(app.state.diagnostics.read, user_id, all_accounts=scope == "all",
                                            request_id=requestId, level=level, component=component, before=before, limit=limit)
        except (OSError, sqlite3.Error, ValueError):
            raise HTTPException(503, "Diagnostic storage is unavailable") from None
        db.rollback()
        if current_user(request, db) != user_id or scope == "all" and not operator(user_id):
            raise HTTPException(403)
        return JSONResponse({**result, "canViewAll": is_operator, "scope": scope}, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

    @app.post("/api/diagnostics/client", status_code=204)
    async def client_failure(input: ClientFailure, user_id=Depends(authenticated_user)):
        # Bound noisy scripts per process without storing client-controlled text.
        now = time.monotonic()
        recent = app.state.diagnostic_client_reports
        for key in list(recent):
            if now - recent[key][0] >= 60:
                del recent[key]
        stamp, count = recent.get(user_id, (now, 0))
        if count >= 10 or user_id not in recent and len(recent) >= 4096:
            raise HTTPException(429)
        recent[user_id] = (stamp, count + 1)
        emit("browser", input.area, "reported", reason=input.kind, status=input.status)
