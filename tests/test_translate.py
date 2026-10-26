import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from sirius_vision.auth import hash_password
from sirius_vision.config import ProviderConfig, Settings
from sirius_vision.main import create_app
from sirius_vision.provider import ChatAdapter, Completion
from sirius_vision.templates import TEMPLATES


def png():
    image = Image.new('RGB', (200, 100), '#334455')
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


BAND = {'id': 1, 'text': '促销', 'position': 'top', 'band_bbox': [100, 100, 900, 400],
        'bg_hex': '#DE252D', 'text_hex': '#FFFFFF', 'background_type': 'solid_band'}
COMPLEX = {'id': 2, 'text': '新品', 'position': 'shirt', 'band_bbox': None,
           'bg_hex': None, 'text_hex': '#FFFFFF', 'background_type': 'complex'}


class Fake:
    def __init__(self, items=None, translations=None):
        self.items = items if items is not None else [BAND]
        self.raw_vision = None
        self.translations = translations if translations is not None else ['{"translations":[{"id":1,"ru":"SALE"}]}']
        self.text_calls = []
        self.vision_calls = []

    async def complete(self, prompt, image, preferred_model=None):
        self.vision_calls.append((prompt, image, preferred_model))
        return Completion(self.raw_vision or json.dumps({'items': self.items}), 'fake', 'vision', {'total_tokens': 3}, 1)

    async def complete_text(self, prompt, preferred_model=None):
        self.text_calls.append((prompt, preferred_model))
        raw = self.translations[min(len(self.text_calls)-1, len(self.translations)-1)]
        return Completion(raw, 'fake', 'qwen3.8-flash', {'total_tokens': 4}, 1)


@pytest.fixture
def service(tmp_path):
    settings = Settings(root=tmp_path, api_keys=('client',), admin_api_keys=('admin',),
                        admin_password_hash=hash_password('passphrase'), session_secret='s'*40,
                        translate_model='qwen3.8-flash')
    fake = Fake()
    app = create_app(settings, fake)
    with TestClient(app, base_url='https://testserver') as client:
        yield client, app, fake


def call(client, mode='none', target='ru'):
    return client.post('/v1/analyze', headers={'X-API-Key': 'client'}, json={
        'task': 'image_translate', 'image_base64': base64.b64encode(png()).decode(),
        'options': {'target_lang': target, 'render': mode}})


def test_template_schema_and_four_existing_tasks():
    assert set(TEMPLATES) == {'avatar_tag', 'garment_attr', 'brand_risk', 'brand_risk_style', 'image_translate'}
    assert TEMPLATES['image_translate'].version == '0.1.0'
    assert TEMPLATES['image_translate'].parse(json.dumps({'items': [BAND, COMPLEX]}))['items'][1]['band_bbox'] is None
    with pytest.raises(ValueError):
        TEMPLATES['image_translate'].parse(json.dumps({'items': [{**BAND, 'band_bbox': [0, 0, 1001, 5]}]}))


@pytest.mark.parametrize('task,fields', [
    ('avatar_tag', {'avatar_type':'real_person','gender_feel':'unclear','age_feel':'young',
                    'face_view':'frontal','style_tags':['casual'],'face_ratio':0.5,
                    'clarity':'high','occlusion':'none','risk_flags':['none'],
                    'confidence':{'avatar_type':0.9}}),
    ('garment_attr', {'category':'shirt','color':['blue'],'fit':'regular','sleeve_length':'long',
                      'neckline':'round','pattern':'solid','occasions':['casual']}),
    ('brand_risk', {'brand_or_ip':None,'evidence_type':'none','element_position':'none',
                    'confidence':0.8,'note':'No mark visible'}),
    ('brand_risk_style', {'card_brand':'example','matched_elements':[],
                          'match_count':0,'style_confidence':0.1,'note':'No match'}),
])
def test_existing_tasks_analyze_regression(service, task, fields):
    client, app, fake = service
    fake.raw_vision = json.dumps(fields)
    response = client.post('/v1/analyze', headers={'X-API-Key': 'client'}, json={
        'task': task, 'image_base64': base64.b64encode(png()).decode()})
    assert response.status_code == 200, response.text
    assert app.state.storage.get(response.json()['id'])['parsed_fields'] == fields
    assert fake.text_calls == []


def test_three_item_extraction_and_translation(service):
    client, _, fake = service
    third = {**BAND, 'id': 3, 'text': '包邮', 'position': 'bottom'}
    fake.items = [BAND, COMPLEX, third]
    fake.translations = [json.dumps({'translations': [
        {'id': 1, 'ru': 'SALE'}, {'id': 2, 'ru': 'NEW'}, {'id': 3, 'ru': 'FREE DELIVERY'}]})]
    response = call(client, 'none')
    assert response.status_code == 200, response.text
    assert [item['id'] for item in response.json()['segments']] == [1, 2, 3]
    assert len(fake.vision_calls) == 1 and len(fake.text_calls) == 1


