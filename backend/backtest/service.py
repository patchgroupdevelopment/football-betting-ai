"""Runs the backtest, stores the report (``model_runs``, kind "backtest") and applies calibrated rules."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select

from backend.backtest.data import load_history
from backend.backtest.engine import run_backtest
from backend.backtest.report import build_report, params_from_config
from backend.backtest.simulate import Params
from backend.backtest.sweep import SweepRow, choose, sweep
from backend.config import AppConfig
from backend.database.session import Database
from backend.models import ModelRun
from backend.models.constants import RunStatus
from backend.services.runs import get_latest_run
from backend.utils.timeutils import local_today, utcnow

logger = logging.getLogger(__name__)

RUN_KIND_BACKTEST = "backtest"
KEEP_REPORTS = 3


@dataclass
class BacktestOutcome:
    report: dict
    chosen: SweepRow | None = None
    applied: bool = False
    warnings: list[str] = field(default_factory=list)


class BacktestService:
    def __init__(self, db: Database, config: AppConfig, tz: ZoneInfo, cache_dir: Path, config_path: Path) -> None:
        self._db = db
        self._config = config
        self._tz = tz
        self._cache_dir = cache_dir
        self._config_path = config_path

    def run(
        self,
        *,
        months: int = 12,
        today: date | None = None,
        search: bool = False,
        apply: bool = False,
        save: bool = True,
        codes: set[str] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> BacktestOutcome:
        today = today or local_today(self._tz)
        end = today - timedelta(days=1)
        start = today - timedelta(days=round(months * 30.44))
        since = start - timedelta(days=self._config.model.fit_window_days)
        started = utcnow()

        if progress:
            progress("load")
        leagues, matches, warnings = load_history(self._config, self._cache_dir, since, today, codes=codes)
        if progress:
            progress("run")
        run = run_backtest(matches, leagues, self._config, start, end, self._tz)

        params = params_from_config(self._config)
        rows: list[SweepRow] = []
        chosen: SweepRow | None = None
        split = start + (end - start) / 2
        applied = False
        if search:
            if progress:
                progress("sweep")
            rows = sweep(run.candidates, params, split)
            chosen = choose(rows)
            if apply and chosen is not None:
                apply_params(self._config_path, chosen.params)
                params = replace(params, **_rule_fields(chosen.params))
                applied = True

        report = build_report(
            run, self._config, params=params, sweep_rows=rows, chosen=chosen, applied=applied, split=split,
            generated_at=started,
        )
        report["warnings"] = warnings
        if save:
            self._store(today, started, report)
        return BacktestOutcome(report, chosen, applied, warnings)

    def _store(self, day: date, started: datetime, report: dict) -> None:
        with self._db.session() as session:
            session.add(
                ModelRun(
                    kind=RUN_KIND_BACKTEST,
                    run_date=day,
                    started_at=started,
                    finished_at=utcnow(),
                    status=RunStatus.SUCCESS,
                    details=report,
                )
            )
            session.flush()
            keep = list(
                session.scalars(
                    select(ModelRun.id)
                    .where(ModelRun.kind == RUN_KIND_BACKTEST)
                    .order_by(ModelRun.id.desc())
                    .limit(KEEP_REPORTS)
                )
            )
            session.execute(delete(ModelRun).where(ModelRun.kind == RUN_KIND_BACKTEST, ModelRun.id.not_in(keep)))


def latest_report(db: Database) -> dict | None:
    with db.session() as session:
        run = get_latest_run(session, kind=RUN_KIND_BACKTEST)
        return dict(run.details) if run is not None and run.details else None


def _rule_fields(params: Params) -> dict:
    return {"model_weight": params.model_weight, "min_ev": params.min_ev, "min_confidence": params.min_confidence}


_CONFIG_KEYS = {
    "market_blend_weight": lambda p: f"{p.model_weight:g}",
    "min_ev": lambda p: f"{p.min_ev:g}" if p.min_ev else "0.0",
    "min_confidence": lambda p: str(p.min_confidence),
}


def apply_params(path: Path, params: Params) -> None:
    """Writes the calibrated selection rules into config.yaml, keeping its comments and layout."""
    text = path.read_text(encoding="utf-8")
    for key, render in _CONFIG_KEYS.items():
        pattern = re.compile(rf"^(\s+{key}:\s*)([0-9.]+)", re.MULTILINE)
        if len(pattern.findall(text)) != 1:
            raise ValueError(f"config.yaml: '{key}' must appear exactly once")
        text = pattern.sub(lambda m: m.group(1) + render(params), text, count=1)
    path.write_text(text, encoding="utf-8")
    logger.info("config.yaml yeniləndi: %s", _rule_fields(params))
