import base64
import json

import pytest
from fastapi.testclient import TestClient

from sirius_vision.auth import hash_password
from sirius_vision.config import Settings
from sirius_vision.main import create_app
from sirius_vision.provider import Completion
from sirius_vision.templates import TEMPLATES

PNG = base64.b64encode(b'\x89PNG\r\n\x1a\n' + b'example image').decode()
FIELDS = dict(avatar_type='real_person', gender_feel='unclear', age_feel='young',
              face_view='frontal', style_tags=['casual'], face_ratio=0.5,
              clarity='high', occlusion='none', risk_flags=['none'],
              confidence={'avatar_type': 0.9})

class Fake:
    def __init__(self, outputs=None):
        self.outputs = outputs or [json.dumps(FIELDS)]
        self.prompts = []

    async def complete(self, prompt, image, preferred_model=None):
        self.prompts.append(prompt)
        return Completion(self.outputs[min(len(self.prompts)-1, len(self.outputs)-1)],
                          'test', 'test-model', {'total_tokens': 12}, 10)

@pytest.fixture
def setup(tmp_path):
    config = Settings(root=tmp_path, api_keys=('client',), admin_api_keys=('manager',),
                      admin_password_hash=hash_password('passphrase'), session_secret='s' * 40)
    adapter = Fake()
    app = create_app(config, adapter)
    with TestClient(app, base_url='https://testserver') as client:
        yield client, app, adapter


def analyze(client):
    return client.post('/v1/analyze', headers={'X-API-Key':'client'},
                       json={'task':'avatar_tag','image_base64':PNG})


def login(client):
    response = client.post('/admin/login', json={'password':'passphrase'},
                           headers={'Origin':'https://testserver'})
    assert response.status_code == 200
    return response.json()['csrf_token']


def test_templates():
    template = TEMPLATES['avatar_tag']
    assert template.parse('<think>{"secret": 1}</think>```json\n' + json.dumps(FIELDS) + '\n```') == FIELDS
    for raw in ['{}', 'not json', json.dumps({**FIELDS, 'face_ratio': 2})]:
        with pytest.raises(ValueError):
            template.parse(raw)
    garment = dict(category='shirt', color=['blue'], fit='regular', sleeve_length='long',
                   neckline='round', pattern='solid', occasions=['casual'])
    assert TEMPLATES['garment_attr'].parse(json.dumps(garment)) == garment


def test_auth_chain_records_stats(setup):
    client, app, adapter = setup
    assert client.get('/v1/tasks').status_code == 401
    assert client.get('/v1/tasks', headers={'X-API-Key':'client'}).status_code == 200
    result = analyze(client)
    assert result.status_code == 200, result.text
    record_id = result.json()['id']
    record = app.state.storage.get(record_id)
    assert record['parsed_fields'] == FIELDS
    assert record['tokens'] == 12
    assert (app.state.settings.root / record['input_image']).exists()
    assert client.get('/v1/records', headers={'X-API-Key':'client'}).status_code == 403
    response = client.get('/v1/records', headers={'X-API-Key':'manager'})
    assert response.json()['items'][0]['id'] == record_id
    assert client.get('/v1/stats', headers={'X-API-Key':'client'}).json()['calls'] == 1


def test_parse_retry_preserves_raw(setup):
    client, app, adapter = setup
    adapter.outputs = ['<think>private</think>bad']
    response = analyze(client)
    assert response.status_code == 502
    assert len(adapter.prompts) == 2
    assert adapter.prompts[-1].endswith('Output ONLY the JSON object, nothing else.')
    record = app.state.storage.list_records()['items'][0]
    assert record['status'] == 'parse_error'
    assert record['raw_output'] == adapter.outputs[0]
    assert record['tokens'] == 24


@pytest.mark.parametrize('url', ['http://127.0.0.1/a', 'http://169.254.169.254/a',
                                'file:///etc/passwd', 'http://[::1]/a'])
def test_ssrf(setup, url):
    client, _, adapter = setup
    result = client.post('/v1/analyze', headers={'X-API-Key':'client'},
                         json={'task':'avatar_tag','image_url':url})
    assert result.status_code == 400
    assert not adapter.prompts


def test_admin_review_csrf(setup):
    client, app, _ = setup
    rid = analyze(client).json()['id']
    assert client.get('/admin/records', follow_redirects=False).status_code == 307
    assert client.get(f'/admin/records/{rid}/image', follow_redirects=False).status_code == 307
    assert client.post('/admin/login', json={'password':'passphrase'},
                       headers={'Origin':'https://evil.example'}).status_code == 403
    token = login(client)
    cookie = client.cookies.get('vision_session')
    assert cookie
    url = f'/admin/records/{rid}/review'
    assert client.patch(url, json={'review':'correct'}).status_code == 403
    headers = {'X-CSRF-Token': token, 'Origin':'https://testserver'}
    assert client.patch(url, headers=headers, json={'review':'correct','review_note':'checked'}).status_code == 200
    assert app.state.storage.get(rid)['review_note'] == 'checked'
    assert client.get(f'/admin/records/{rid}/image').status_code == 200
    assert client.post('/admin/logout', headers=headers).status_code == 200
    assert client.get('/admin/records', follow_redirects=False).status_code == 307


def test_white_label_validation(setup):
    client, _, _ = setup
    response = client.post('/v1/analyze', headers={'X-API-Key':'client'},
                           json={'task':'minimax-kimi-glm-bigmodel','image_url':'file:///internal'})
    assert response.status_code == 422
    assert not any(word in response.text.lower() for word in ['minimax','kimi','glm','bigmodel','internal'])
