import json

import httpx
import pytest

from sirius_vision.config import ProviderConfig, Settings
from sirius_vision.provider import ChatAdapter, ProviderError


@pytest.fixture
def provider_env(monkeypatch):
    for name in ('VISION_BASE_URL', 'VISION_MODEL', 'QWEN_MODEL', 'MINIMAX_MODEL', 'TRANSLATE_PROVIDER'):
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


@pytest.mark.parametrize('preferred_model', [None, 'qwen-preferred'])
async def test_text_defaults_to_qwen_without_glm_credentials(provider_env, preferred_model):
    provider_env.setenv('TRANSLATE_MODEL', 'qwen3.8-flash')
    for name in ('GLM_BASE_URL', 'Z_AI_API_KEY', 'GLM_MODEL'):
        provider_env.delenv(name, raising=False)
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    result = await ChatAdapter(Settings(), httpx.MockTransport(handler)).complete_text(
        'translate', preferred_model=preferred_model)
    assert len(seen) == 1
    assert str(seen[0].url) == 'https://qwen.invalid/v1/chat/completions'
    body = json.loads(seen[0].content)
    assert body['model'] == (preferred_model or 'qwen3.8-flash')
    assert body['messages'][0]['content'] == 'translate'
    assert result.provider == 'qwen'
    assert result.model == body['model']


async def test_translate_provider_env_overrides_selection(provider_env):
    provider_env.setenv('TRANSLATE_PROVIDER', 'minimax')
    provider_env.setenv('TRANSLATE_MODEL', 'translation-model')

    def handler(request):
        assert str(request.url) == 'https://minimax.invalid/v1/chat/completions'
        assert json.loads(request.content)['model'] == 'translation-model'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})

    settings = Settings()
    assert settings.provider == 'qwen'
    assert settings.translate_provider == 'minimax'
    result = await ChatAdapter(settings, httpx.MockTransport(handler)).complete_text('translate')
    assert result.provider == 'minimax'


@pytest.mark.parametrize('config', [
    None,
    ProviderConfig('qwen', '', 'test-key', 'qwen3.8-flash'),
    ProviderConfig('qwen', 'https://qwen.invalid/v1', '', 'qwen3.8-flash'),
])
async def test_text_missing_provider_configuration(config, provider_env):
    providers = {'glm': ProviderConfig('glm', 'https://glm.invalid/v1', 'test-key', 'glm')}
    if config is not None:
        providers['qwen'] = config

    def handler(request):
        pytest.fail('Missing translation configuration must fail before requesting')

    adapter = ChatAdapter(Settings(providers=providers), httpx.MockTransport(handler))
    with pytest.raises(ProviderError, match='^Service unavailable$'):
        await adapter.complete_text('translate')
