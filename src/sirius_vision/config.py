"""Environment configuration; endpoints and credentials have no code defaults."""
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


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
    timeout: float = field(default_factory=lambda: float(os.getenv('VISION_TIMEOUT', '75')))
    max_vision_concurrency: int = field(default_factory=lambda: int(
        os.getenv('VISION_MAX_CONCURRENCY', '2')))
    retry_backoff: float = field(default_factory=lambda: float(
        os.getenv('VISION_RETRY_BACKOFF', '1')))
    edit_timeout: float = field(default_factory=lambda: float(os.getenv('EDIT_TIMEOUT', '180')))
    translate_provider: str = field(default_factory=lambda: os.getenv('TRANSLATE_PROVIDER', 'qwen'))
    translate_model: str = field(default_factory=lambda: os.getenv('TRANSLATE_MODEL', 'qwen3.8-flash'))
    font_path_cyrillic: str = field(default_factory=lambda: os.getenv('FONT_PATH_CYRILLIC', '/System/Library/Fonts/Supplemental/Arial Bold.ttf'))
    font_path_chinese: str = field(default_factory=lambda: os.getenv('FONT_PATH_CHINESE', '/System/Library/Fonts/STHeiti Light.ttc'))
    ark_api_key: str = field(default_factory=lambda: os.getenv('ARK_API_KEY', ''))
    ark_seedream_model: str = field(default_factory=lambda: os.getenv('ARK_SEEDREAM_MODEL', 'doubao-seedream-5-0-pro-260628'))
    qwen_image_api_key: str = field(default_factory=lambda: os.getenv('QWEN_API_KEY', ''))
    qwen_image_base_url: str = field(default_factory=lambda: os.getenv(
        'QWEN_IMAGE_BASE_URL', os.getenv('QWEN_BASE_URL', '').rstrip('/').removesuffix('/compatible-mode/v1')))
    qwen_image_model: str = field(default_factory=lambda: os.getenv('QWEN_IMAGE_MODEL', 'qwen-image-3.0-pro'))
    dashscope_api_key: str = field(default_factory=lambda: os.getenv('DASHSCOPE_API_KEY', ''))
    dashscope_workspace_host: str = field(default_factory=lambda: os.getenv('DASHSCOPE_WORKSPACE_HOST', 'llm-9ucxkul4g12vzutq.cn-beijing.maas.aliyuncs.com'))
    dashscope_image_model: str = field(default_factory=lambda: os.getenv('DASHSCOPE_IMAGE_MODEL', 'qwen-image-3.0-pro'))
    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    public_base_url: str = field(default_factory=lambda: os.getenv(
        'VISION_PUBLIC_BASE_URL', 'https://101.37.175.27/vision').rstrip('/'))

    def __post_init__(self) -> None:
        if not 0 < self.timeout <= 300:
            raise ValueError('VISION_TIMEOUT must be greater than 0 and at most 300 seconds')
        if not 1 <= self.max_vision_concurrency <= 16:
            raise ValueError('VISION_MAX_CONCURRENCY must be between 1 and 16')
        if not 0 <= self.retry_backoff <= 30:
            raise ValueError('VISION_RETRY_BACKOFF must be between 0 and 30 seconds')
        self.public_base_url = self.public_base_url.rstrip('/')
        public_url = urlsplit(self.public_base_url)
        if (public_url.scheme != 'https' or not public_url.netloc or public_url.query
                or public_url.fragment or public_url.username or public_url.password):
            raise ValueError('VISION_PUBLIC_BASE_URL must be an absolute HTTPS URL')
        if not self.providers:
            for name, prefix, key, model in [
                ('minimax', 'MINIMAX', 'MINIMAX_API_KEY', 'MiniMax-M3'),
                ('kimi', 'KIMI', 'KIMI_API_KEY', 'kimi-for-coding'),
                ('glm', 'GLM', 'Z_AI_API_KEY', 'glm-5.3-flash'),
                ('qwen', 'QWEN', 'QWEN_API_KEY', 'qwen3.8-flash'),
            ]:
                selected = name == self.provider
                self.providers[name] = ProviderConfig(
                    name, os.getenv('VISION_BASE_URL', '') if selected and os.getenv('VISION_BASE_URL')
                    else os.getenv(f'{prefix}_BASE_URL', ''), os.getenv(key, ''),
                    os.getenv('VISION_MODEL', model) if selected else os.getenv(f'{prefix}_MODEL', model))


def split_keys(name: str) -> tuple[str, ...]:
    return tuple(value.strip() for value in os.getenv(name, '').split(',') if value.strip())
