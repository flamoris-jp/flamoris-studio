import json
import sqlite3
import time
import uuid
from contextlib import asynccontextmanager
from unittest.mock import Mock

import httpx2
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from flamoris_studio import diagnostics as d
from flamoris_studio.app import create_app
from flamoris_studio.auth import authenticated_user, database
from flamoris_studio.gateway import GatewayError, GenerationGateway
from flamoris_studio.logging_setup import JsonFormatter
from flamoris_studio.media import Thumbnails

A, B = uuid.uuid4(), uuid.uuid4()


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.delenv('STUDIO_DIAGNOSTICS_OPERATORS', raising=False)
    app = create_app(session_factory=Mock(), thumbnails=Thumbnails(str(tmp_path)))
    identity = {'owner': A}
    def verified(request: d.Request):
        d.bind_owner(request, identity['owner'])
        return identity['owner']
    app.dependency_overrides[authenticated_user] = verified
    app.dependency_overrides[database] = lambda: Mock()
    monkeypatch.setattr(d, 'current_user', lambda request, db: identity['owner'])
    @app.get('/api/diagnostic-fixture/{private_id}')
    async def broken(user=Depends(authenticated_user)):
        raise RuntimeError('PRIVATE prompt, Bearer secret, /private/files')
    @app.get('/api/rejection-fixture')
    async def rejected(user=Depends(authenticated_user)):
        raise HTTPException(409, 'Image template unavailable; select a ready template')
    # No lifespan is needed for these isolated authenticated boundary checks.
    return app, identity, app.state.diagnostics


def test_error_id_privacy_account_scope_and_operator(boundary, monkeypatch, caplog):
    app, identity, store = boundary
    client = TestClient(app)
    failure = client.get('/api/diagnostic-fixture/SECRET-ID?prompt=QUERYSECRET', headers={'X-Request-ID': 'forged'})
    assert failure.status_code == 500
    reference = failure.headers['x-request-id']
    assert len(reference) == 32 and reference != 'forged'
    assert 'PRIVATE' not in failure.text
    page = client.get('/api/diagnostics/logs', params={'requestId': reference})
    assert page.status_code == 200
    events = page.json()['items']
    assert events and all(event['requestId'] == reference for event in events)
    encoded = json.dumps(events)
    for secret in ('PRIVATE', 'secret', '/private/files', 'SECRET-ID', 'QUERYSECRET'):
        assert secret not in encoded
    assert 'RuntimeError' in encoded and '/api/diagnostic-fixture/{private_id}' in encoded
    assert page.headers['cache-control'] == 'private, no-store'
    identity['owner'] = B
    assert client.get('/api/diagnostics/logs', params={'requestId': reference}).json()['items'] == []
    assert client.get('/api/diagnostics/logs?scope=all').status_code == 403
    monkeypatch.setenv('STUDIO_DIAGNOSTICS_OPERATORS', json.dumps([str(B)]))
    assert client.get('/api/diagnostics/logs', params={'scope': 'all', 'requestId': reference}).json()['items']
    monkeypatch.setenv('STUDIO_DIAGNOSTICS_OPERATORS', '["malformed"]')
    assert client.get('/api/diagnostics/logs?scope=all').status_code == 403


def test_rejection_and_strict_filters(boundary):
    app, _, _ = boundary
    client = TestClient(app)
    failure = client.get('/api/rejection-fixture')
    events = client.get('/api/diagnostics/logs', params={'requestId': failure.headers['x-request-id']}).json()['items']
    assert any(event.get('reason') == 'image_template_unavailable' for event in events)
    for query in ('limit=101', 'before=-1', 'requestId=../secret', 'level=DEBUG', 'component=arbitrary', 'scope=other'):
        assert client.get('/api/diagnostics/logs?' + query).status_code == 422


