"""Bounded image input with DNS validation and connection-address pinning."""
import asyncio
import base64
import binascii
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

MAX_BYTES = 20 * 1024 * 1024


class ImageError(ValueError):
    pass


def image_type(data: bytes) -> tuple[str, str]:
    if len(data) > MAX_BYTES:
        raise ImageError('Image too large')
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg', 'image/jpeg'
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png', 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'gif', 'image/gif'
    if data.startswith(b'BM'):
        return 'bmp', 'image/bmp'
    if data.startswith(b'RIFF') and data[8:12] == b'WEBP':
        return 'webp', 'image/webp'
    raise ImageError('Unsupported image type')


def decode_image(value: str) -> bytes:
    if value.startswith('data:'):
        header, sep, value = value.partition(',')
        if not sep or not header.endswith(';base64'):
            raise ImageError('Invalid image encoding')
    if len(value) > ((MAX_BYTES + 2) // 3) * 4:
        raise ImageError('Image too large')
    try:
        data = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        raise ImageError('Invalid image encoding') from None
    image_type(data)
    return data


async def fetch_image(url: str) -> bytes:
    try:
        async with asyncio.timeout(20):
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
                raise ImageError('Invalid image URL')
            port = parts.port or (443 if parts.scheme == 'https' else 80)
            if port not in (80, 443):
                raise ImageError('Invalid image URL')
            host = parts.hostname.encode('idna').decode()
            addresses = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
                raise ImageError('Image URL is not public')
            ip = addresses[0][4][0]
            # Host header and TLS SNI retain the hostname; TCP connects only to validated IP.
            target = httpx.URL(url).copy_with(host=ip)
            host_header = ('[' + host + ']') if ':' in host else host
            if parts.port:
                host_header += ':' + str(port)
            async with httpx.AsyncClient(timeout=15, follow_redirects=False, trust_env=False) as client:
                async with client.stream('GET', target, headers={'Host': host_header, 'Accept-Encoding': 'identity'},
                                         extensions={'sni_hostname': host}) as response:
                    if response.status_code != 200:
                        raise ImageError('Image download failed')
                    size = response.headers.get('content-length')
                    if size and int(size) > MAX_BYTES:
                        raise ImageError('Image too large')
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_BYTES:
                            raise ImageError('Image too large')
            data = bytes(chunks)
            image_type(data)
            return data
    except ImageError:
        raise
    except (ValueError, OSError, httpx.HTTPError, TimeoutError):
        raise ImageError('Image download failed') from None
