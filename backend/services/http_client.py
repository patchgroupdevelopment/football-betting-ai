"""Resilient JSON-over-HTTP client: timeouts, retries with backoff, rate limiting."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from backend.services.errors import (
    ProviderAuthError,
    ProviderError,
    ProviderResponseError,
    ProviderUnavailableError,
    RateLimitedError,
)

logger = logging.getLogger(__name__)

RETRY_AFTER_CAP_SECONDS = 120.0


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    backoff_base: float = 1.0
    backoff_max: float = 30.0

    def delay(self, attempt: int) -> float:
        return min(self.backoff_max, self.backoff_base * 2**attempt) + random.uniform(0, 0.5)


@dataclass(frozen=True)
class HttpResponseData:
    data: Any
    headers: httpx.Headers
    status_code: int


class RateLimiter:
    """Sliding-window limiter: at most ``per_minute`` requests in any 60 seconds."""

    WINDOW_SECONDS = 60.0

    def __init__(
        self,
        per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._per_minute = max(1, per_minute)
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = self._clock()
            if now < self._blocked_until:
                self._sleep(self._blocked_until - now)
                now = self._clock()
            while self._stamps and now - self._stamps[0] >= self.WINDOW_SECONDS:
                self._stamps.popleft()
            if len(self._stamps) >= self._per_minute:
                wait = self.WINDOW_SECONDS - (now - self._stamps[0])
                if wait > 0:
                    self._sleep(wait)
                    now = self._clock()
                self._stamps.popleft()
            self._stamps.append(now)

    def block_for(self, seconds: float) -> None:
        """Pause all requests, e.g. when the server reports the minute quota is used up."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + seconds)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return min(max(float(value), 0.0), RETRY_AFTER_CAP_SECONDS)
    except ValueError:
        return None


class HttpJsonClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: Mapping[str, str] | None = None,
        timeout: float = 20.0,
        retry: RetryPolicy | None = None,
        rate_limiter: RateLimiter | None = None,
        auth_key_name: str = "API_KEY",
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=dict(headers or {}),
            timeout=timeout,
            transport=transport,
            follow_redirects=True,  # football-data.co.uk redirects file requests
        )
        self._retry = retry or RetryPolicy()
        self._limiter = rate_limiter
        self._auth_key_name = auth_key_name
        self._sleep = sleep

    def close(self) -> None:
        self._client.close()

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> HttpResponseData:
        response = self._get(path, params)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderResponseError("response is not valid JSON") from exc
        return HttpResponseData(data=data, headers=response.headers, status_code=response.status_code)

    def get_text(self, path: str, params: Mapping[str, Any] | None = None) -> str:
        """Body as text (CSV files). UTF-8 with a Latin-1 fallback, BOM removed."""
        content = self._get(path, params).content
        try:
            return content.decode("utf-8-sig")
        except UnicodeDecodeError:
            return content.decode("latin-1")

    def _get(self, path: str, params: Mapping[str, Any] | None) -> httpx.Response:
        attempt = 0
        while True:
            if self._limiter is not None:
                self._limiter.acquire()
            retry_after: float | None = None
            error: ProviderError
            try:
                response = self._client.get(path, params=dict(params or {}))
            except httpx.TimeoutException as exc:
                error = ProviderUnavailableError(f"timeout: {exc!r}")
            except httpx.TransportError as exc:
                error = ProviderUnavailableError(f"transport error: {exc!r}")
            else:
                status = response.status_code
                if status in (401, 403):
                    detail = f"HTTP {status}: {response.text[:200]}"
                    raise ProviderAuthError(detail, key_name=self._auth_key_name)
                if status == 429 or status >= 500:
                    error = (
                        RateLimitedError(f"HTTP {status}")
                        if status == 429
                        else ProviderUnavailableError(f"HTTP {status}")
                    )
                    # football-data.org reports the wait in X-RequestCounter-Reset instead of Retry-After.
                    retry_after = _parse_retry_after(
                        response.headers.get("retry-after") or response.headers.get("x-requestcounter-reset")
                    )
                elif status == 404:
                    raise ProviderResponseError("HTTP 404")
                elif status >= 400:
                    raise ProviderResponseError(f"HTTP {status}")
                else:
                    return response

            if attempt >= self._retry.max_retries:
                raise error
            delay = retry_after if retry_after is not None else self._retry.delay(attempt)
            logger.warning(
                "Sorğu alınmadı (%s): %s — %.1f san. sonra təkrar cəhd (%d/%d)",
                path, error.detail, delay, attempt + 1, self._retry.max_retries,
            )
            self._sleep(delay)
            attempt += 1
