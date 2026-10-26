"""Full-image editing with primary and backup providers."""
import base64
import io
import logging
from urllib.parse import urlsplit

import httpx
from PIL import Image

from ..config import Settings
from ..images import MAX_BYTES, image_type

logger = logging.getLogger(__name__)
INSTRUCTION = ('Edit this e-commerce image: replace ONLY the listed Chinese text with the given Russian text. '
               'Render the Russian verbatim in the SAME place, same text color, same badge/band background, '
               'centered, never truncate. Do not change anything else (model, clothes, pose, background, lighting).')


def _jpeg_data_url(image: bytes) -> str:
    with Image.open(io.BytesIO(image)) as source:
        output = io.BytesIO()
        source.convert('RGB').save(output, format='JPEG', quality=92)
    return 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()


def _instruction(items: list[dict]) -> str:
    return INSTRUCTION + '\n' + '\n'.join(
        f"{index}. {item['position']} ({item['text']}) -> {item['translated_text']}"
        for index, item in enumerate(items, 1))


async def _download(url: str, client: httpx.AsyncClient, transport: httpx.AsyncBaseTransport | None) -> bytes:
    parts = urlsplit(url)
    if parts.scheme != 'https' or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError('Invalid generated image URL')
    response = await client.get(url)
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError('Generated image too large')
    image_type(response.content)
    return response.content


async def render_edit(image: bytes, items: list[dict], settings: Settings,
                      transport: httpx.AsyncBaseTransport | None = None, *,
                      target_lang: str = 'ru') -> tuple[bytes | None, str | None, str | None]:
    data_url, instruction = _jpeg_data_url(image), _instruction(items)
    if target_lang == 'zh':
        instruction = (instruction.replace('listed Chinese text', 'listed Russian text')
                       .replace('given Russian text', 'given Chinese text')
                       .replace('Russian verbatim', 'Chinese verbatim'))
    async with httpx.AsyncClient(timeout=settings.edit_timeout, transport=transport, trust_env=False) as client:
        if settings.qwen_image_api_key:
            try:
                host = settings.qwen_image_base_url.rstrip('/')
                response = await client.post(f'{host}/api/v1/services/aigc/multimodal-generation/generation',
                    headers={'Authorization': f'Bearer {settings.qwen_image_api_key}'},
                    json={'model': settings.qwen_image_model, 'input': {'messages': [{'role': 'user',
                          'content': [{'image': data_url}, {'text': instruction}]}]}})
                response.raise_for_status()
                content = response.json()['output']['choices'][0]['message']['content']
                url = next(part['image'] for part in content if isinstance(part, dict) and part.get('image'))
                result = await _download(url, client, transport)
                logger.info('edit_render_complete engine=qwen-image')
                return result, 'qwen-image', None
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
                logger.warning('edit_render_failed engine=qwen-image reason=%s', f'{type(exc).__name__}: {str(exc)[:120]}')
        if settings.ark_api_key:
            try:
                response = await client.post('https://ark.cn-beijing.volces.com/api/v3/images/generations',
                    headers={'Authorization': f'Bearer {settings.ark_api_key}'},
                    json={'model': settings.ark_seedream_model, 'prompt': instruction, 'image': [data_url],
                          'response_format': 'url', 'watermark': False})
                response.raise_for_status()
                url = response.json()['data'][0]['url']
                result = await _download(url, client, transport)
                logger.info('edit_render_complete engine=seedream')
                return result, 'seedream', 'qwen-image'
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
                logger.warning('edit_render_failed engine=seedream reason=%s', f'{type(exc).__name__}: {str(exc)[:120]}')
    return None, None, 'qwen-image->seedream'
