from __future__ import annotations

import ipaddress
import os
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx


class MediaDownloadError(RuntimeError):
    pass


def _validate_https_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise MediaDownloadError("Only public HTTPS URLs are allowed.")
    try:
        ips = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as exc:
        raise MediaDownloadError("Host could not be resolved.") from exc
    for info in ips:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise MediaDownloadError("Private/local destinations are blocked.")


async def download_public_media(url: str, *, max_mb: int = 25) -> dict[str, str | int]:
    _validate_https_public(url)
    max_bytes = max_mb * 1024 * 1024
    suffix = Path(urlparse(url).path).suffix[:10]
    fd, path = tempfile.mkstemp(prefix="veltrix_", suffix=suffix)
    os.close(fd)
    size = 0
    content_type = "application/octet-stream"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=40) as client:
            async with client.stream("GET", url, headers={"user-agent": "VeltrixTelegramAgent/1.0"}) as r:
                r.raise_for_status()
                content_type = r.headers.get("content-type", content_type).split(";", 1)[0]
                with open(path, "wb") as f:
                    async for chunk in r.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            raise MediaDownloadError(f"File exceeds {max_mb} MB limit.")
                        f.write(chunk)
        return {"path": path, "bytes": size, "content_type": content_type, "url": url}
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise
