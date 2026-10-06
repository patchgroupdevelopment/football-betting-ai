from __future__ import annotations

import httpx
import pytest

from backend.services.errors import ProviderAuthError, ProviderResponseError, ProviderUnavailableError
from backend.services.http_client import HttpJsonClient, RateLimiter, RetryPolicy


def _client(handler, *, retries: int = 2, sleeps: list[float] | None = None) -> HttpJsonClient:
    return HttpJsonClient(
        "https://example.test",
        retry=RetryPolicy(max_retries=retries),
        transport=httpx.MockTransport(handler),
        sleep=(sleeps if sleeps is not None else []).append,
        auth_key_name="FOOTBALL_API_KEY",
    )


def test_retries_server_errors_then_succeeds():
    statuses = iter([500, 502, 200])
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        status = next(statuses)
        return httpx.Response(status, json={"ok": True} if status == 200 else None)

    result = _client(handler, sleeps=sleeps).get_json("/x")
    assert result.data == {"ok": True}
    assert len(sleeps) == 2 and 1.0 <= sleeps[0] <= 1.5 and 2.0 <= sleeps[1] <= 2.5


def test_honours_retry_after_on_429():
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={})])
    sleeps: list[float] = []
    _client(lambda request: next(responses), sleeps=sleeps).get_json("/x")
    assert sleeps == [7.0]


def test_gives_up_after_max_retries():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(ProviderUnavailableError) as info:
        _client(handler, retries=2).get_json("/x")
    assert len(calls) == 3
    assert info.value.user_message == "⚠️ Məlumat mənbəyindən cavab alınmadı."


def test_timeout_is_retried_and_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    with pytest.raises(ProviderUnavailableError):
        _client(handler, retries=1).get_json("/x")


def test_auth_error_is_not_retried():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(401)

    with pytest.raises(ProviderAuthError) as info:
        _client(handler).get_json("/x")
    assert len(calls) == 1
    assert "FOOTBALL_API_KEY" in info.value.user_message


def test_invalid_json_is_a_response_error():
    with pytest.raises(ProviderResponseError):
        _client(lambda request: httpx.Response(200, text="<html>")).get_json("/x")


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_rate_limiter_waits_for_the_window():
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    clock.now = 10
    limiter.acquire()
    limiter.acquire()  # third request within 60 s must wait until the first expires
    assert clock.sleeps == [50.0]


def test_rate_limiter_block_for():
    clock = FakeClock()
    limiter = RateLimiter(100, clock=clock, sleep=clock.sleep)
    limiter.block_for(60)
    limiter.acquire()
    assert clock.sleeps == [60.0]
