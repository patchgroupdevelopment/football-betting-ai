"""Backtest: settlement rules, historical odds, walk-forward engine, selection replay, pyramid, calibration."""

from __future__ import annotations

import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from backend.backtest.data import HistoricalMatch, LeagueSource, build_matches
from backend.backtest.engine import CandidateRecord, run_backtest
from backend.backtest.history import HistoryBook, build_context
from backend.backtest.report import build_report, params_from_config
from backend.backtest.service import apply_params
from backend.backtest.simulate import Params, SimBet, select_bets, simulate_pyramid, value
from backend.backtest.sweep import SweepRow, choose
from backend.config import AppConfig, BankrollConfig, LeagueConfig, SelectionConfig
from backend.models.constants import Decision
from backend.predictors.market_odds import OddsQuote
from backend.presenters.backtest import format_backtest_report
from backend.services.external.football_data_couk import parse_csv, parse_odds
from backend.services.settlement import FinalScore, profit, settle

TZ = ZoneInfo("Asia/Baku")


# ------------------------------------------------------------------ settlement


@pytest.mark.parametrize(
    ("market", "selection", "line", "score", "expected"),
    [
        ("1X2", "1", None, FinalScore(2, 1), "win"),
        ("1X2", "2", None, FinalScore(2, 1), "loss"),
        ("DC", "1X", None, FinalScore(1, 1), "win"),
        ("DC", "X2", None, FinalScore(2, 0), "loss"),
        ("DNB", "1", None, FinalScore(0, 0), "push"),
        ("DNB", "2", None, FinalScore(0, 1), "win"),
        ("BTTS", "YES", None, FinalScore(1, 0), "loss"),
        ("BTTS", "NO", None, FinalScore(3, 0), "win"),
        ("OU", "OVER", 2.5, FinalScore(2, 1), "win"),
        ("OU", "UNDER", 2.5, FinalScore(2, 1), "loss"),
        ("OU_1H", "OVER", 0.5, FinalScore(1, 0, 0, 0), "loss"),
        ("OU_1H", "OVER", 0.5, FinalScore(1, 0), None),  # half-time score unknown
        ("AH", "1", -1.5, FinalScore(2, 0), "win"),
        ("AH", "2", 1.5, FinalScore(2, 0), "loss"),
        ("AH", "1", -1.0, FinalScore(2, 1), "push"),
        ("TEAM_TOTAL_AWAY", "UNDER", 1.5, FinalScore(0, 1), "win"),
        ("CORNERS_OU", "OVER", 9.5, FinalScore(1, 1, corners=10), "win"),
        ("CARDS_OU", "UNDER", 4.5, FinalScore(1, 1), None),  # no card count
    ],
)
def test_settle(market, selection, line, score, expected):
    assert settle(market, selection, line, score) == expected


def test_profit():
    assert profit("win", 1.5, 2.0) == pytest.approx(1.0)
    assert profit("loss", 1.5) == -1.0
    assert profit("push", 1.5) == 0.0


# ------------------------------------------------------------------ historical odds

CSV = (
    "Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,HTHG,HTAG,B365H,B365D,B365A,PSH,PSD,PSA,MaxH,MaxD,MaxA,"
    "B365>2.5,B365<2.5,P>2.5,P<2.5,AHh,B365AHH,B365AHA,B365CH,B365CD,B365CA,AHCh,B365CAHH,B365CAHA\n"
    "E0,16/08/2026,15:00,Arsenal,Wolves,2,0,1,0,1.30,5.50,9.00,1.32,5.60,9.50,1.40,6.00,11.00,"
    "1.60,2.30,1.62,2.40,-1.5,1.95,1.90,1.25,6.00,12.00,-1.75,1.92,1.94\n"
)


