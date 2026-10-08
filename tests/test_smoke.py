"""Explicit opt-in smoke: VISION_SMOKE_IMAGE=/path/to/image uv run pytest -m slow."""
import base64
import os
from pathlib import Path

import pytest

from sirius_vision.config import Settings
from sirius_vision.images import image_type
from sirius_vision.provider import ChatAdapter
from sirius_vision.templates import TEMPLATES


@pytest.mark.slow
async def test_real_api_smoke():
    path = os.getenv('VISION_SMOKE_IMAGE')
    if not path:
        pytest.skip('Set VISION_SMOKE_IMAGE and provider environment for live smoke')
    import json
    data = Path(path).read_bytes()
    _, mime = image_type(data)
    template = TEMPLATES[os.getenv('VISION_SMOKE_TASK', 'garment_attr')]
    result = await ChatAdapter(Settings()).complete(template.prompt + '\n' + json.dumps(template.schema),
                                                    f'data:{mime};base64,' + base64.b64encode(data).decode())
    assert template.parse(result.content)
