"""Environment configuration; endpoints and credentials have no code defaults."""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key: str
    model: str


@dataclass
class Settings:
    root: Path = field(default_factory=lambda: Path(os.getenv('VISION_ROOT', '.')).resolve())
    api_keys: tuple[str, ...] = field(default_factory=lambda: split_keys('VISION_API_KEYS'))
    admin_api_keys: tuple[str, ...] = field(default_factory=lambda: split_keys('VISION_ADMIN_API_KEYS'))
    admin_password_hash: str = field(default_factory=lambda: os.getenv('VISION_ADMIN_PASSWORD_HASH', ''))
    session_secret: str = field(default_factory=lambda: os.getenv('VISION_SESSION_SECRET', ''))
    session_ttl: int = 28800
    provider: str = field(default_factory=lambda: os.getenv('VISION_PROVIDER', 'minimax'))
    timeout: float = 45.0
    providers: dict[str, ProviderConfig] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.providers:
            for name, prefix, key, model in [
                ('minimax', 'MINIMAX', 'MINIMAX_API_KEY', 'MiniMax-M3'),
                ('kimi', 'KIMI', 'KIMI_API_KEY', 'kimi-for-coding'),
                ('glm', 'GLM', 'Z_AI_API_KEY', 'glm-5.3-flash'),
            ]:
                selected = name == self.provider
                self.providers[name] = ProviderConfig(
                    name, os.getenv('VISION_BASE_URL', '') if selected and os.getenv('VISION_BASE_URL')
                    else os.getenv(f'{prefix}_BASE_URL', ''), os.getenv(key, ''),
                    os.getenv('VISION_MODEL', model) if selected else os.getenv(f'{prefix}_MODEL', model))


def split_keys(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, '').split(',') if value.strip())
