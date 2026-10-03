"""Durable owner-scoped request fences; Generation remains the job authority."""
import uuid

from fastapi import HTTPException

from .db import Execution, GenerationRequestRecord


def existing_request(db, user, request_id, request_digest=None, operation=None):
    record = db.get(GenerationRequestRecord, (user, request_id))
    if record is None:
        return None
    execution = db.get(Execution, record.execution_id)
    if execution is None or execution.user_id != user:
        raise HTTPException(404)
    if (request_digest is not None and record.request_digest != request_digest or
            operation is not None and execution.operation != operation):
        raise HTTPException(409, "This request identifier already belongs to another generation request")
    return execution
