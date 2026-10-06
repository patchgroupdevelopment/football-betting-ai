"""Analysis service end to end (ingestion → analysis → stored predictions → messages)."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from backend.models import Bet, Match, Prediction, TeamRating
from backend.models.constants import Decision
from backend.presenters.formatters import format_daily_analysis, format_daily_overview, format_match_detail
from backend.services.analysis import AnalysisService
from backend.services.ingestion import IngestionService
from backend.services.overview import build_daily_overview
from backend.services.picks import DailyAnalysis, PickView, build_daily_analysis, get_match_analysis
from backend.services.pyramid import PyramidService
from tests.builders import goal_models, value_scenario
from tests.conftest import make_client
from tests.test_ingestion import DAY, Scenario

TZ = ZoneInfo("Asia/Baku")
MORNING_UTC = datetime(2026, 10, 5, 4, 0)  # 08:00 in Baku


def _run(db, app_config, fake_api):
    Scenario(fake_api)
    IngestionService(db, make_client(fake_api, db=db), app_config, "Asia/Baku").run_daily(DAY)
    return AnalysisService(db, app_config, TZ, clock=lambda: MORNING_UTC).run_daily(DAY)


def _match_id(db, api_id: int) -> int:
    with db.session() as s:
        return s.scalar(select(Match.id).where(Match.api_id == api_id))


def test_analysis_stores_a_decision_for_every_upcoming_match(db, app_config, fake_api):
    report = _run(db, app_config, fake_api)
    assert report.analyzed == 3 and report.matches_used >= 36
    assert report.bets + report.watch + report.no_bet == 3

    with db.session() as s:
        predictions = {p.match_id: p for p in s.scalars(select(Prediction))}
        assert len(predictions) == 3
        no_odds = predictions[_match_id(db, 1002)]
        assert (no_odds.market, no_odds.decision) == ("NONE", Decision.NO_BET)
        assert no_odds.reasons["match_flags"] == ["no_odds"]
        assert s.scalar(select(func.count(TeamRating.id))) > 0


def test_injured_top_scorer_makes_the_match_no_bet(db, app_config, fake_api):
    _run(db, app_config, fake_api)
    view = get_match_analysis(db, _match_id(db, 1001), TZ)
    assert view is not None and view.has_candidate
    assert view.decision == Decision.NO_BET
    assert "critical_absence" in view.reasons["not_bet"]
    analysis = "\n".join(view.reasons["analysis"])
    assert "Zədəlilər: Team 50 — Player 1100 (bombardir, əsas hücumçu, 6 qol)" in analysis
    assert "əsas hücumçunun olmaması hücum gücünü əhəmiyyətli dərəcədə aşağı salır" in analysis
    assert any(a["key"] == "injuries" and a["team"] == "home" for a in view.reasons["adjustments"])


def test_daily_analysis_without_picks(db, app_config, fake_api):
    _run(db, app_config, fake_api)
    analysis = build_daily_analysis(db, DAY, TZ)
    assert analysis.analyzed and not analysis.picks and analysis.total == 3
    assert analysis.no_bet_reasons.get("no_odds") == 2

    state = PyramidService(db, app_config.bankroll).get_state()
    plain = format_daily_analysis(analysis, state, paper_mode=True).render_plain()
    assert "⚽ GÜNÜN FUTBOL ANALİZİ" in plain and "⛔ MƏRC YOXDUR" in plain
    assert "Bu gün uyğun seçim tapılmadı" in plain and "əmsal yoxdur — 2 oyun" in plain
    assert "Bu gün yeni mərhələ açılmır." in plain and "heç bir nəticəyə zəmanət vermir" in plain


def test_overview_shows_decisions_and_links(db, app_config, fake_api):
    _run(db, app_config, fake_api)
    plain = format_daily_overview(build_daily_overview(db, DAY, TZ)).render_plain()
    assert f"⛔ MƏRC YOXDUR · /oyun_{_match_id(db, 1002)}" in plain
    assert "Statistik model hələ aktiv deyil" not in plain


def test_match_detail_message(db, app_config, fake_api):
    _run(db, app_config, fake_api)
    plain = format_match_detail(get_match_analysis(db, _match_id(db, 1001), TZ)).render_plain()
    assert "QƏRAR: ⛔ MƏRC YOXDUR" in plain and "📋 ANALİZ" in plain
    assert "✅ YEKUN RƏY" in plain and "zədə vəziyyəti kritikdir" in plain
    assert "BU MƏRC NİYƏ UDUZA BİLƏR?" in plain and "MARKETLƏR" in plain
    assert format_match_detail(None).render_plain() == "Bu ID ilə analiz edilmiş oyun tapılmadı."


def test_rerun_keeps_history_but_reads_latest(db, app_config, fake_api):
    _run(db, app_config, fake_api)
    AnalysisService(db, app_config, TZ, clock=lambda: MORNING_UTC).run_daily(DAY)
    with db.session() as s:
        assert s.scalar(select(func.count(Prediction.id))) == 6
    assert build_daily_analysis(db, DAY, TZ).total == 3


def test_started_matches_are_not_analysed(db, app_config, fake_api):
    Scenario(fake_api)
    IngestionService(db, make_client(fake_api, db=db), app_config, "Asia/Baku").run_daily(DAY)
    evening = datetime(2026, 10, 5, 18, 30)  # after the 15:30 and 18:00 kick-offs
    report = AnalysisService(db, app_config, TZ, clock=lambda: evening).run_daily(DAY)
    assert report.analyzed == 1


def test_paper_bet_is_recorded_once(db, app_config):
    evaluation = evaluate_value_match_in_db(db)
    service = AnalysisService(db, app_config, TZ, clock=lambda: MORNING_UTC)
    with db.session() as s:
        for _ in range(2):
            prediction = service._store_prediction(s, evaluation, DAY, rank=1)
            service._record_paper_bet(s, prediction)
    with db.session() as s:
        bet = s.scalar(select(Bet))
        assert s.scalar(select(func.count(Bet.id))) == 1
        assert bet.is_paper and bet.stake == 1.0 and bet.odds_taken == 1.50 and bet.status == "pending"


def evaluate_value_match_in_db(db):
    """The synthetic value scenario, attached to a real match row."""
    from dataclasses import replace

    from backend.config import AppConfig
    from backend.models import League, Team
    from backend.selection.engine import evaluate_match

    with db.session() as s:
        league = League(api_id=1, name="Premyer Liqa")
        home, away = Team(api_id=1, name="Qarabağ"), Team(api_id=2, name="Neftçi")
        s.add_all([league, home, away])
        s.flush()
        match = Match(api_id=1, league_id=league.id, season=2026, kickoff_utc=datetime(2026, 10, 5, 15, 30),
                      home_team_id=home.id, away_team_id=away.id, is_priority=True)
        s.add(match)
        s.flush()
        match_id = match.id
    evaluation = evaluate_match(value_scenario(), goal_models(), AppConfig())
    assert evaluation.decision == Decision.BET
    return replace(evaluation, context=replace(evaluation.context, match_id=match_id))


def _pick(rank: int, home: str, away: str, market: str = "OU", selection: str = "OVER", line: float | None = 1.5) -> PickView:
    return PickView(
        prediction_id=rank, match_id=10 + rank, rank=rank, decision="bet", market=market, selection=selection,
        line=line, odds=1.32, bookmaker="Bet365", p_model=0.82, p_market=0.7576, p_final=0.783, fair_odds=1.277,
        edge_pp=2.6, ev=0.0335, confidence=86, risk="low", home=home, away=away,
        league="İngiltərə — Premyer Liqa", kickoff_local=datetime(2026, 10, 5, 19, 30, tzinfo=TZ), status="NS",
        reasons={
            "analysis": ["Son 5 oyun forması: ..."],
            "why": ["Son 5 oyunda 1.5 ÜST nisbəti: A — 100%, B — 80%."],
            "against": ["Model düz olsa belə, bu mərc təxminən 22% ehtimalla uduzur — təqribən hər 5 mərcdən biri."],
            "top_factors": ["form", "xg"],
        },
    )


def test_daily_analysis_message_with_picks(db, app_config):
    analysis = DailyAnalysis(
        target_date=date(2026, 10, 5),
        analyzed=True,
        picks=(_pick(1, "Manchester City", "Arsenal"), _pick(2, "Real Madrid", "Barcelona", "DC", "1X", None)),
        total=34,
        counts={"bet": 2, "watch": 3, "no_bet": 29},
    )
    state = PyramidService(db, app_config.bankroll).get_state()
    plain = format_daily_analysis(analysis, state, paper_mode=True).render_plain()

    for expected in (
        "🔥 GÜNÜN ƏN YAXŞI SEÇİMİ",
        "⚽ Manchester City – Arsenal",
        "🏆 İngiltərə — Premyer Liqa · 🕐 19:30",
        "🎯 Mərc: 1.5 ÜST",
        "💰 Əmsal: 1.32 (Bet365)",
        "📊 Model ehtimalı: 78.3% (statistik model: 82.0%, bazar: 75.8%)",
        "📌 Əmsalın göstərdiyi ehtimal: 75.76%",
        "💎 Dəyər üstünlüyü: +3.35%",
        "⭐ Əminlik səviyyəsi: 86/100 (GÜCLÜ)",
        "🟢 Risk səviyyəsi: AŞAĞI RİSK",
        "✅ YEKUN RƏY",
        "Əsas üstünlük: forma, xG.",
        "👍 NİYƏ BU MƏRC?",
        "⚠️ BU MƏRC NİYƏ UDUZA BİLƏR?",
        "🎯 DİGƏR SEÇİMLƏR",
        "2. Real Madrid – Barcelona · İkili şans 1X — Real Madrid uduzmaz · 1.32",
        "Ətraflı analiz: /oyun_12",
        "📊 Analiz edilən oyunlar: 34 · ✅ Mərc edilə bilər: 2 · 👀 İzləmədə: 3 · ⛔ Mərc yoxdur: 29",
        "Seçilmiş əmsal: 1.32",
        "Nəzəri növbəti balans: 2.64 AZN",
        "Hədəf: 10,000.00 AZN",
        "📝 Simulyasiya rejimi",
    ):
        assert expected in plain, expected


def test_analysis_with_no_matches(db, app_config):
    AnalysisService(db, app_config, TZ, clock=lambda: MORNING_UTC).run_daily(DAY)
    analysis = build_daily_analysis(db, DAY, TZ)
    assert analysis.analyzed and analysis.total == 0
    state = PyramidService(db, app_config.bankroll).get_state()
    plain = format_daily_analysis(analysis, state, paper_mode=True).render_plain()
    assert "Analiz ediləcək oyun yoxdur" in plain and "analiz hələ aparılmayıb" not in plain


def test_analysis_not_run_message(db, app_config):
    state = PyramidService(db, app_config.bankroll).get_state()
    plain = format_daily_analysis(DailyAnalysis(target_date=DAY, analyzed=False), state, paper_mode=True).render_plain()
    assert "analiz hələ aparılmayıb" in plain