def test_storage_additive_migration_preserves_records(service):
    from sirius_vision.storage import Storage
    client, app, _ = service
    record_id = call(client, 'none').json()['id']
    with app.state.storage.connect() as conn:
        conn.execute('ALTER TABLE records DROP COLUMN output_image')
        conn.execute('ALTER TABLE records DROP COLUMN error_code')
    restored = Storage(app.state.settings.root)
    assert restored.get(record_id)['id'] == record_id
    assert restored.get(record_id)['output_image'] is None
    assert restored.get(record_id)['error_code'] is None


def test_none_pipeline_persists_and_admin_protects_output(service):
    client, app, fake = service
    response = call(client)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['segments'][0]['translated_text'] == 'SALE'
    assert body['render']['engine'] is None
    assert body['render']['output_image_id'] is None
    assert body['usage']['total_tokens'] == 7
    assert fake.text_calls[0][1] == 'qwen3.8-flash'
    record = app.state.storage.get(body['id'])
    assert record['parsed_fields']['segments'] == body['segments']
    assert record['status'] == 'success'
    assert client.get(f"/admin/records/{body['id']}/output_image", follow_redirects=False).status_code == 307


def test_translate_retry_failure_is_recorded(service):
    client, app, fake = service
    fake.translations = ['broken', '{"translations":[{"id":9,"ru":"X"}]}']
    response = call(client)
    assert response.status_code == 502
    assert response.json()['error_code'] == 'translate_parse_failed'
    assert len(fake.text_calls) == 2
    record = app.state.storage.get(response.json()['id'])
    assert record['status'] == 'error'
    assert record['error_code'] == 'translate_parse_failed'


def test_pil_and_auto_modes_and_admin_image(service):
    client, app, fake = service
    for mode in ('pil', 'auto'):
        response = call(client, mode)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['render']['engine'] == 'pil'
        assert body['render']['rendered_items'] == [1]
        login = client.post('/admin/login', json={'password': 'passphrase'}, headers={'Origin': 'https://testserver'})
        assert login.status_code == 200
        image = client.get(f"/admin/records/{body['id']}/output_image")
        assert image.status_code == 200
        with Image.open(io.BytesIO(image.content)) as result:
            assert result.getpixel((20, 20)) == (222, 37, 45)
        assert app.state.storage.get(body['id'])['parsed_fields']['render']['output_image_id']


def test_pil_skips_complex_and_auto_uses_edit(service, monkeypatch):
    client, _, fake = service
    fake.items = [BAND, COMPLEX]
    fake.translations = [json.dumps({'translations': [{'id': 1, 'ru': 'SALE'}, {'id': 2, 'ru': 'NEW'}]})]
    response = call(client, 'pil')
    assert response.status_code == 200
    assert response.json()['render']['skipped_items'] == [2]
    seen = []
    async def edited(*args, **kwargs):
        seen.append(args)
        return png(), 'seedream', None
    monkeypatch.setattr('sirius_vision.main.render_edit', edited)
    response = call(client, 'auto')
    assert response.status_code == 200, response.text
    assert response.json()['render']['engine'] == 'seedream'
    assert response.json()['render']['rendered_items'] == [1, 2]
    assert seen


def test_edit_total_failure_falls_back_to_pil_or_text(service, monkeypatch):
    client, app, fake = service
    async def unavailable(*args, **kwargs):
        return None, None, 'qwen-image->seedream'
    monkeypatch.setattr('sirius_vision.main.render_edit', unavailable)
    response = call(client, 'edit')
    assert response.status_code == 200
    assert response.json()['render']['engine'] == 'pil'
    assert response.json()['render']['fallback_from'] == 'qwen-image->seedream'
    fake.items = [COMPLEX]
    fake.translations = ['{"translations":[{"id":2,"ru":"NEW"}]}']
    response = call(client, 'edit')
    assert response.status_code == 200
    assert response.json()['render']['engine'] is None
    assert response.json()['render']['error']
    assert response.json()['render']['skipped_items'] == [2]
    assert app.state.storage.get(response.json()['id'])['status'] == 'success'


def test_admin_output_image_only_for_record_with_render(service):
    client, _, _ = service
    no_output = call(client, 'none').json()['id']
    output = call(client, 'pil').json()['id']
    client.post('/admin/login', json={'password': 'passphrase'}, headers={'Origin': 'https://testserver'})
    assert client.get(f'/admin/records/{no_output}/output_image').status_code == 404
    assert client.get(f'/admin/records/{output}/output_image').status_code == 200
    js = client.get('/admin/static/app.js').text
    html = client.get('/admin/').text
    assert 'output_image_id' in js and 'detail-output-image' in js
    assert 'detail-output-image' in html


