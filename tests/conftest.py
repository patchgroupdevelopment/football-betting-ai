"""Shared fixtures: in-memory database, test configuration and a fake API-Football."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from backend.config import ApiFootballConfig, AppConfig, CacheTtlConfig, LeagueConfig
from backend.database.session import Database
from backend.services.cache import ResponseCache
from backend.services.providers.api_football import ApiFootballClient
from tests.factories import envelope

Payload = dict[str, Any] | httpx.Response | Callable[[dict[str, str]], "dict[str, Any] | httpx.Response"]


class FakeApiFootball:
    """Routes httpx requests to canned API-Football payloads and records every call."""

    def __init__(self, daily_limit: int = 7500) -> None:
        self.routes: list[tuple[str, Callable[[dict[str, str]], bool], Payload]] = []
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.daily_limit = daily_limit
        self.daily_remaining = daily_limit

    def route(self, path: str, payload: Payload, when: Callable[[dict[str, str]], bool] | None = None) -> FakeApiFootball:
        self.routes.append((path, when or (lambda _params: True), payload))
        return self

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.lstrip("/")
        params = dict(request.url.params)
        self.calls.append((path, params))
        self.daily_remaining -= 1
        headers = {
            "x-ratelimit-requests-limit": str(self.daily_limit),
            "x-ratelimit-requests-remaining": str(self.daily_remaining),
            "X-RateLimit-Limit": "300",
            "X-RateLimit-Remaining": "299",
        }
        for route_path, when, payload in self.routes:
            if route_path == path and when(params):
                body = payload(params) if callable(payload) else payload
                if isinstance(body, httpx.Response):
                    return body
                return httpx.Response(200, json=body, headers=headers)
        return httpx.Response(200, json=envelope(path, []), headers=headers)

    def count(self, path: str, predicate: Callable[[dict[str, str]], bool] | None = None) -> int:
        return sum(1 for p, params in self.calls if p == path and (predicate is None or predicate(params)))


@pytest.fixture
def db() -> Database:
    database = Database("sqlite://")
    database.create_all()
    yield database
    database.dispose()


@pytest.fixture
def app_config() -> AppConfig:
    return AppConfig(
        leagues=[
            LeagueConfig(api_id=39, name_az="İngiltərə — Premyer Liqa", tier=1, country="England"),
            LeagueConfig(api_id=140, name_az="İspaniya — La Liqa", tier=1, country="Spain"),
        ],
        api_football=ApiFootballConfig(requests_per_minute=10_000, max_retries=1, daily_reserve=5),
    )


@pytest.fixture
def fake_api() -> FakeApiFootball:
    return FakeApiFootball()


def make_client(
    fake: FakeApiFootball,
    *,
    db: Database | None = None,
    config: ApiFootballConfig | None = None,
    ttl: CacheTtlConfig | None = None,
    sleeps: list[float] | None = None,
) -> ApiFootballClient:
    recorded = sleeps if sleeps is not None else []
    return ApiFootballClient(
        "test-key",
        config or ApiFootballConfig(requests_per_minute=10_000, max_retries=1),
        ttl or CacheTtlConfig(),
        ResponseCache(db) if db is not None else None,
        transport=httpx.MockTransport(fake.handler),
        sleep=recorded.append,
    )
