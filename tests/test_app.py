"""Web API, scheduler helpers and the pipeline runner."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from backend.config import Settings
from backend.container import build_container
from backend.main import create_app
from backend.presenters.messages import MessageBuilder
from backend.scheduler.jobs import build_scheduler, next_daily_run, should_catch_up
from backend.scheduler.pipeline import PipelineRunner
from tests.conftest import make_client
from tests.test_ingestion import DAY, Scenario

TZ = ZoneInfo("Asia/Baku")


def _container(db, app_config, **settings):
    return build_container(Settings(_env_file=None, database_url="sqlite://", **settings), app_config, db=db)


# ------------------------------------------------------------------ web API


def test_health_and_pyramid(db, app_config):
    client = TestClient(create_app(_container(db, app_config), start_background=False))
    assert client.get("/api/health").json() == {"status": "ok"}
    data = client.get("/api/pyramid").json()
    assert data["state"]["balance"] == 2.0 and data["state"]["remaining"] == 9998.0
    assert data["projection"]["stages_needed"] == 22


def test_matches_endpoint_before_ingestion(db, app_config):
    client = TestClient(create_app(_container(db, app_config), start_background=False))
    data = client.get("/api/matches", params={"date": "2026-10-05"}).json()
    assert data["loaded"] is False and data["priority_count"] == 0


def test_status_endpoint_without_api_key(db, app_config):
    client = TestClient(create_app(_container(db, app_config), start_background=False))
    data = client.get("/api/status").json()
    assert data["quota"] is None and "FOOTBALL_API_KEY" in data["quota_error"]


def test_dashboard_password(db, app_config):
    app = create_app(_container(db, app_config, dashboard_password=SecretStr("gizli")), start_background=False)
    client = TestClient(app)
    assert client.get("/api/health").status_code == 200  # health stays open
    denied = client.get("/api/pyramid")
    assert denied.status_code == 401 and denied.json()["detail"] == "Giriş üçün parol tələb olunur."
    assert client.get("/api/pyramid", auth=("admin", "gizli")).status_code == 200


def test_errors_are_azerbaijani(db, app_config):
    client = TestClient(create_app(_container(db, app_config), start_background=False))
    assert client.get("/yoxdur").json() == {"detail": "Səhifə tapılmadı."}
    assert client.get("/api/matches", params={"date": "dünən"}).json() == {"detail": "Sorğu parametrləri yanlışdır."}
    assert "Futbol Analiz Sistemi" in client.get("/").text


# ---------------------------------------------------------------- scheduler


def test_should_catch_up():
    morning = datetime(2026, 10, 5, 7, 59, tzinfo=TZ)
    later = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)
    assert should_catch_up(later, "08:00", already_ran=False)
    assert not should_catch_up(later, "08:00", already_ran=True)
    assert not should_catch_up(morning, "08:00", already_ran=False)


def test_next_daily_run():
    assert next_daily_run(datetime(2026, 10, 5, 7, 0, tzinfo=TZ), "08:00") == datetime(2026, 10, 5, 8, 0, tzinfo=TZ)
    assert next_daily_run(datetime(2026, 10, 5, 9, 0, tzinfo=TZ), "08:00") == datetime(2026, 10, 6, 8, 0, tzinfo=TZ)


def test_scheduler_jobs(db, app_config):
    runner = PipelineRunner(_container(db, app_config))
    scheduler = build_scheduler(runner, app_config.schedule, TZ)
    assert {job.id for job in scheduler.get_jobs()} == {"daily_pipeline", "second_run", "cache_cleanup"}


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, message: MessageBuilder) -> bool:
        self.messages.append(message.render_plain())
        return True


def test_runner_sends_daily_report_once_per_day(db, app_config, fake_api):
    Scenario(fake_api)
    container = build_container(
        Settings(_env_file=None, database_url="sqlite://"), app_config, db=db, api_client=make_client(fake_api, db=db)
    )
    notifier = RecordingNotifier()
    runner = PipelineRunner(container, notifier)

    result = asyncio.run(runner.run_daily(DAY, trigger="catch_up"))
    assert result is not None and result.ingestion.status == "success" and result.analysis is not None
    assert len(notifier.messages) == 1 and "GÜNÜN FUTBOL ANALİZİ" in notifier.messages[0]

    asyncio.run(runner.run_daily(DAY, trigger="schedule"))  # same day: report not repeated
    assert len(notifier.messages) == 1

    asyncio.run(runner.run_daily(DAY, trigger="manual"))  # manual run: ingestion report + overview
    assert len(notifier.messages) == 3 and "MƏLUMAT YÜKLƏNMƏSİ" in notifier.messages[1]


def test_runner_reports_missing_api_key(db, app_config):
    notifier = RecordingNotifier()
    runner = PipelineRunner(_container(db, app_config), notifier)
    assert asyncio.run(runner.run_daily(date(2026, 10, 5))) is None
    assert notifier.messages == ["⚠️ FOOTBALL_API_KEY təyin edilməyib. Açarı .env faylına əlavə edin."]