def test_invalid_options_rejected_before_provider(service):
    client, _, fake = service
    response = call(client, 'unknown')
    assert response.status_code == 422
    assert fake.vision_calls == []


async def test_text_only_qwen_payload():
    seen = []
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': '{"translations":[]}'}}]})
    settings = Settings(providers={'qwen': ProviderConfig('qwen', 'https://provider.invalid/v1', 'key', 'flash')})
    result = await ChatAdapter(settings, httpx.MockTransport(handler)).complete_text('translate', 'qwen3.8-flash')
    assert result.model == 'qwen3.8-flash'
    assert seen[0]['messages'][0]['content'] == 'translate'
    assert all('image_url' not in str(message) for message in seen[0]['messages'])


async def test_zh_edit_instruction_uses_correct_language():
    from sirius_vision.renderer.edit_render import render_edit
    prompts = []
    def handler(request):
        if request.url.host == 'image.invalid':
            return httpx.Response(200, content=png())
        prompts.append(json.loads(request.content)['prompt'])
        return httpx.Response(200, json={'data': [{'url': 'https://image.invalid/out'}]})
    settings = Settings(ark_api_key='ark')
    result, engine, _ = await render_edit(png(), [{**BAND, 'translated_text': '促销'}], settings,
                                          httpx.MockTransport(handler), target_lang='zh')
    assert result and engine == 'seedream'
    assert 'replace ONLY the listed Russian text with the given Chinese text' in prompts[0]
    assert 'Render the Chinese verbatim' in prompts[0]


def test_pil_fill_center_and_shrink(tmp_path):
    from sirius_vision.renderer.pil_render import render_pil
    image = png()
    item = {**BAND, 'translated_text': 'LONG RUSSIAN TRANSLATION'}
    result, rendered, skipped, warnings = render_pil(image, [item], str(tmp_path / 'missing.ttf'))
    assert rendered == [1] and skipped == []
    with Image.open(io.BytesIO(result)) as output:
        assert output.getpixel((20, 10)) == (222, 37, 45)
        assert output.getpixel((100, 5)) == (51, 68, 85)
        assert any(output.getpixel((x, y)) != (222, 37, 45) for x in range(35, 165) for y in range(12, 40))


def test_pil_text_is_centered():
    from sirius_vision.renderer.pil_render import render_pil
    data, _, _, _ = render_pil(png(), [{**BAND, 'translated_text': 'SALE'}], '/missing/font.ttf')
    with Image.open(io.BytesIO(data)) as output:
        points = [(x, y) for x in range(20, 180) for y in range(10, 40)
                  if output.getpixel((x, y)) != (222, 37, 45)]
    assert points
    assert abs((min(x for x, _ in points) + max(x for x, _ in points)) / 2 - 100) <= 4
    assert abs((min(y for _, y in points) + max(y for _, y in points)) / 2 - 25) <= 4


def test_pil_rejects_missing_cyrillic_glyphs():
    from sirius_vision.renderer.pil_render import render_pil
    with pytest.raises(ValueError, match='glyph'):
        render_pil(png(), [{**BAND, 'translated_text': 'Скидка'}], '/missing/font.ttf')


def test_chinese_target_uses_chinese_font(service):
    client, app, fake = service
    fake.translations = ['{"translations":[{"id":1,"ru":"促销"}]}']
    response = call(client, 'pil', 'zh')
    assert response.status_code == 200, response.text
    if Path(app.state.settings.font_path_chinese).is_file():
        assert response.json()['render']['engine'] == 'pil'
        assert response.json()['render']['error'] is None
    else:
        assert response.json()['render']['engine'] is None
        assert response.json()['render']['error']


