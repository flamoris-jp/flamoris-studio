"""PostgreSQL admission shared by workers/restarts; active protection is never evicted."""

from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import delete, func, select, text

from .auth import digest
from .db import LoginThrottle

MAX_ADDRESSES = 10_000
WINDOW_SECONDS = 300
MAX_ATTEMPTS = 10
LOCK_ID = 0x5354554C4F47494E


def admit_login(db, address: str):
    # Serialize only a short metadata transaction; no password verification is
    # performed under the global lock. Capacity pressure fails closed rather
    # than forgetting other clients' attempts.
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_ID}):
        raise HTTPException(429, headers={"Retry-After": "2"})
    instant = db.scalar(text("SELECT clock_timestamp()"))
    expired = select(LoginThrottle.address_hash).where(
        LoginThrottle.expires_at <= instant).limit(128)
    db.execute(delete(LoginThrottle).where(LoginThrottle.address_hash.in_(expired)))
    key = digest(address)
    bucket = db.get(LoginThrottle, key)
    if bucket is None:
        if db.scalar(select(func.count()).select_from(LoginThrottle)) >= MAX_ADDRESSES:
            raise HTTPException(429, headers={"Retry-After": str(WINDOW_SECONDS)})
        bucket = LoginThrottle(address_hash=key, attempts=0,
                               expires_at=instant + timedelta(seconds=WINDOW_SECONDS))
        db.add(bucket)
    elif bucket.expires_at <= instant:
        # The addressed row may fall outside the bounded cleanup batch. Reset
        # that expired window directly so its own request can restore admission.
        bucket.attempts = 0
        bucket.expires_at = instant + timedelta(seconds=WINDOW_SECONDS)
    if bucket.attempts >= MAX_ATTEMPTS:
        raise HTTPException(429, headers={"Retry-After": str(max(
            1, int((bucket.expires_at - instant).total_seconds()) + 1))})
    bucket.attempts += 1
    db.commit()
