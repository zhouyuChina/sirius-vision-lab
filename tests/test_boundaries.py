import json
import socket
from unittest.mock import AsyncMock

import httpx
import pytest

from sirius_vision.auth import issue_session, read_session
from sirius_vision.config import ProviderConfig, Settings
from sirius_vision.images import ImageError, decode_image, fetch_image, image_type
from sirius_vision.provider import ChatAdapter, ProviderError
from test_core import FIELDS, PNG, Fake, analyze, login, setup


@pytest.mark.parametrize('name', ['minimax', 'kimi', 'glm'])
async def test_provider_payload_and_usage(name):
    seen = []
    async def handler(request):
        seen.append(json.loads(request.content))
        assert request.url == 'https://provider.invalid/v1/chat/completions'
        assert request.headers['authorization'] == 'Bearer key'
        message = {'content': json.dumps(FIELDS)} if name != 'glm' else {'content':'', 'reasoning_content':'reasoning\n' + json.dumps(FIELDS)}
        return httpx.Response(200, json={'choices':[{'message':message}], 'usage':{'total_tokens':7}})
    settings = Settings(provider=name, providers={name:ProviderConfig(name, 'https://provider.invalid/v1', 'key', 'model')})
    result = await ChatAdapter(settings, httpx.MockTransport(handler)).complete('prompt', 'data:image/png;base64,AA==')
    assert result.usage == {'total_tokens':7}
    assert result.latency_ms >= 0
    assert seen[0]['messages'][0]['content'][1]['image_url']['url'].startswith('data:')
    assert FIELDS == __import__('sirius_vision.templates', fromlist=['TEMPLATES']).TEMPLATES['avatar_tag'].parse(result.content)


@pytest.mark.parametrize('failure', ['429', 'timeout', '500', 'malformed'])
async def test_provider_retry_bounds(failure, monkeypatch):
    attempts = []
    monkeypatch.setattr('sirius_vision.provider.asyncio.sleep', AsyncMock())
    def handler(request):
        attempts.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('private provider details')
        return httpx.Response(int(failure) if failure.isdigit() else 200, json={'private':'bad'})
    settings = Settings(providers={'minimax':ProviderConfig('minimax','https://provider.invalid','key','model')})
    with pytest.raises(ProviderError):
        await ChatAdapter(settings, httpx.MockTransport(handler)).complete('p', 'data:')
    assert len(attempts) == (2 if failure in ('429', 'timeout') else 1)


async def test_dns_mixed_private_rejected(monkeypatch):
    loop = __import__('asyncio').get_running_loop()
    monkeypatch.setattr(loop, 'getaddrinfo', AsyncMock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8',80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1',80))]))
    with pytest.raises(ImageError):
        await fetch_image('http://example.org/image')


