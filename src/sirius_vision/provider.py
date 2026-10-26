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
    async def complete_text(self, prompt: str, preferred_model: str | None = None) -> Completion: ...


class ProviderError(Exception):
    """Internal transport failure; never expose upstream exceptions."""


class ChatAdapter:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport
        self._vision_slots = asyncio.Semaphore(settings.max_vision_concurrency)

    async def complete(self, prompt: str, image: str, preferred_model: str | None = None) -> Completion:
        config = self.settings.providers.get(self.settings.provider)
        if not config or not config.base_url or not config.api_key:
            raise ProviderError('Service unavailable')
        # Template preferences must not hijack the deployment-level channel selection.
        model = config.model
        queued = time.monotonic()
        async with self._vision_slots:
            wait_ms = int((time.monotonic() - queued) * 1000)
            if wait_ms >= 100:
                logger.warning(
                    'inference_queue_wait provider=%s model=%s stage=vision wait_ms=%d limit=%d',
                    config.name, model, wait_ms, self.settings.max_vision_concurrency)
            else:
                logger.info(
                    'inference_slot_acquired provider=%s model=%s stage=vision wait_ms=%d limit=%d',
                    config.name, model, wait_ms, self.settings.max_vision_concurrency)
            return await self._request(config, model, [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': image}},
            ], stage='vision')

    async def complete_text(self, prompt: str, preferred_model: str | None = None) -> Completion:
        config = self.settings.providers.get('glm')
        if not config or not config.base_url or not config.api_key:
            raise ProviderError('Service unavailable')
        return await self._request(
            config, preferred_model or self.settings.translate_model, prompt, stage='translation')

    async def _request(self, config, model: str, content: Any, *, stage: str) -> Completion:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=self.settings.timeout, transport=self.transport, trust_env=False) as client:
            for attempt in range(2):
                try:
                    async with asyncio.timeout(self.settings.timeout):
                        response = await client.post(config.base_url.rstrip('/') + '/chat/completions',
                            headers={'Authorization': f'Bearer {config.api_key}'},
                            json={'model': model, 'messages': [{'role': 'user', 'content': content}]})
                    if response.status_code == 429 and attempt == 0:
                        logger.warning(
                            'inference_retry provider=%s model=%s stage=%s reason=rate_limit '
                            'attempt=%d next_attempt=%d backoff_s=%.3f',
                            config.name, model, stage, attempt + 1, attempt + 2,
                            self.settings.retry_backoff)
                        await asyncio.sleep(self.settings.retry_backoff)
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
                    logger.info(
                        'inference_complete provider=%s model=%s stage=%s attempt=%d latency_ms=%d',
                        config.name, model, stage, attempt + 1, elapsed)
                    return Completion(content, config.name, model, usage, elapsed)
                except (httpx.TimeoutException, TimeoutError):
                    if attempt == 0:
                        logger.warning(
                            'inference_retry provider=%s model=%s stage=%s reason=timeout '
                            'attempt=%d next_attempt=%d timeout_s=%.3f backoff_s=%.3f',
                            config.name, model, stage, attempt + 1, attempt + 2,
                            self.settings.timeout, self.settings.retry_backoff)
                        await asyncio.sleep(self.settings.retry_backoff)
                        continue
                    elapsed = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        'inference_failed provider=%s model=%s stage=%s reason=timeout '
                        'attempts=%d latency_ms=%d',
                        config.name, model, stage, attempt + 1, elapsed)
                    raise ProviderError('Service timeout') from None
                except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                    elapsed = int((time.monotonic() - started) * 1000)
                    logger.warning(
                        'inference_failed provider=%s model=%s stage=%s reason=%s '
                        'attempts=%d latency_ms=%d',
                        config.name, model, stage, type(exc).__name__, attempt + 1, elapsed)
                    raise ProviderError('Service unavailable') from None
        raise ProviderError('Service unavailable')
