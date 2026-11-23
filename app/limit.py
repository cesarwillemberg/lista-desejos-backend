"""Small in-memory rate limiter for auth and store fetches."""

from __future__ import annotations

import os
import time
from collections import defaultdict
from threading import Lock

from fastapi import HTTPException, Request


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._hits[key] if now - stamp < self.window_seconds]
            if len(recent) >= self.limit:
                self._hits[key] = recent
                return False
            recent.append(now)
            self._hits[key] = recent
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def _limit_from_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


auth_limiter = RateLimiter(_limit_from_env("WISHLY_AUTH_RATE", 20))
scrape_limiter = RateLimiter(_limit_from_env("WISHLY_SCRAPE_RATE", 30))
account_limiter = RateLimiter(_limit_from_env("WISHLY_ACCOUNT_RATE", 10))


def _client_key(request: Request, bucket: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{bucket}:{host}"


def limit_auth(request: Request) -> None:
    if not auth_limiter.allow(_client_key(request, "auth")):
        raise HTTPException(429, "Muitas tentativas de entrar. Espere um minuto e tente de novo.")


def limit_account(request: Request) -> None:
    if not account_limiter.allow(_client_key(request, "account")):
        raise HTTPException(429, "Muitas alterações na conta. Espere um minuto e tente de novo.")


def limit_scrape(request: Request) -> None:
    if not scrape_limiter.allow(_client_key(request, "scrape")):
        raise HTTPException(429, "Muitas leituras de loja seguidas. Espere um minuto e tente de novo.")
