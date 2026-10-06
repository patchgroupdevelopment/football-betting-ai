"""Dependency wiring: one place that builds the services the app and CLI use."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import date
from zoneinfo import ZoneInfo

from backend.config import AppConfig, Settings, get_config, get_settings
from backend.database.session import Database
from backend.services.analysis import AnalysisReport, AnalysisService
from backend.services.cache import ResponseCache
from backend.services.external.football_data_couk import FootballDataCoUk
from backend.services.external.football_data_org import FootballDataOrgClient
from backend.services.external.sync import ExternalDataSync, ExternalSyncReport
from backend.services.ingestion import IngestionReport, IngestionService
from backend.services.providers.api_football import ApiFootballClient
from backend.services.pyramid import PyramidService


@dataclass
class AppContainer:
    settings: Settings
    config: AppConfig
    db: Database
    tz: ZoneInfo
    cache: ResponseCache
    _api_client: ApiFootballClient | None = None
    _client_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def api_client(self) -> ApiFootballClient:
        """Shared client, so quota tracking spans scheduled runs, /status and the CLI."""
        with self._client_lock:
            if self._api_client is None:
                self._api_client = ApiFootballClient(
                    self.settings.football_api_key.get_secret_value(),
                    self.config.api_football,
                    self.config.cache_ttl_seconds,
                    self.cache,
                )
            return self._api_client

    def ingestion_service(self) -> IngestionService:
        return IngestionService(self.db, self.api_client(), self.config, self.settings.timezone)

    def external_sync(self) -> ExternalDataSync:
        """Free sources. football-data.co.uk needs no key; football-data.org only when its key is set."""
        cfg = self.config.external_data
        couk = FootballDataCoUk(cfg.football_data_couk, self.cache, cfg.cache_hours)
        org_key = self.settings.football_data_org_key.get_secret_value()
        org = FootballDataOrgClient(org_key, cfg.football_data_org, self.cache, cfg.cache_hours) if org_key else None
        return ExternalDataSync(self.db, self.config, couk, org)

    def run_external_sync(self, day: date) -> ExternalSyncReport | None:
        if not self.config.external_data.enabled:
            return None
        sync = self.external_sync()
        try:
            return sync.run(day)
        finally:
            sync.close()

    def run_ingestion(self, day: date) -> IngestionReport:
        """Free sources first (history), then API-Football (today's fixtures, odds, injuries)."""
        external = self.run_external_sync(day)
        report = self.ingestion_service().run_daily(day)
        report.external = external
        return report

    def run_analysis(self, day: date) -> AnalysisReport:
        return AnalysisService(self.db, self.config, self.tz).run_daily(day)

    @property
    def daily_cutoff(self) -> str:
        """The daily run time; one day's analysis window ends at this time the next morning."""
        return self.config.schedule.daily_pipeline

    def pyramid_service(self) -> PyramidService:
        return PyramidService(self.db, self.config.bankroll)

    def close(self) -> None:
        if self._api_client is not None:
            self._api_client.close()
        self.db.dispose()


def build_container(
    settings: Settings | None = None,
    config: AppConfig | None = None,
    *,
    db: Database | None = None,
    api_client: ApiFootballClient | None = None,
) -> AppContainer:
    settings = settings or get_settings()
    config = config or get_config()
    db = db or Database(settings.database_url)
    return AppContainer(
        settings=settings,
        config=config,
        db=db,
        tz=ZoneInfo(settings.timezone),
        cache=ResponseCache(db),
        _api_client=api_client,
    )
