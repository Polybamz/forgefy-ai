"""Read a web page.

Grant with care: a key with plugin:web can make this server fetch URLs. Only
public http(s) addresses are allowed — private, loopback and link-local ones
(the database, the model runtime, cloud metadata endpoints) are refused, and
each redirect is checked again.
"""
import html
import ipaddress
import re
import socket
from typing import Annotated
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import Field

from app.plugins.sdk import tool

DESCRIPTION = "Fetch a public web page and return its text."

_MAX_BYTES = 2_000_000
_MAX_REDIRECTS = 3
_TAGS = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>|<[^>]+>", re.S | re.I)


def _check_public(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("only http:// and https:// URLs can be fetched")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise ValueError(f"cannot resolve {parsed.hostname}") from exc
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError(f"{parsed.hostname} is not a public address")


def _to_text(body: str) -> str:
    text = html.unescape(_TAGS.sub(" ", body))
    return re.sub(r"[ \t]+", " ", re.sub(r"\s*\n\s*", "\n", text)).strip()


@tool
async def fetch(
    url: Annotated[str, Field(description="Public http(s) URL")],
    max_chars: Annotated[int, Field(ge=500, le=20_000)] = 8_000,
) -> str:
    """Fetch a public web page and return its readable text (HTML tags removed)."""
    async with httpx.AsyncClient(timeout=15, follow_redirects=False,
                                 headers={"User-Agent": "ai-platform-web-plugin/1.0"}) as client:
        for _ in range(_MAX_REDIRECTS + 1):
            _check_public(url)
            async with client.stream("GET", url) as resp:
                if resp.is_redirect and resp.headers.get("location"):
                    url = urljoin(url, resp.headers["location"])
                    continue
                if resp.is_error:
                    raise ValueError(f"HTTP {resp.status_code} from {url}")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        break
                    chunks.append(chunk)
                body = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
                kind = resp.headers.get("content-type", "")
                text = _to_text(body) if "html" in kind else body
                return f"URL: {url}\n\n{text[:max_chars]}"
        raise ValueError("too many redirects")
