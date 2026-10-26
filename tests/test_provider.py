import json

import httpx
import pytest

from sirius_vision.config import Settings
from sirius_vision.provider import ChatAdapter


@pytest.fixture
def provider_env(monkeypatch):
    for name in ('VISION_BASE_URL', 'VISION_MODEL', 'QWEN_MODEL', 'MINIMAX_MODEL'):
        monkeypatch.delenv(name, raising=False)
    for prefix, key in [('QWEN', 'QWEN_API_KEY'), ('MINIMAX', 'MINIMAX_API_KEY'),
                        ('GLM', 'Z_AI_API_KEY')]:
        monkeypatch.setenv(key, 'test-key')
        monkeypatch.setenv(f'{prefix}_BASE_URL', f'https://{prefix.lower()}.invalid/v1')
    monkeypatch.setenv('VISION_PROVIDER', 'qwen')
    return monkeypatch


def test_settings_registers_qwen(provider_env):
    settings = Settings()
    config = settings.providers['qwen']
    assert settings.provider == 'qwen'
    assert config.name == 'qwen'
    assert config.base_url == 'https://qwen.invalid/v1'
    assert config.api_key == 'test-key'
    assert config.model == 'qwen3.8-flash'


def test_selected_provider_overrides_only_qwen(provider_env):
    provider_env.setenv('VISION_BASE_URL', 'https://override.invalid/v1')
    provider_env.setenv('VISION_MODEL', 'qwen-override')
    settings = Settings()
    assert settings.providers['qwen'].base_url == 'https://override.invalid/v1'
    assert settings.providers['qwen'].model == 'qwen-override'
    assert settings.providers['minimax'].base_url == 'https://minimax.invalid/v1'
    assert settings.providers['minimax'].model == 'MiniMax-M3'


@pytest.mark.parametrize('provider,override,expected_model', [
    ('qwen', None, 'qwen3.8-flash'),
    ('qwen', 'qwen-override', 'qwen-override'),
    ('minimax', None, 'MiniMax-M3'),
])
async def test_vision_preference_keeps_selected_channel(provider_env, provider, override, expected_model):
    provider_env.setenv('VISION_PROVIDER', provider)
    if override:
        provider_env.setenv('VISION_MODEL', override)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    result = await ChatAdapter(Settings(), httpx.MockTransport(handler)).complete(
        'extract', 'data:image/png;base64,dGVzdA==', preferred_model='MiniMax-M3')
    assert len(seen) == 1
    assert str(seen[0].url) == f'https://{provider}.invalid/v1/chat/completions'
    assert json.loads(seen[0].content)['model'] == expected_model
    assert result.provider == provider
    assert result.model == expected_model


@pytest.mark.parametrize('preferred_model', [None, 'glm-preferred'])
async def test_text_keeps_glm_channel(provider_env, preferred_model):
    provider_env.setenv('TRANSLATE_MODEL', 'glm-translate')
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    result = await ChatAdapter(Settings(), httpx.MockTransport(handler)).complete_text(
        'translate', preferred_model=preferred_model)
    assert len(seen) == 1
    assert str(seen[0].url) == 'https://glm.invalid/v1/chat/completions'
    body = json.loads(seen[0].content)
    assert body['model'] == (preferred_model or 'glm-translate')
    assert body['messages'][0]['content'] == 'translate'
    assert result.provider == 'glm'
    assert result.model == body['model']