@pytest.mark.parametrize('qwen_ok,seed_ok,expected', [(True, True, 'qwen-image'), (False, True, 'seedream'), (False, False, None)])
async def test_edit_engine_fallbacks(qwen_ok, seed_ok, expected, caplog):
    from sirius_vision.renderer.edit_render import render_edit
    caplog.set_level('INFO')
    calls = []
    def handler(request):
        calls.append(request.url.host)
        if request.url.host == 'image.invalid':
            return httpx.Response(200, content=png())
        body = json.loads(request.content)
        assert request.extensions['timeout']['read'] == 123
        if request.url.host == 'token-plan.cn-beijing.maas.aliyuncs.com':
            assert request.url.path == '/api/v1/services/aigc/multimodal-generation/generation'
            assert request.headers['Authorization'] == 'Bearer token-plan-test'
            assert body['model'] == 'qwen-image-3.0-pro'
            message = body['input']['messages'][0]
            assert message['role'] == 'user'
            assert message['content'][0]['image'].startswith('data:image/jpeg;base64,')
            assert 'SALE' in message['content'][1]['text']
            if not qwen_ok:
                return httpx.Response(503)
            return httpx.Response(200, json={'output': {'choices': [{'message': {'content': [{'text': 'done'}, {'image': 'https://image.invalid/out'}]}}]}})
        assert request.url.host == 'ark.cn-beijing.volces.com'
        assert request.headers['Authorization'] == 'Bearer ark'
        if not seed_ok:
            return httpx.Response(503)
        return httpx.Response(200, json={'data': [{'url': 'https://image.invalid/out'}]})
    settings = Settings(ark_api_key='ark', qwen_image_api_key='token-plan-test',
                        qwen_image_base_url='https://token-plan.cn-beijing.maas.aliyuncs.com', edit_timeout=123)
    result, engine, fallback = await render_edit(png(), [{**BAND, 'translated_text': 'SALE'}], settings, httpx.MockTransport(handler))
    assert engine == expected
    assert result == (png() if expected else None)
    assert fallback == (None if qwen_ok else 'qwen-image' if seed_ok else 'qwen-image->seedream')
    assert calls == (['token-plan.cn-beijing.maas.aliyuncs.com', 'image.invalid'] if qwen_ok else
                     ['token-plan.cn-beijing.maas.aliyuncs.com', 'ark.cn-beijing.volces.com'] +
                     (['image.invalid'] if seed_ok else []))
    if expected:
        assert f'edit_render_complete engine={expected}' in caplog.text
    if not qwen_ok:
        assert 'engine=qwen-image reason=HTTPStatusError: ' in caplog.text
    if not qwen_ok and not seed_ok:
        assert 'engine=seedream reason=HTTPStatusError: ' in caplog.text


@pytest.mark.parametrize('suffix', ['/compatible-mode/v1', '/compatible-mode/v1/'])
def test_qwen_image_settings_from_env(monkeypatch, suffix):
    monkeypatch.delenv('QWEN_IMAGE_BASE_URL', raising=False)
    monkeypatch.delenv('QWEN_IMAGE_MODEL', raising=False)
    monkeypatch.setenv('QWEN_API_KEY', 'token-plan-test')
    monkeypatch.setenv('QWEN_BASE_URL', 'https://token-plan.cn-beijing.maas.aliyuncs.com' + suffix)
    settings = Settings()
    assert settings.qwen_image_api_key == 'token-plan-test'
    assert settings.qwen_image_base_url == 'https://token-plan.cn-beijing.maas.aliyuncs.com'
    assert settings.qwen_image_model == 'qwen-image-3.0-pro'
    monkeypatch.setenv('QWEN_IMAGE_BASE_URL', 'https://custom.invalid')
    monkeypatch.setenv('QWEN_IMAGE_MODEL', 'custom-model')
    settings = Settings()
    assert settings.qwen_image_base_url == 'https://custom.invalid'
    assert settings.qwen_image_model == 'custom-model'


async def test_vendor_image_download_skips_input_url_fetcher_and_validates(monkeypatch):
    from sirius_vision.renderer.edit_render import _download
    from sirius_vision.images import ImageError

    async def input_url_fetcher(_url):
        raise AssertionError('Input URL fetcher must not handle vendor output')

    monkeypatch.setattr('sirius_vision.images.fetch_image', input_url_fetcher)
    monkeypatch.setattr('sirius_vision.renderer.edit_render.fetch_image', input_url_fetcher, raising=False)
    responses = [png(), b'not an image']
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=responses.pop(0)))
    async with httpx.AsyncClient(transport=transport, trust_env=False) as client:
        assert await _download('https://image.invalid/out', client, None) == png()
        with pytest.raises(ImageError):
            await _download('https://image.invalid/out', client, None)


async def test_edit_render_uses_dedicated_timeout(monkeypatch):
    from sirius_vision.renderer.edit_render import render_edit
    monkeypatch.setenv('EDIT_TIMEOUT', '123')
    settings = Settings(timeout=0.01, ark_api_key='ark')
    observed = []
    original_client = httpx.AsyncClient

    def client_factory(**kwargs):
        observed.append(kwargs['timeout'])
        return original_client(**kwargs)

    monkeypatch.setattr('sirius_vision.renderer.edit_render.httpx.AsyncClient', client_factory)
    transport = httpx.MockTransport(lambda request: httpx.Response(503))
    await render_edit(png(), [{**BAND, 'translated_text': 'SALE'}], settings, transport)
    assert settings.edit_timeout == 123.0
    assert observed == [123.0]
    assert settings.timeout == 0.01