def test_csv_odds_keep_individual_bookmakers_opening_and_closing():
    match = parse_csv(CSV, "E0", season=2026, with_odds=True)[0]
    books = {q.bookmaker for q in match.odds}
    assert books == {"Bet365", "Pinnacle"}  # Max/Avg aggregates are not a bookmaker
    home = {(q.bookmaker, q.price) for q in match.odds if q.market == "1X2" and q.selection == "1"}
    assert home == {("Bet365", 1.30), ("Pinnacle", 1.32)}
    ah = {(q.selection, q.line) for q in match.odds if q.market == "AH"}
    assert ah == {("1", -1.5), ("2", 1.5)}  # AHh is the home team's handicap
    assert {(q.market, q.selection, q.line) for q in match.odds if q.market == "OU"} == {("OU", "OVER", 2.5), ("OU", "UNDER", 2.5)}
    closing_ah = {(q.selection, q.line) for q in match.closing_odds if q.market == "AH"}
    assert closing_ah == {("1", -1.75), ("2", 1.75)}
    assert parse_csv(CSV, "E0", season=2026)[0].odds == ()  # odds only when asked for


def test_parse_odds_skips_empty_prices():
    assert parse_odds({"B365H": "", "B365D": "3.4", "B365A": "1.0"}, closing=False) == (
        OddsQuote("Bet365", None, "1X2", "X", None, 3.4),
    )


# ------------------------------------------------------------------ synthetic history

TEAMS = [f"Team {i}" for i in range(10)]


def _quotes(p_home: float, p_draw: float, total_over: float, margin: float = 1.05) -> tuple[OddsQuote, ...]:
    p_away = 1 - p_home - p_draw
    quotes = []
    for book, shade in (("A", 1.0), ("B", 1.02), ("C", 0.99)):
        for selection, p in (("1", p_home), ("X", p_draw), ("2", p_away)):
            quotes.append(OddsQuote(book, None, "1X2", selection, None, round(shade / (p * margin), 2)))
        quotes.append(OddsQuote(book, None, "OU", "OVER", 2.5, round(shade / (total_over * margin), 2)))
        quotes.append(OddsQuote(book, None, "OU", "UNDER", 2.5, round(shade / ((1 - total_over) * margin), 2)))
    return tuple(quotes)