async def test_image_fetch_pinned_and_redirect_rejected(monkeypatch):
    loop = __import__('asyncio').get_running_loop()
    monkeypatch.setattr(loop, 'getaddrinfo', AsyncMock(return_value=[
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8',443))]))
    seen = []
    def handler(request):
        seen.append(request)
        assert request.url.host == '8.8.8.8'
        assert request.headers['host'] == 'example.org'
        assert request.extensions['sni_hostname'] == 'example.org'
        return httpx.Response(302, headers={'location':'http://127.0.0.1/private'})
    original = httpx.AsyncClient
    monkeypatch.setattr('sirius_vision.images.httpx.AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    with pytest.raises(ImageError):
        await fetch_image('https://example.org/image')
    assert len(seen) == 1


def test_image_magic_and_size(monkeypatch):
    for value in ['no base64!', 'data:text/plain,abc', 'aGVsbG8=']:
        with pytest.raises(ImageError):
            decode_image(value)
    monkeypatch.setattr('sirius_vision.images.MAX_BYTES', 10)
    with pytest.raises(ImageError):
        image_type(b'\x89PNG\r\n\x1a\n' + b'x'*10)
    for data, ext in [(b'\xff\xd8\xff','jpg'),(b'GIF89a','gif'),(b'BM','bmp'),(b'RIFF1234WEBP','webp')]:
        if len(data) <= 10:
            assert image_type(data)[0] == ext


def test_session_expiry_tampering_and_cookie(setup):
    client, app, _ = setup
    secret = app.state.settings.session_secret
    token, _ = issue_session(secret, -1)
    assert read_session(token, secret) is None
    token, _ = issue_session(secret, 60)
    assert read_session(token+'x', secret) is None
    response = client.post('/admin/login', json={'password':'passphrase'}, headers={'Origin':'https://testserver'})
    cookie = response.headers['set-cookie'].lower()
    assert 'secure' in cookie and 'httponly' in cookie and 'samesite=strict' in cookie
    client.cookies.set('vision_session', issue_session(secret, -1)[0], domain='testserver.local', path='/admin')
    assert client.get('/admin/records', follow_redirects=False).status_code == 307


def test_cursor_filters_and_issued_keys(setup):
    client, app, _ = setup
    ids = {analyze(client).json()['id'] for _ in range(3)}
    storage = app.state.storage
    with storage.connect() as conn:
        conn.execute("UPDATE records SET created_at='2026-10-08T00:00:00+00:00'")
    first = storage.list_records(limit=2)
    second = storage.list_records(limit=2, cursor=first['next_cursor'])
    assert {r['id'] for r in first['items'] + second['items']} == ids
    assert not second['next_cursor']
    assert storage.list_records(task='garment_attr')['items'] == []
    key = storage.issue_key('test', 'admin')
    assert client.get('/v1/records', headers={'X-API-Key':key}).status_code == 200
    with storage.connect() as conn:
        conn.execute('UPDATE api_keys SET revoked=1')
    assert client.get('/v1/records', headers={'X-API-Key':key}).status_code == 401


def test_parse_retry_recovers_and_provider_error_white_label(setup):
    client, app, adapter = setup
    adapter.outputs = ['broken', json.dumps(FIELDS)]
    assert analyze(client).status_code == 200
    assert app.state.storage.list_records()['items'][0]['tokens'] == 24
    class Broken:
        async def complete(self, *args):
            raise ProviderError('minimax kimi glm bigmodel /secret/path')
    app.state.adapter = Broken()
    response = analyze(client)
    assert response.status_code == 502
    assert not any(x in response.text for x in ['minimax','kimi','glm','bigmodel','secret'])
    assert app.state.storage.get(response.json()['id'])['status'] == 'provider_error'


def test_admin_static_paths(setup):
    client, _, _ = setup
    login(client)
    response = client.get('/admin')
    assert str(response.url).endswith('/admin/')
    assert client.get('/admin/static/app.js').status_code == 200


def test_logout_invalidates_copied_session(setup):
    client, _, _ = setup
    token = login(client)
    copied = client.cookies.get('vision_session')
    client.post('/admin/logout', headers={'Origin':'https://testserver', 'X-CSRF-Token':token})
    client.cookies.set('vision_session', copied, path='/admin')
    assert client.get('/admin/records', follow_redirects=False).status_code == 307


def test_nonfinite_output_retries_and_records_parse_error(setup):
    client, app, adapter = setup
    adapter.outputs = [json.dumps({**FIELDS, 'face_ratio': float('nan')})]
    response = analyze(client)
    assert response.status_code == 502
    assert app.state.storage.get(response.json()['id'])['status'] == 'parse_error'


async def test_reasoning_uses_final_object():
    from sirius_vision.templates import TEMPLATES
    final = {**FIELDS, 'avatar_type':'cartoon'}
    text = 'Draft: ' + json.dumps(FIELDS) + '\nFinal: ' + json.dumps(final)
    def handler(request):
        return httpx.Response(200, json={'choices':[{'message':{'reasoning_content':text}}]})
    settings = Settings(providers={'minimax':ProviderConfig('minimax','https://provider.invalid','key','model')})
    result = await ChatAdapter(settings, httpx.MockTransport(handler)).complete('p','image')
    assert TEMPLATES['avatar_tag'].parse(result.content) == final


@pytest.mark.parametrize('payload', [
    {'choices':[{'message':[]}]},
    {'choices':[{'message':{'content':'{}'}}], 'usage':[1]},
])
async def test_invalid_provider_containers_normalized(payload):
    settings = Settings(providers={'minimax':ProviderConfig('minimax','https://provider.invalid','key','model')})
    with pytest.raises(ProviderError):
        await ChatAdapter(settings, httpx.MockTransport(lambda r: httpx.Response(200,json=payload))).complete('p','image')


def test_console_url_resolution():
    import shutil
    import subprocess
    from pathlib import Path
    if not shutil.which('node'):
        pytest.skip('Node is optional for standalone JavaScript URL regression')
    source = Path('src/sirius_vision/static/admin/app.js').read_text()
    declaration = next(line for line in source.splitlines() if line.startswith('const adminBase'))
    for path in ['/admin', '/admin/', '/admin/login', '/vision/admin/', '/vision/admin/login']:
        expected = 'https://example.org' + path.split('/admin')[0] + '/admin/login'
        script = f'const location = new URL({json.dumps("https://example.org" + path)});\n' + declaration + '\nprocess.stdout.write(new URL("login", adminBase).href);'
        result = subprocess.run(['node','-e',script], capture_output=True, text=True, check=True)
        assert result.stdout == expected


def test_proxy_prefix_session_cookie(setup):
    from fastapi.testclient import TestClient
    _, app, _ = setup
    with TestClient(app, base_url='https://testserver/vision', root_path='/vision') as client:
        response = client.post('/admin/login', json={'password':'passphrase'}, headers={'Origin':'https://testserver'})
        assert response.status_code == 200
        assert 'Path=/vision/admin' in response.headers['set-cookie']
        assert client.get('/admin/records', follow_redirects=False).status_code == 200
        token = response.json()['csrf_token']
        response = client.post('/admin/logout', headers={'Origin':'https://testserver','X-CSRF-Token':token})
        assert 'Path=/vision/admin' in response.headers['set-cookie']
