from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from flamoris_studio.app import create_app
from flamoris_studio.auth import DUMMY_PASSWORD_HASH, digest, hasher as account_hasher
from flamoris_studio.db import LoginThrottle, now
from flamoris_studio.login_throttle import admit_login
from flamoris_studio.server import trusted_proxy_ips
from test_studio import clients, register


@pytest.mark.parametrize("value", ["*", "proxy.internal", "127.0.0.1,*", "invalid"])
def test_proxy_configuration_rejects_implicit_trust(value):
    with pytest.raises(ValueError):
        trusted_proxy_ips(value)


def test_dummy_hash_is_valid_and_matches_account_hash_parameters():
    assert account_hasher.verify(DUMMY_PASSWORD_HASH, "studio-dummy-no-account-login-verification")
    assert account_hasher.check_needs_rehash(DUMMY_PASSWORD_HASH) is False


@pytest.mark.asyncio
async def test_forwarded_headers_only_change_clients_from_configured_proxy():
    scopes = []

    async def app(scope, receive, send):
        scopes.append(scope)

    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted_proxy_ips("127.0.0.1,::1"))
    for address in ("198.51.100.9", "127.0.0.1"):
        scope = {"type": "http", "scheme": "http", "client": (address, 1234),
                 "headers": [(b"x-forwarded-for", b"203.0.113.4"),
                             (b"x-forwarded-proto", b"https")]}
        await middleware(scope, None, None)
    assert scopes[0]["client"] == ("198.51.100.9", 1234)
    assert scopes[0]["scheme"] == "http"
    assert scopes[1]["client"][0] == "203.0.113.4" and scopes[1]["scheme"] == "https"


@pytest.mark.asyncio
async def test_configured_cidr_is_normalized_for_actual_proxy_middleware():
    observed = []

    async def app(scope, receive, send):
        observed.append(scope)

    trusted = trusted_proxy_ips("192.0.2.7/24")
    assert trusted == "192.0.2.0/24"
    middleware = ProxyHeadersMiddleware(app, trusted_hosts=trusted)
    await middleware({"type": "http", "scheme": "http", "client": ("192.0.2.20", 1234),
                      "headers": [(b"x-forwarded-for", b"203.0.113.4"),
                                  (b"x-forwarded-proto", b"https")]}, None, None)
    assert observed[0]["client"][0] == "203.0.113.4"
    assert observed[0]["scheme"] == "https"


def test_unknown_known_and_locked_passwords_each_verify_once(clients, monkeypatch):
    a, b, _, _ = clients
    register(a, "known@example.test")
    csrf = b.get("/api/session").json()["csrfToken"]
    calls = []

    def reject(encoded, password):
        calls.append(encoded)
        return account_hasher.verify(encoded, password)

    monkeypatch.setattr("flamoris_studio.app.hasher", SimpleNamespace(verify=reject))
    for address in ["missing@example.test"] + ["known@example.test"] * 6:
        response = b.post("/api/auth/login", json={"email": address, "password": "Wrong-Password-123"},
                          headers={"X-CSRF-TOKEN": csrf})
        assert response.status_code == 401 and response.json() == {"detail": "Unauthorized"}
    assert len(calls) == 7 and calls[0] == DUMMY_PASSWORD_HASH
    assert all(value != DUMMY_PASSWORD_HASH for value in calls[1:])


def test_login_admission_survives_new_app_and_ignores_untrusted_headers(clients, monkeypatch):
    a, _, gateway, factory = clients
    monkeypatch.setattr("flamoris_studio.app.hasher", SimpleNamespace(verify=lambda *args: False))
    csrf = a.get("/api/session").json()["csrfToken"]
    credentials = {"email": "missing@example.test", "password": "Wrong-Password-123"}
    for index in range(10):
        assert a.post("/api/auth/login", json=credentials,
                      headers={"X-CSRF-TOKEN": csrf, "X-Forwarded-For": f"203.0.113.{index}"}).status_code == 401
    with TestClient(create_app(factory, gateway, a.app.state.thumbnails)) as restarted:
        new_csrf = restarted.get("/api/session").json()["csrfToken"]
        assert restarted.post("/api/auth/login", json=credentials,
                              headers={"X-CSRF-TOKEN": new_csrf}).status_code == 429
    with factory() as db:
        rows = db.scalars(select(LoginThrottle)).all()
        assert len(rows) == 1 and rows[0].address_hash == digest("testclient")
        assert rows[0].attempts == 10


def test_capacity_pressure_keeps_other_active_protection(clients, monkeypatch):
    _, _, _, factory = clients
    monkeypatch.setattr("flamoris_studio.login_throttle.MAX_ADDRESSES", 2)
    with factory() as db:
        admit_login(db, "client-a")
        admit_login(db, "client-b")
        with pytest.raises(HTTPException) as denied:
            admit_login(db, "client-c")
        assert denied.value.status_code == 429
        db.rollback()
        assert db.get(LoginThrottle, digest("client-a")).attempts == 1
        assert db.get(LoginThrottle, digest("client-b")).attempts == 1
        db.get(LoginThrottle, digest("client-a")).expires_at = now() - timedelta(seconds=1)
        db.commit()
        admit_login(db, "client-c")
        assert db.get(LoginThrottle, digest("client-b")).attempts == 1


def test_concurrent_workers_cannot_exceed_shared_address_limit(clients):
    _, _, _, factory = clients

    def attempt(index):
        with factory() as db:
            try:
                admit_login(db, "concurrent-client")
                return True
            except HTTPException as exc:
                assert exc.status_code == 429
                return False

    with ThreadPoolExecutor(max_workers=6) as pool:
        accepted = sum(pool.map(attempt, range(30)))
    with factory() as db:
        assert 1 <= accepted <= 10
        assert db.get(LoginThrottle, digest("concurrent-client")).attempts == accepted


def test_addressed_expired_window_resets_outside_cleanup_batch(clients):
    _, _, _, factory = clients
    with factory() as db:
        for index in range(256):
            db.add(LoginThrottle(address_hash=digest(f"expired-{index}"), attempts=10,
                                 expires_at=now() - timedelta(seconds=1)))
        db.commit()
        # PostgreSQL's bounded deletion has no promised order. Select a row
        # beyond the exact current first 128, then ensure its own admission works.
        excluded = set(db.scalars(select(LoginThrottle.address_hash).limit(128)))
        address = next(f"expired-{index}" for index in range(256)
                       if digest(f"expired-{index}") not in excluded)
        db.rollback()
        admit_login(db, address)
        assert db.get(LoginThrottle, digest(address)).attempts == 1
