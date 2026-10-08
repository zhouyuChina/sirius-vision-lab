"""OpenAI-compatible chat transport with bounded transient retries."""
import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


@dataclass
class Completion:
    content: str
    provider: str
    model: str
    usage: dict[str, int]
    latency_ms: int


class Adapter(Protocol):
    async def complete(self, prompt: str, image: str, preferred_model: str | None = None) -> Completion: ...


class ProviderError(Exception):
    """Internal transport failure; never expose upstream exceptions."""


class ChatAdapter:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    async def complete(self, prompt: str, image: str, preferred_model: str | None = None) -> Completion:
        config = self.settings.providers.get(self.settings.provider)
        if preferred_model:
            config = next((p for p in self.settings.providers.values() if p.model == preferred_model), config)
        if not config or not config.base_url or not config.api_key:
            raise ProviderError('Service unavailable')
        model = preferred_model or config.model
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.settings.timeout, transport=self.transport, trust_env=False) as client:
            for attempt in range(2):
                try:
                    async with asyncio.timeout(self.settings.timeout):
                        response = await client.post(config.base_url.rstrip('/') + '/chat/completions',
                            headers={'Authorization': f'Bearer {config.api_key}'},
                            json={'model': model, 'messages': [{'role': 'user', 'content': [
                                {'type': 'text', 'text': prompt},
                                {'type': 'image_url', 'image_url': {'url': image}},
                            ]}]})
                    if response.status_code == 429 and attempt == 0:
                        logger.warning('inference_retry reason=rate_limit attempt=1')
                        await asyncio.sleep(0.25)
                        continue
                    response.raise_for_status()
                    data = response.json()
                    if not isinstance(data, dict):
                        raise ValueError('Invalid response')
                    message = data['choices'][0]['message']
                    if not isinstance(message, dict) or not isinstance(data.get('usage') or {}, dict):
                        raise ValueError('Invalid response fields')
                    content = message.get('content') or message.get('reasoning_content') or ''
                    if not isinstance(content, str):
                        raise ValueError('Invalid content')
                    usage = {k: v for k, v in (data.get('usage') or {}).items()
                             if isinstance(v, int) and not isinstance(v, bool) and v >= 0}
                    elapsed = int((time.monotonic() - started) * 1000)
                    logger.info('inference_complete latency_ms=%d', elapsed)
                    return Completion(content, config.name, model, usage, elapsed)
                except (httpx.TimeoutException, TimeoutError):
                    if attempt == 0:
                        logger.warning('inference_retry reason=timeout attempt=1')
                        await asyncio.sleep(0.25)
                        continue
                    raise ProviderError('Service timeout') from None
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                    logger.warning('inference_failed')
                    raise ProviderError('Service unavailable') from None
        raise ProviderError('Service unavailable')