def test_storage_failure_cannot_change_error_response(boundary, monkeypatch):
    app, _, store = boundary
    monkeypatch.setattr(store, 'append', Mock(side_effect=sqlite3.OperationalError('PRIVATEDISK')))
    response = TestClient(app).get('/api/diagnostic-fixture/anything')
    assert response.status_code == 500
    assert 'PRIVATEDISK' not in response.text and 'x-request-id' in response.headers
    monkeypatch.setattr(store, 'read', Mock(side_effect=OSError('PRIVATEPATH')))
    response = TestClient(app).get('/api/diagnostics/logs')
    assert response.status_code == 503 and 'PRIVATEPATH' not in response.text


def test_retention_pagination_filters_and_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(d, 'MAX_EVENTS', 3)
    store = d.DiagnosticStore(str(tmp_path))
    def event(i):
        return {'requestId': f'{i:032x}', 'level': 'ERROR', 'component': 'generation', 'time': 'fixture'}
    for i in range(5):
        store.append(event(i), str(A))
    assert len(store.read(A)['items']) == 3
    first = store.read(A, limit=2)
    second = d.DiagnosticStore(str(tmp_path)).read(A, before=first['nextCursor'], limit=2)
    assert len(second['items']) == 1
    assert not {r['id'] for r in first['items']} & {r['id'] for r in second['items']}
    assert store.read(B)['items'] == []
    assert store.read(A, component='studio')['items'] == []
    assert len(store.read(A, request_id=f'{4:032x}')['items']) == 1
    with store.connect() as db:
        db.execute('UPDATE events SET stamp = ?', (time.time() - d.RETENTION_SECONDS - 1,))
    assert store.read(A)['items'] == []


def test_generation_retains_original_411_without_body_or_credentials(boundary, monkeypatch):
    app, _, store = boundary
    monkeypatch.setenv('STUDIO_GENERATION_ENDPOINT', 'http://127.0.0.1:8765/api/v1/generation')
    monkeypatch.setenv('STUDIO_GENERATION_TOKEN', 'PRIVATE-TOKEN-' * 4)
    @asynccontextmanager
    async def connection():
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _: httpx2.Response(411, text='PRIVATEBODY')), base_url='http://fixture/') as http:
            yield http
    gateway = GenerationGateway()
    monkeypatch.setattr(gateway, '_connection', connection)
    @app.get('/api/upstream-fixture')
    async def fail(user=Depends(authenticated_user)):
        await gateway._call('jobs.submit', {'prompt': 'PRIVATEPROMPT'})
    response = TestClient(app).get('/api/upstream-fixture')
    assert response.status_code == 502
    events = store.read(A, request_id=response.headers['x-request-id'])['items']
    upstream = next(event for event in events if event['component'] == 'generation')
    assert upstream['status'] == 411 and upstream['operation'] == 'jobs.submit'
    assert upstream['phase'] == 'response'
    assert all(value not in json.dumps(events) for value in ('PRIVATEBODY', 'PRIVATEPROMPT', 'PRIVATE-TOKEN'))


def test_context_isolation_and_browser_report_boundaries(boundary):
    app, _, store = boundary
    client = TestClient(app)
    # CSRF still applies to browser diagnostic writes.
    assert client.post('/api/diagnostics/client', json={'kind': 'script_error'}).status_code == 403
    from flamoris_studio.auth import CSRF_COOKIE
    csrf = 'a' * 43
    client.cookies.set(CSRF_COOKIE, csrf)
    headers = {'X-CSRF-TOKEN': csrf}
    assert client.post('/api/diagnostics/client', json={'kind': 'script_error', 'message': 'SECRET'}, headers=headers).status_code == 422
    for i in range(10):
        response = client.post('/api/diagnostics/client', json={'kind': 'script_error'}, headers=headers)
        assert response.status_code == 204
    assert client.post('/api/diagnostics/client', json={'kind': 'script_error'}, headers=headers).status_code == 429
    assert len(store.read(A, component='browser')['items']) == 10
    assert d.CONTEXT.get() is None

