import hashlib
import io
import json
from urllib.parse import urlsplit

import pytest
from PIL import Image

from sirius_vision.config import Settings
from test_translate import call, service  # noqa: F401


def encoded_image(format_name: str) -> bytes:
    image = Image.new('RGB', (32, 24), '#204060')
    output = io.BytesIO()
    image.save(output, format=format_name)
    return output.getvalue()


def test_public_output_base_requires_https_and_normalizes_trailing_slash():
    assert Settings(public_base_url='https://example.test/vision/').public_base_url == 'https://example.test/vision'
    with pytest.raises(ValueError, match='absolute HTTPS'):
        Settings(public_base_url='http://example.test/vision')


def attach_output(app, record_id: str, data: bytes, ext: str) -> str:
    digest, relative = app.state.storage.save_image(data, ext)
    record = app.state.storage.get(record_id)
    parsed = record['parsed_fields'] or {}
    parsed['render'] = {
        'mode': 'auto', 'engine': 'test', 'output_image_id': digest,
        'output_url': f'https://101.37.175.27/vision/v1/outputs/{digest}',
    }
    with app.state.storage.connect() as conn:
        conn.execute('UPDATE records SET output_image=?, parsed_fields=? WHERE id=?',
                     (relative, json.dumps(parsed), record_id))
    return digest


@pytest.mark.parametrize('format_name,ext,mime', [
    ('JPEG', 'jpg', 'image/jpeg'),
    ('PNG', 'png', 'image/png'),
    ('WEBP', 'webp', 'image/webp'),
])
def test_authenticated_output_download_formats_and_headers(service, format_name, ext, mime):
    client, app, _ = service
    record_id = call(client, 'none').json()['id']
    data = encoded_image(format_name)
    digest = attach_output(app, record_id, data, ext)

    response = client.get(f'/v1/outputs/{digest}', headers={'X-API-Key': 'client'})

    assert response.status_code == 200
    assert response.content == data
    assert response.headers['content-type'] == mime
    assert response.headers['content-length'] == str(len(data))
    assert response.headers['etag'] == f'"{digest}"'
    assert response.headers['cache-control'] == 'private, max-age=86400'
    assert hashlib.sha256(response.content).hexdigest() == digest
    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (32, 24)


@pytest.mark.parametrize('headers', [{}, {'X-API-Key': 'wrong'}])
def test_output_download_requires_valid_key(service, headers):
    client, app, _ = service
    record_id = call(client, 'none').json()['id']
    digest = attach_output(app, record_id, encoded_image('PNG'), 'png')
    assert client.get(f'/v1/outputs/{digest}', headers=headers).status_code == 401


def test_output_download_rejects_missing_invalid_and_input_ids(service):
    client, app, _ = service
    record_id = call(client, 'none').json()['id']
    input_id = app.state.storage.get(record_id)['input_sha256']
    headers = {'X-API-Key': 'client'}

    assert client.get('/v1/outputs/' + 'f' * 64, headers=headers).status_code == 404
    assert client.get('/v1/outputs/not-a-sha256', headers=headers).status_code in (400, 422)
    assert client.get(f'/v1/outputs/{input_id}', headers=headers).status_code == 404


@pytest.mark.parametrize('path', [
    '/v1/outputs/../vision.sqlite3',
    '/v1/outputs/%2e%2e%2fvision.sqlite3',
    '/v1/outputs/' + ('a' * 64) + '%2f..%2fvision.sqlite3',
])
def test_output_download_blocks_directory_traversal(service, path):
    client, _, _ = service
    response = client.get(path, headers={'X-API-Key': 'client'})
    assert response.status_code in (400, 404, 422)
    assert response.headers.get('content-type', '').startswith('application/json')


@pytest.mark.parametrize('data,ext,status,error', [
    (b'', 'png', 422, 'empty'),
    (b'not an image', 'png', 422, 'invalid'),
    (b'\x89PNG\r\n\x1a\n' + b'broken', 'png', 422, 'invalid'),
    (b'GIF89a' + b'broken', 'gif', 415, 'not supported'),
])
def test_output_download_reports_invalid_files(service, data, ext, status, error):
    client, app, _ = service
    record_id = call(client, 'none').json()['id']
    digest = attach_output(app, record_id, data, ext)
    response = client.get(f'/v1/outputs/{digest}', headers={'X-API-Key': 'client'})
    assert response.status_code == status
    assert error in response.json()['error'].lower()


def test_output_download_rejects_checksum_mismatch(service):
    client, app, _ = service
    record_id = call(client, 'none').json()['id']
    digest = attach_output(app, record_id, encoded_image('PNG'), 'png')
    path = app.state.storage.output_path(digest)
    path.write_bytes(encoded_image('JPEG'))

    response = client.get(f'/v1/outputs/{digest}', headers={'X-API-Key': 'client'})
    assert response.status_code == 422
    assert 'checksum' in response.json()['error'].lower()


def test_analyze_returns_compatible_output_id_and_absolute_https_url(service):
    client, _, _ = service
    response = call(client, 'pil')
    assert response.status_code == 200, response.text
    render = response.json()['render']
    digest = render['output_image_id']
    assert len(digest) == 64
    assert render['output_url'] == f'https://101.37.175.27/vision/v1/outputs/{digest}'

    # TestClient has no nginx prefix-stripping layer, so call the upstream path.
    upstream_path = urlsplit(render['output_url']).path.removeprefix('/vision')
    downloaded = client.get(upstream_path, headers={'X-API-Key': 'client'})
    assert downloaded.status_code == 200
    assert hashlib.sha256(downloaded.content).hexdigest() == digest
    assert downloaded.headers['content-type'].startswith('image/')


def test_analyze_without_rendered_image_has_empty_output_url(service):
    client, _, _ = service
    render = call(client, 'none').json()['render']
    assert render['output_image_id'] is None
    assert render.get('output_url', '') == ''
