from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup


class WebToolError(RuntimeError):
    pass


def _public_host(hostname: str) -> bool:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False
    return True


async def fetch_public_url(url: str, *, max_bytes: int = 2_000_000) -> dict[str, object]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise WebToolError("Only public HTTPS URLs are allowed.")
    if not _public_host(parsed.hostname):
        raise WebToolError("Private/local network destinations are not allowed.")

    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        async with client.stream("GET", url, headers={"user-agent": "VeltrixTelegramAgent/1.0"}) as r:
            r.raise_for_status()
            body = bytearray()
            async for chunk in r.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise WebToolError("Page is too large to fetch safely.")
            content_type = r.headers.get("content-type", "")
            final_url = str(r.url)

    raw = bytes(body)
    text = raw.decode("utf-8", errors="ignore")
    if "html" in content_type.lower():
        soup = BeautifulSoup(text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        extracted = "\n".join(line.strip() for line in soup.get_text("\n").splitlines() if line.strip())
    else:
        title = ""
        extracted = text
    return {
        "url": final_url,
        "title": title,
        "content_type": content_type,
        "text": extracted[:30000],
    }


def platform_query(query: str, platform: str | None = None) -> str:
    domains = {
        "pinterest": "pinterest.com",
        "instagram": "instagram.com",
        "telegram": "t.me",
        "github": "github.com",
        "reddit": "reddit.com",
        "youtube": "youtube.com",
    }
    if not platform:
        return query
    domain = domains.get(platform.lower())
    return f"site:{domain} {query}" if domain else f"{platform} {query}"