@pytest.mark.asyncio
async def test_agent_and_intelligence_original_status_and_private_exceptions(tmp_path):
    from types import SimpleNamespace
    import httpx
    from flamoris_studio.agent_gateway import AgentGateway, AgentError, CATALOG
    from flamoris_studio.intelligence_gateway import IntelligenceGateway
    store = d.DiagnosticStore(str(tmp_path))
    state = {'id': uuid.uuid4().hex, 'scope': {'method': 'POST', 'route': SimpleNamespace(path='/api/fixture')}, 'store': store, 'owner': str(A)}
    marker = d.CONTEXT.set(state)
    try:
        def agent(request):
            if request.url.path.endswith('/capabilities'):
                return httpx2.Response(200, json={'ok': True, 'api_version': 1, 'operations': sorted(CATALOG)})
            return httpx2.Response(503, text='PRIVATE-AGENT-BODY')
        gateway = AgentGateway('http://127.0.0.1:8000', 'a' * 32, transport_factory=lambda: httpx2.MockTransport(agent))
        with pytest.raises(AgentError):
            await gateway.call('health', {})
        pin = {'model_id': 'local', 'provider_id': 'llamacpp', 'context_tokens': 32768, 'max_output_tokens': 4096}
        intelligence = IntelligenceGateway('http://127.0.0.1:8001', transport_factory=lambda: httpx.MockTransport(lambda _: httpx.Response(429, text='PRIVATE-INFERENCE-BODY')))
        assert not (await intelligence.discover([pin]))['available']
    finally:
        d.CONTEXT.reset(marker)
    items = store.read(A)['items']
    assert any(x['component'] == 'agent' and x['status'] == 503 for x in items)
    assert any(x['component'] == 'intelligence' and x['status'] == 429 for x in items)
    assert 'PRIVATE-' not in json.dumps(items)


@pytest.mark.asyncio
async def test_concurrent_request_contexts_never_mix_accounts(boundary):
    import asyncio
    import httpx
    app, _, store = boundary
    async def authenticated(request: d.Request):
        owner = A if request.headers.get('test-account') == 'a' else B
        d.bind_owner(request, owner)
        return owner
    app.dependency_overrides[authenticated_user] = authenticated
    @app.get('/api/concurrent-fixture')
    async def concurrent(owner=Depends(authenticated_user)):
        await asyncio.sleep(0.01)
        raise HTTPException(409)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://fixture') as client:
        a, b = await asyncio.gather(client.get('/api/concurrent-fixture', headers={'test-account': 'a'}), client.get('/api/concurrent-fixture', headers={'test-account': 'b'}))
    assert a.headers['x-request-id'] != b.headers['x-request-id']
    for owner, response in ((A, a), (B, b)):
        assert {x['requestId'] for x in store.read(owner)['items']} == {response.headers['x-request-id']}
    assert d.CONTEXT.get() is None


def test_browser_observed_http_failure_without_server_header_can_be_correlated(boundary):
    from flamoris_studio.auth import CSRF_COOKIE
    app, _, store = boundary
    client = TestClient(app)
    csrf = 'b' * 43
    client.cookies.set(CSRF_COOKIE, csrf)
    response = client.post('/api/diagnostics/client', json={'kind': 'http_failure', 'status': 411, 'area': 'image'}, headers={'X-CSRF-TOKEN': csrf})
    assert response.status_code == 204
    event = store.read(A, request_id=response.headers['x-request-id'])['items'][0]
    assert event['reason'] == 'http_failure' and event['status'] == 411
    assert event['phase'] == 'reported'  # Report ID is not the original failed request.

# Exercise the same boundary with real Studio cookies/accounts on PostgreSQL CI.
from test_studio import clients as authenticated_clients, register


def test_real_account_cookie_cannot_read_another_accounts_reference(authenticated_clients):
    a, b, _, _ = authenticated_clients
    register(a, 'diagnostics-a@example.test')
    register(b, 'diagnostics-b@example.test')
    failure = a.get('/api/assets/' + str(uuid.uuid4()))
    assert failure.status_code == 404
    reference = failure.headers['x-request-id']
    assert a.get('/api/diagnostics/logs', params={'requestId': reference}).json()['items']
    assert b.get('/api/diagnostics/logs', params={'requestId': reference}).json()['items'] == []
    assert b.get('/api/diagnostics/logs?scope=all').status_code == 403