def synthetic_matches(days: int = 300, seed: int = 7) -> list[HistoricalMatch]:
    """Round-robin rounds every 3–4 days with strength-driven scores and fair-ish odds."""
    rng = random.Random(seed)
    strength = {i: 0.6 + 0.12 * i for i in range(len(TEAMS))}
    start = datetime(2025, 8, 1, 14, 0)
    matches: list[HistoricalMatch] = []
    for round_no in range(days // 3):
        kickoff = start + timedelta(days=3 * round_no)
        order = list(range(len(TEAMS)))
        rng.shuffle(order)
        for k in range(0, len(order), 2):
            home, away = order[k], order[k + 1]
            lam_h, lam_a = 1.35 * strength[home] / strength[away] ** 0.5, 1.1 * strength[away] / strength[home] ** 0.5

            def poisson(lam: float) -> int:
                limit, n, prod = math.exp(-lam), 0, rng.random()
                while prod > limit:
                    n += 1
                    prod *= rng.random()
                return n

            hg, ag = poisson(lam_h), poisson(lam_a)
            p_home = min(0.85, max(0.1, 0.45 + 0.35 * (strength[home] - strength[away])))
            quotes = _quotes(p_home, 0.25 if p_home < 0.7 else 0.15, 0.55)
            matches.append(
                HistoricalMatch(
                    index=len(matches), league_id=39, season=2025, kickoff_utc=kickoff + timedelta(minutes=k),
                    home_id=home + 1, away_id=away + 1, home=TEAMS[home], away=TEAMS[away],
                    home_goals=hg, away_goals=ag, ht_home_goals=hg // 2, ht_away_goals=ag // 2,
                    home_xg=None, away_xg=None, home_corners=None, away_corners=None, home_cards=None, away_cards=None,
                    odds=quotes, closing_odds=quotes,
                )
            )
    return matches


def _config(**selection) -> AppConfig:
    return AppConfig(
        leagues=[LeagueConfig(api_id=39, name_az="İngiltərə — Premyer Liqa", tier=1, country="England", fd_couk="E0")],
        selection=SelectionConfig(**selection) if selection else SelectionConfig(),
    )


LEAGUES = [LeagueSource(39, "E0", "İngiltərə — Premyer Liqa")]


def test_history_book_sees_only_what_was_added():
    matches = synthetic_matches(days=60)
    book = HistoryBook()
    for m in matches[:20]:
        book.add(m)
    history = book.history(matches[0].home_id)
    assert history and all(h.kickoff_utc <= matches[19].kickoff_utc for h in history)
    assert history == sorted(history, key=lambda h: h.kickoff_utc, reverse=True)  # newest first
    target = matches[25]
    ctx = build_context(book, target, target.odds, "Liqa", TZ)
    assert all(h.match_id < 20 for h in ctx.home.history + ctx.away.history)
    assert ctx.quality.components["injuries"] and not ctx.quality.components["lineups"]


def test_build_matches_assigns_stable_team_ids_and_drops_duplicates():
    rows = parse_csv(CSV, "E0", season=2026, with_odds=True)
    matches = build_matches({LEAGUES[0]: rows + rows})
    assert len(matches) == 1 and matches[0].home_id != matches[0].away_id


@pytest.fixture(scope="module")
def backtest_run():
    matches = synthetic_matches()
    config = _config(min_confidence=0, min_ev=0.0)
    end = matches[-1].kickoff_utc.date()
    return config, run_backtest(matches, LEAGUES, config, end - timedelta(days=90), end, TZ)


def test_walk_forward_evaluates_every_match_in_the_period_with_known_outcomes(backtest_run):
    config, run = backtest_run
    assert run.matches and run.candidates
    assert all(run.start <= m.day <= run.end for m in run.matches)
    assert all(c.outcome in ("win", "loss", "push") for c in run.candidates)
    for c in run.candidates[:50]:
        match = run.match(c.match_index)
        assert c.outcome == settle(c.market, c.selection, c.line, FinalScore(match.home_goals, match.away_goals))


def test_replay_reproduces_the_live_decisions(backtest_run):
    config, run = backtest_run
    params = replace_params(params_from_config(config), max_picks=1000)
    replayed = {b.record.match_index for b in select_bets(run.candidates, params)}
    live = {m.index for m in run.matches if m.decision == Decision.BET}
    assert replayed == live


def replace_params(params: Params, **changes) -> Params:
    from dataclasses import replace

    return replace(params, **changes)


def test_value_matches_the_candidate_formula():
    record = _record(p_model=0.70, p_market=0.66, odds=1.55)
    odds, p_final, ev = value(record, 0.4)
    assert odds == 1.55 and p_final == pytest.approx(0.676) and ev == pytest.approx(0.676 * 1.55 - 1)
    assert value(record, 0.4, "median")[0] == 1.50


def test_report_renders_in_azerbaijani(backtest_run):
    config, run = backtest_run
    report = build_report(run, config)
    text = format_backtest_report(report).render_plain()
    assert "BACKTEST" in text and "PİRAMİDA" in text and "100%" in text
    assert report["summary"]["bets"] == len(report["bets"])
    assert format_backtest_report(None).render_plain().count("\n") >= 1


# ------------------------------------------------------------------ pyramid


def _record(
    *, day: date = date(2026, 1, 1), outcome: str = "win", odds: float = 1.5, p_model: float = 0.7,
    p_market: float = 0.68, confidence: int = 70, index: int = 0, hour: int = 15,
) -> CandidateRecord:
    return CandidateRecord(
        day=day, match_index=index, league_id=39, kickoff_utc=datetime.combine(day, datetime.min.time()) + timedelta(hours=hour),
        market="1X2", selection="1", line=None, odds=odds, bookmaker="A", median_odds=odds - 0.05,
        p_model=p_model, p_market=p_market, push_model=0.0, market_draw=0.2, confidence=confidence,
        risk="low", blocked=False, family="side", outcome=outcome, closing_probability=0.66, closing_price=False,
    )


def _bets(outcomes: list[str], odds: float = 2.0) -> list[SimBet]:
    bets = []
    for i, outcome in enumerate(outcomes):
        record = _record(day=date(2026, 1, 1) + timedelta(days=i), outcome=outcome, odds=odds, index=i)
        bets.append(SimBet(record, 1, odds, 0.6, 0.1, profit(outcome, odds), None))
    return bets


def test_classic_pyramid_compounds_and_restarts_after_a_loss():
    config = BankrollConfig(starting=2, target=100, mode="classic")
    attempts = simulate_pyramid(_bets(["win", "win", "loss", "win"]), config)
    assert [a.status for a in attempts] == ["lost", "running"]
    assert attempts[0].peak == 8.0 and attempts[0].wins == 2
    assert attempts[0].breaking_stage.balance_before == 8.0
    assert attempts[1].stages[-1].balance_after == 4.0


def test_pyramid_reaches_the_target_and_locks_milestones():
    config = BankrollConfig(starting=2, target=30, mode="milestone_lock", milestones=[10], lock_fraction=0.5)
    attempts = simulate_pyramid(_bets(["win"] * 6), config)
    first = attempts[0]
    assert first.locked == 8.0 and first.locks == 1  # 2 → 4 → 8 → 16 reaches 10: half of 16 locked, once
    assert first.status == "target" and first.stages[-1].balance_after >= 30


def test_chain_strategy_plays_later_matches_of_the_same_day():
    day = date(2026, 1, 1)
    early = _record(day=day, index=1, hour=13)
    clash = _record(day=day, index=2, hour=14)  # starts before the first one is over
    late = _record(day=day, index=3, hour=18)
    bets = [SimBet(r, rank, 1.5, 0.7, 0.05, 0.5, None) for rank, r in enumerate((early, clash, late), start=1)]
    attempts = simulate_pyramid(bets, BankrollConfig(starting=2, target=100), "chain")
    assert [s.bet.record.match_index for s in attempts[0].stages] == [1, 3]
    assert len(simulate_pyramid(bets, BankrollConfig(starting=2, target=100), "daily")[0].stages) == 1


# ------------------------------------------------------------------ calibration


def _row(first_roi: float, second_roi: float, clv: float, bets: int = 40, **params) -> SweepRow:
    summary = {"bets": bets, "roi": (first_roi + second_roi) / 2, "avg_clv": clv}
    return SweepRow(
        Params(params.get("w", 0.1), params.get("ev", 0.0), params.get("conf", 60)),
        summary,
        {"bets": bets, "roi": first_roi},
        {"bets": bets, "roi": second_roi},
    )


def test_choose_requires_both_halves_profitable_and_positive_clv():
    lucky = _row(0.30, -0.05, 0.02)
    no_clv = _row(0.10, 0.10, -0.01)
    small = _row(0.20, 0.20, 0.02, bets=10)
    good = _row(0.05, 0.08, 0.01, conf=65)
    assert choose([lucky, no_clv, small]) is None
    assert choose([lucky, no_clv, small, good]) is good


def test_apply_params_rewrites_values_and_keeps_comments(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        "selection:\n  min_confidence: 75\n  min_ev: 0.03                # EV\nmodel:\n  market_blend_weight: 0.4   # pay\n",
        encoding="utf-8",
    )
    apply_params(path, Params(0.1, 0.0, 55))
    text = path.read_text(encoding="utf-8")
    assert "min_confidence: 55" in text and "min_ev: 0.0" in text and "market_blend_weight: 0.1" in text
    assert "# EV" in text and "# pay" in text
