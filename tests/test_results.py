"""Settling live picks, the paper pyramid that follows them, statistics, /neticeler, /backtest and the dashboard."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from backend.config import BankrollConfig, Settings
from backend.container import build_container
from backend.dashboard.build import build_site, render
from backend.dashboard.collect import collect
from backend.models import Bet, League, Match, ModelRun, OddsSnapshot, Prediction, Team
from backend.models.constants import BetStatus, RunStatus, StageStatus
from backend.presenters.results import format_settlement, format_track_record
from backend.scheduler.pipeline import PipelineRunner
from backend.services.pyramid import PyramidService
from backend.services.results import ResultsService, track_record
from backend.telegram.commands import Incoming, respond

TZ = ZoneInfo("Asia/Baku")
DAY = date(2026, 10, 5)
NOW = datetime(2026, 10, 5, 4, 0)
CHAT = 1018324710


def _seed(db) -> dict[str, int]:
    """Two matches with a top pick and a paper bet each: one played yesterday, one tonight."""
    with db.session() as s:
        league = League(api_id=39, name="Premier League", name_az="İngiltərə — Premyer Liqa", priority=0)
        teams = [Team(name=n) for n in ("Arsenal", "Wolves", "Chelsea", "Fulham")]
        s.add_all([league, *teams])
        s.flush()
        played = Match(api_id=1, league_id=league.id, season=2026, kickoff_utc=NOW - timedelta(hours=14),
                       home_team_id=teams[0].id, away_team_id=teams[1].id, status="FT", home_goals=2, away_goals=0,
                       is_priority=True)
        tonight = Match(api_id=2, league_id=league.id, season=2026, kickoff_utc=NOW + timedelta(hours=14),
                        home_team_id=teams[2].id, away_team_id=teams[3].id, status="NS", is_priority=True)
        s.add_all([played, tonight])
        s.flush()
        for book, price in (("A", 1.45), ("B", 1.40)):  # last prices before kick-off
            for selection, odds in (("1", price), ("X", 4.4), ("2", 7.5)):
                s.add(OddsSnapshot(match_id=played.id, bookmaker=book, market="1X2", selection=selection,
                                   price=odds, captured_at=played.kickoff_utc - timedelta(hours=1)))
        ids = {}
        for key, match, run_date in (("played", played, DAY - timedelta(days=1)), ("tonight", tonight, DAY)):
            prediction = Prediction(match_id=match.id, run_date=run_date, rank=1, market="1X2", selection="1",
                                    line=None, best_odds=1.50, final_prob=0.7, decision="bet", model_version="t")
            s.add(prediction)
            s.flush()
            bet = Bet(prediction_id=prediction.id, is_paper=True, stake=1.0, odds_taken=1.50, status=BetStatus.PENDING)
            s.add(bet)
            s.flush()
            ids[key] = bet.id
            ids[f"{key}_match"] = match.id
        return ids


def _service(db, paper: bool = True) -> tuple[ResultsService, PyramidService]:
    pyramid = PyramidService(db, BankrollConfig(starting=2, target=100))
    return ResultsService(db, pyramid, paper_mode=paper), pyramid


def test_finished_picks_are_settled_with_profit_and_clv(db):
    ids = _seed(db)
    service, _ = _service(db)
    report = service.settle(NOW)
    assert [b.bet_id for b in report.settled] == [ids["played"]]
    settled = report.settled[0]
    assert settled.status == BetStatus.WON and settled.pnl == 0.5 and settled.score == "2:0"
    with db.session() as s:
        bet = s.get(Bet, ids["played"])
        assert bet.odds_closing == 1.45 and bet.clv is not None and bet.clv > 0  # 1.50 beat the closing price
        assert s.get(Bet, ids["tonight"]).status == BetStatus.PENDING
    assert service.settle(NOW).settled == []  # settled only once


def test_cancelled_match_is_void(db):
    ids = _seed(db)
    with db.session() as s:
        s.get(Match, ids["tonight_match"]).status = "PST"
    report, _ = _service(db)[0].settle(NOW), None
    void = [b for b in report.settled if b.bet_id == ids["tonight"]]
    assert void and void[0].status == BetStatus.VOID and void[0].pnl == 0.0


def test_paper_pyramid_follows_the_top_pick(db):
    ids = _seed(db)
    service, pyramid = _service(db)
    assert service.open_stage(DAY, NOW) is True
    assert pyramid.get_state().pending_odds == 1.50
    assert service.open_stage(DAY, NOW) is False  # one stage at a time
    with db.session() as s:
        match = s.get(Match, ids["tonight_match"])
        match.status, match.home_goals, match.away_goals = "FT", 1, 0
    report = service.settle(NOW + timedelta(days=1))
    assert report.stage_settled == StageStatus.WON
    assert report.pyramid.balance == 3.0 and report.pyramid.stage_no == 2


def test_no_automatic_pyramid_outside_paper_mode(db):
    _seed(db)
    service, pyramid = _service(db, paper=False)
    assert service.open_stage(DAY, NOW) is False and pyramid.get_state().pending_odds is None


def test_track_record_and_messages(db):
    _seed(db)
    service, pyramid = _service(db)
    report = service.settle(NOW)
    record = track_record(db)
    assert (record.bets, record.wins, record.pending) == (1, 1, 1) and record.roi == 0.5
    text = format_settlement(report, record).render_plain()
    assert "NƏTİCƏLƏR" in text and "Arsenal – Wolves 2:0" in text and "+0.50" in text and "100%" in text
    stats = format_track_record(record, track_record(db, top_only=True), record, pyramid.get_state(), None).render_plain()
    assert "CANLI NƏTİCƏLƏR" in stats


def _container(db, app_config):
    settings = Settings(_env_file=None, database_url="sqlite://", telegram_chat_id=str(CHAT))
    return build_container(settings, app_config, db=db)


def _reply(container, text: str) -> str:
    return asyncio.run(respond(container, PipelineRunner(container), Incoming(CHAT, "Fuad", text))).render_plain()


def _store_backtest(db) -> None:
    report = {
        "period": {"start": "2025-10-06", "end": "2026-10-05"}, "leagues": ["Liqa"], "matches": 10, "candidates": 4,
        "params": {"model_weight": 0.1, "min_ev": 0.0, "min_confidence": 55, "max_picks": 3},
        "summary": {"bets": 2, "wins": 1, "losses": 1, "pushes": 0, "hit_rate": 0.5, "expected_hit_rate": 0.6,
                    "avg_odds": 1.5, "profit": -0.5, "roi": -0.25, "max_drawdown": 1.0, "longest_losing_streak": 1,
                    "avg_clv": 0.01, "clv_positive": 0.5, "clv_bets": 2},
        "median_price": {"bets": 0}, "baseline": {"bets": 0}, "periods": {}, "pyramid": {}, "bets": [], "notes": ["best_price"],
    }
    with db.session() as s:
        s.add(ModelRun(kind="backtest", run_date=DAY, status=RunStatus.SUCCESS, details=report))


def test_results_and_backtest_commands(db, app_config):
    container = _container(db, app_config)
    assert "CANLI NƏTİCƏLƏR" in _reply(container, "/neticeler")
    assert "backtest aparılmayıb" in _reply(container, "/backtest")
    _store_backtest(db)
    reply = _reply(container, "/backtest")
    assert "BACKTEST" in reply and "-25.0%" in reply


def test_dashboard_contains_data_but_no_secrets(db, app_config, tmp_path):
    _seed(db)
    _store_backtest(db)
    data = collect(db, app_config, TZ, DAY, paper_mode=True)
    assert data["today"]["picks"][0]["pick"] and data["backtest"]["summary"]["bets"] == 2
    html = render(data)
    assert "/*__DATA__*/" not in html
    embedded = html.split('id="data">', 1)[1].split("</script>", 1)[0]
    assert json.loads(embedded)["labels"]["title"] == "Futbol Analiz Paneli"
    page = build_site(db, app_config, TZ, DAY, tmp_path, paper_mode=True)
    text = page.read_text(encoding="utf-8")
    assert "Chelsea" in text
    for secret_name in ("FOOTBALL_API_KEY", "TELEGRAM_BOT_TOKEN", "X-Auth-Token", "api_key"):
        assert secret_name not in text
    assert (tmp_path / ".nojekyll").exists()


# ------------------------------------------------------------------ price check at the user's bookmaker


def test_minimum_odds_is_the_fair_price_rounded_up():
    from backend.presenters.formatters import min_odds

    class View:
        p_final = 0.7

    assert min_odds(View()) == 1.43  # 1 / 0.7 = 1.4286 → never below fair
    View.p_final = 0.5
    assert min_odds(View()) == 2.0


def test_misli_command_checks_a_price(db, app_config):
    ids = _seed(db)
    container = _container(db, app_config)
    match_id = ids["tonight_match"]
    bad = _reply(container, f"/misli_{match_id} 1,30")  # comma decimal is accepted
    assert "ƏMSAL YOXLAMASI" in bad and "⛔" in bad and "1.30" in bad
    good = _reply(container, f"/misli {match_id} 1.55")
    assert "✅" in good and "Piramida" in good
    assert "İstifadə" in _reply(container, "/misli")
    assert "seçimi yoxdur" in _reply(container, "/misli_99999 1.5")
    assert "1.01" in _reply(container, f"/misli_{match_id} 0.5")


def test_pick_card_shows_the_minimum_price_at_the_users_bookmaker(db, app_config):
    from backend.presenters.formatters import format_match_detail
    from backend.services.picks import get_match_analysis

    ids = _seed(db)
    view = get_match_analysis(db, ids["tonight_match"], TZ)
    text = format_match_detail(view, user_bookmaker="Misli.az").render_plain()
    assert "Misli.az-da əmsal ən azı 1.43" in text and f"/misli_{ids['tonight_match']}" in text
    assert "Misli" not in format_match_detail(view).render_plain()


def test_price_checks_are_stored_and_summarised(db, app_config):
    from backend.services.price_checks import bookmaker_stats

    ids = _seed(db)
    config = app_config.model_copy(
        update={"selection": app_config.selection.model_copy(update={"user_bookmaker": "Misli.az"})}
    )
    container = _container(db, config)
    played, tonight = ids["played_match"], ids["tonight_match"]
    _reply(container, f"/misli_{played} 1.30")  # below the minimum (1.43): ⛔
    _reply(container, f"/misli_{tonight} 1.40")  # first look ...
    _reply(container, f"/misli_{tonight} 1.50")  # ... the latest check of a pick counts: ✅

    stats = bookmaker_stats(db, "Misli.az")
    assert (stats.checks, stats.passed) == (2, 1)
    assert stats.gap_to_fair == pytest.approx(((1.30 / 1.43 - 1) + (1.50 / 1.43 - 1)) / 2)
    # Only the played match is settled: 2:0, home win at 1.30 → +0.30
    assert stats.all_results.bets == 1 and stats.all_results.profit == pytest.approx(0.30)
    assert stats.passed_results.bets == 0

    text = _reply(container, "/misli")
    assert "MİSLİ.AZ YOXLAMALARI" in text and "Yoxlanan seçim: 2" in text
    assert "Misli.az: 2 yoxlama" in _reply(container, "/neticeler")
    data = collect(db, config, TZ, DAY, paper_mode=True)
    assert data["prices"]["checks"] == 2
