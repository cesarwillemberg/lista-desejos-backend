"""Fetch product pages with a browser-like client.

curl_cffi impersonates Chrome's TLS fingerprint. Amazon.com.br answers
stock httpx with HTTP 503 from this kind of network and returns the real
product page to the impersonated client. httpx remains the fallback.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

MAX_HTML_CHARS = 3_000_000
MAX_IMAGE_BYTES = 5_000_000


@dataclass
class FetchResult:
    ok: bool
    url: str
    final_url: str
    status: int | None
    html: str
    error: str | None
    via: str | None


def ensure_public_url(url: str) -> str:
    """Reject non-http URLs and hosts that resolve to a non-public address."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Informe um link de produto que comece com http:// ou https://.")
    host = parsed.hostname.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("Esse endereço aponta para uma rede interna e não pode ser lido.")
    try:
        ips = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror as exc:
            raise ValueError("Não encontramos esse endereço. Confira o link.") from exc
        ips = set()
        for info in infos:
            try:
                ips.add(ipaddress.ip_address(info[4][0]))
            except ValueError:
                continue
        if not ips:
            raise ValueError("Não encontramos esse endereço. Confira o link.")
    for ip in ips:
        if not ip.is_global:
            raise ValueError("Esse endereço aponta para uma rede interna e não pode ser lido.")
    return url.strip()


def fetch_html(url: str, timeout: float = 20.0) -> FetchResult:
    ensure_public_url(url)
    try:
        return _with_curl(url, timeout)
    except ValueError:
        raise
    except TimeoutError:
        return FetchResult(False, url, url, None, "", "A página demorou demais para responder.", None)
    except Exception:
        pass
    try:
        return _with_httpx(url, timeout)
    except ValueError:
        raise
    except TimeoutError:
        return FetchResult(False, url, url, None, "", "A página demorou demais para responder.", "httpx")
    except Exception:
        return FetchResult(False, url, url, None, "", "Não foi possível acessar a página da loja.", None)


def fetch_image(url: str, referer: str | None, timeout: float = 15.0) -> tuple[bytes, str]:
    ensure_public_url(url)
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9",
        "User-Agent": BROWSER_HEADERS["User-Agent"],
    }
    if referer:
        headers["Referer"] = referer
    content, content_type = _image_via_curl(url, headers, timeout)
    if content is None:
        content, content_type = _image_via_httpx(url, headers, timeout)
    if content is None:
        raise RuntimeError("image fetch failed")
    if len(content) > MAX_IMAGE_BYTES:
        raise RuntimeError("image too large")
    media = _image_media_type(content, content_type or "")
    if media is None:
        raise RuntimeError("not an image")
    return content, media


def _with_curl(url: str, timeout: float) -> FetchResult:
    from curl_cffi import requests as creq

    try:
        response = creq.get(
            url,
            impersonate="chrome131",
            timeout=timeout,
            allow_redirects=True,
            headers={"Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7"},
        )
    except Exception as exc:
        if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
            raise TimeoutError(str(exc)) from exc
        raise
    return _result_from_response(url, str(response.url), response.status_code, response.headers, response.text, "curl_cffi")


def _with_httpx(url: str, timeout: float) -> FetchResult:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=BROWSER_HEADERS) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise TimeoutError(str(exc)) from exc
    return _result_from_response(url, str(response.url), response.status_code, response.headers, response.text, "httpx")


def _result_from_response(url, final_url, status, headers, text, via) -> FetchResult:
    ensure_public_url(final_url)
    ctype = ""
    if headers is not None:
        getter = getattr(headers, "get", None)
        ctype = (getter("content-type") or "") if getter else ""
    ctype = ctype.lower()
    if ctype and not any(token in ctype for token in ("html", "xml", "json", "text/")):
        return FetchResult(
            True,
            url,
            final_url,
            status,
            "",
            "Esse link não parece uma página de produto.",
            via,
        )
    html = text or ""
    if len(html) > MAX_HTML_CHARS:
        html = html[:MAX_HTML_CHARS]
    return FetchResult(True, url, final_url, status, html, None, via)


def _image_via_curl(url, headers, timeout):
    try:
        from curl_cffi import requests as creq

        response = creq.get(url, impersonate="chrome131", timeout=timeout, allow_redirects=True, headers=headers)
    except Exception:
        return None, None
    try:
        ensure_public_url(str(response.url))
    except ValueError:
        return None, None
    ctype = response.headers.get("content-type") if response.headers else ""
    return response.content, ctype


def _image_via_httpx(url, headers, timeout):
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
            response = client.get(url)
    except Exception:
        return None, None
    try:
        ensure_public_url(str(response.url))
    except ValueError:
        return None, None
    return response.content, response.headers.get("content-type")


def _image_media_type(content: bytes, header: str) -> str | None:
    header = header.split(";")[0].strip().lower()
    if header.startswith("image/") and header != "image/svg+xml":
        return header
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "image/webp"
    if len(content) > 12 and content[4:8] == b"ftyp":
        return "image/avif"
    return None
