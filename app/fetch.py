"""Fetch product pages with a browser-like client.

curl_cffi impersonates Chrome's TLS fingerprint. Amazon.com.br answers
stock httpx with HTTP 503 from this kind of network and returns the real
product page to the impersonated client. httpx remains the fallback.
"""

from __future__ import annotations

import gzip
import ipaddress
import logging
import os
import socket
import zlib
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("wishly.fetch")

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


def accept_encoding() -> str:
    """Advertise brotli only when the decoder is installed."""
    try:
        import brotli  # noqa: F401
    except ImportError:
        return "gzip, deflate"
    return "gzip, deflate, br"


BROWSER_HEADERS["Accept-Encoding"] = accept_encoding()

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
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified or not ip.is_global:
            logger.warning("blocked non-public host %s", host)
            raise ValueError("Esse endereço aponta para uma rede interna e não pode ser lido.")
    return url.strip()


def _worth_keeping(result: FetchResult | None) -> bool:
    return bool(result and result.status and result.status < 400 and len(result.html or "") > 800)


def _log_fetch(url: str, result: FetchResult) -> FetchResult:
    logger.info(
        "fetch status=%s via=%s final=%s len=%s url=%s",
        result.status,
        result.via,
        result.final_url,
        len(result.html or ""),
        url,
    )
    return result


def _amazon_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return "amazon." in host


def fetch_html(url: str, timeout: float = 10.0) -> FetchResult:
    ensure_public_url(url)
    primary: FetchResult | None = None
    try:
        primary = _amazon_curl(url, timeout) if _amazon_host(url) else _with_curl(url, timeout)
    except ValueError:
        raise
    except TimeoutError:
        primary = FetchResult(False, url, url, None, "", "A página demorou demais para responder.", "curl_cffi")
    except Exception:
        logger.info("curl_cffi could not fetch %s", url)
    if primary is not None and _worth_keeping(primary) and not _looks_like_amazon_wall(primary.html):
        return _log_fetch(url, primary)
    try:
        secondary = _with_httpx(url, timeout)
    except ValueError:
        raise
    except TimeoutError:
        if primary is not None:
            return _log_fetch(url, primary)
        return _log_fetch(url, FetchResult(False, url, url, None, "", "A página demorou demais para responder.", "httpx"))
    except Exception:
        logger.info("httpx could not fetch %s", url)
        secondary = None
    if secondary is not None and _worth_keeping(secondary):
        return _log_fetch(url, secondary)
    if primary is not None and (secondary is None or (primary.status or 999) <= (secondary.status or 999)):
        return _log_fetch(url, primary)
    if secondary is not None:
        return _log_fetch(url, secondary)
    return _log_fetch(url, FetchResult(False, url, url, None, "", "Não foi possível acessar a página da loja.", None))


def proxy_configured() -> bool:
    return bool((os.environ.get("WISHLY_FETCH_PROXY_URL") or "").strip() or (os.environ.get("SCRAPER_API_KEY") or "").strip())


def proxy_targets(url: str) -> list[tuple[str, dict[str, str], dict[str, str]]]:
    """Request specs for the optional proxy. Empty unless an env var is set."""
    targets: list[tuple[str, dict[str, str], dict[str, str]]] = []
    worker = (os.environ.get("WISHLY_FETCH_PROXY_URL") or "").strip()
    if worker:
        secret = (os.environ.get("WISHLY_PROXY_SECRET") or "").strip()
        headers = {"WISHLY_PROXY_SECRET": secret} if secret else {}
        targets.append((worker, {"url": url}, headers))
    key = (os.environ.get("SCRAPER_API_KEY") or "").strip()
    if key:
        targets.append(("https://api.scraperapi.com/", {"api_key": key, "url": url, "country_code": "br"}, {}))
    return targets


def fetch_via_proxy(url: str, timeout: float = 20.0) -> FetchResult | None:
    """Fetch through a Worker or ScraperAPI. Does nothing when both env vars are unset."""
    targets = proxy_targets(url)
    if not targets:
        return None
    last: FetchResult | None = None
    for endpoint, params, headers in targets:
        try:
            with httpx.Client(follow_redirects=True, timeout=timeout, headers=headers) as client:
                response = client.get(endpoint, params=params)
        except Exception as exc:
            logger.info("proxy failed endpoint=%s err=%s", endpoint, exc)
            continue
        via = "proxy" if "scraperapi.com" not in endpoint else "scraperapi"
        last = _result_from_response(url, str(response.url), response.status_code, response.headers, response.content, via)
        logger.info("proxy status=%s via=%s len=%s endpoint=%s", last.status, via, len(last.html or ""), endpoint)
        if last.status and last.status < 400 and len(last.html or "") > 800:
            return last
    return last


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


