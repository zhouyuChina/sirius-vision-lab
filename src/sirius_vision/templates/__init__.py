"""Versioned prompts and strict JSON schema validation."""
import json
import re
from dataclasses import dataclass
from typing import Any

from jsonschema import Draft202012Validator


@dataclass(frozen=True)
class Template:
    task: str
    prompt: str
    schema: dict[str, Any]
    version: str = '1.0.0'
    preferred_model: str | None = None

    def parse(self, raw: str) -> dict[str, Any]:
        clean = re.sub(r'<think\b[^>]*>.*?</think\s*>', '', raw, flags=re.S | re.I).strip()
        if re.search(r'<think\b', clean, re.I):
            raise ValueError('Incomplete reasoning block')
        decoder = json.JSONDecoder()
        # Prefer the final schema-valid object in a reasoning tail; reject nonfinite JSON.
        for match in reversed(list(re.finditer(r'\{', clean))):
            try:
                value, _ = decoder.raw_decode(clean[match.start():])
                json.dumps(value, allow_nan=False)
                if isinstance(value, dict) and Draft202012Validator(self.schema).is_valid(value):
                    return value
            except (ValueError, RecursionError):
                continue
        raise ValueError('Invalid structured output')


def schema(properties: dict[str, Any]) -> dict[str, Any]:
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}


def enum(*values: str) -> dict[str, Any]:
    return {'type': 'string', 'enum': list(values)}


from .avatar_tag import TEMPLATE as AVATAR  # noqa: E402
from .garment_attr import TEMPLATE as GARMENT  # noqa: E402

TEMPLATES = {item.task: item for item in (AVATAR, GARMENT)}