_AMAZON_PROFILES = ("chrome136", "chrome131", "safari18_0", "chrome131_android")


def _looks_like_amazon_wall(html: str) -> bool:
    low = (html or "").lower()
    if "window.location.replace" in low and "/error/500" in low and len(low) < 2000:
        return True
    return any(token in low for token in ("validatecaptcha", "api-services-support@amazon.com", "opfcaptcha"))


def _amazon_curl(url: str, timeout: float) -> FetchResult | None:
    """Try a few TLS profiles. Amazon 503s are tiny, so the extra attempts stay cheap."""
    best: FetchResult | None = None
    language = {"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"}
    for profile in _AMAZON_PROFILES:
        try:
            result = _with_curl(url, timeout, profile=profile, headers=language)
        except TimeoutError:
            raise
        except Exception:
            logger.info("amazon profile %s failed for %s", profile, url)
            continue
        if _worth_keeping(result) and not _looks_like_amazon_wall(result.html):
            return result
        if best is None or len(result.html or "") > len(best.html or ""):
            best = result
    return best


def _with_curl(url: str, timeout: float, profile: str = "chrome131", headers: dict[str, str] | None = None) -> FetchResult:
    from curl_cffi import requests as creq

    try:
        response = creq.get(
            url,
            impersonate=profile,
            timeout=timeout,
            allow_redirects=True,
            headers=BROWSER_HEADERS if headers is None else headers,
        )
    except Exception as exc:
        if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
            raise TimeoutError(str(exc)) from exc
        raise
    return _result_from_response(url, str(response.url), response.status_code, response.headers, response.content, "curl_cffi")


def _with_httpx(url: str, timeout: float) -> FetchResult:
    try:
        with httpx.Client(follow_redirects=True, timeout=timeout, headers=BROWSER_HEADERS, http2=True) as client:
            response = client.get(url)
    except httpx.TimeoutException as exc:
        raise TimeoutError(str(exc)) from exc
    return _result_from_response(url, str(response.url), response.status_code, response.headers, response.content, "httpx")


def decode_body(content: bytes, content_encoding: str | None = None, charset: str | None = None) -> str:
    """Turn a response body into HTML, including gzip and brotli left compressed."""
    raw = content or b""
    encoding = (content_encoding or "").lower()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    elif "br" in {part.strip() for part in encoding.split(",")} and raw[:1] not in {b"<", b" ", b"\n", b"\r", b"\t"}:
        try:
            import brotli
        except ImportError:
            brotli = None
        if brotli is not None:
            try:
                raw = brotli.decompress(raw)
            except Exception:
                logger.warning("brotli body could not be decoded")
    elif "deflate" in encoding or raw.startswith(b"\x78"):
        try:
            raw = zlib.decompress(raw)
        except zlib.error:
            pass
    text_encoding = charset or "utf-8"
    if text_encoding in {"iso-8859-1", "latin-1"} and raw.startswith((b"<", b"\xef\xbb\xbf")):
        text_encoding = "utf-8"
    return raw.decode(text_encoding, errors="replace")


def _result_from_response(url, final_url, status, headers, content, via) -> FetchResult:
    ensure_public_url(final_url)
    ctype = ""
    if headers is not None:
        getter = getattr(headers, "get", None)
        ctype = (getter("content-type") or "") if getter else ""
    ctype = ctype.lower()
    if ctype and not any(token in ctype for token in ("html", "xml", "json", "text/", "octet-stream")):
        return FetchResult(
            True,
            url,
            final_url,
            status,
            "",
            "Esse link não parece uma página de produto.",
            via,
        )
    if isinstance(content, str):
        html = content
    else:
        encoding = ""
        charset = None
        if headers is not None:
            getter = getattr(headers, "get", None)
            if getter:
                encoding = getter("content-encoding") or ""
                if "charset=" in ctype:
                    charset = ctype.split("charset=", 1)[1].split(";", 1)[0].strip()
        html = decode_body(content or b"", encoding, charset)
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
