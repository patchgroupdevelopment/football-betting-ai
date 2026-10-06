"""Message formatting and the Azerbaijani language rule."""

from __future__ import annotations

import string
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from backend.analyzers.data_quality import DataQuality
from backend.config import BankrollConfig
from backend.i18n.az import MESSAGES, t
from backend.presenters.formatters import format_daily_overview, format_pyramid, format_status, format_welcome
from backend.presenters.messages import MessageBuilder, bold, split_message
from backend.services.overview import DailyOverview, LeagueBlock, MatchLine
from backend.services.pyramid import PyramidService
from backend.services.system_status import QuotaInfo, SystemStatus
from backend.utils.formatting import (
    confidence_band,
    format_date,
    format_date_with_weekday,
    format_money,
    format_percent,
    format_signed_percent,
    format_small_probability,
)

TZ = ZoneInfo("Asia/Baku")
# English UI terms the prompt explicitly forbids in user-facing text.
FORBIDDEN_ENGLISH = ("Best Pick", "Confidence", "Model Probability", "Implied Probability", "No Bet",
                     "Low Risk", "Medium Risk", "High Risk", "Warning", "Error", "Today", "Matches")


def _assert_azerbaijani(text: str) -> None:
    for term in FORBIDDEN_ENGLISH:
        assert term not in text, f"English term in user-facing text: {term!r}"


def _line(home: str, away: str, score: int, *, status: str = "NS", has_odds: bool = True) -> MatchLine:
    components = {"odds": has_odds}
    level = "full" if score >= 80 else "partial" if score >= 60 else "low"
    return MatchLine(
        match_id=1, api_id=1, kickoff_local=datetime(2026, 10, 5, 19, 30, tzinfo=TZ), home=home, away=away,
        status=status, home_goals=None, away_goals=None,
        quality=DataQuality(score=score, level=level, components=components),
    )


def test_date_and_money_formats():
    assert format_date(date(2026, 10, 5)) == "5 oktyabr 2026"
    assert format_date_with_weekday(date(2026, 10, 5)) == "5 oktyabr 2026, bazar ertəsi"
    assert format_money(10_000) == "10,000.00 AZN"
    assert format_money(2) == "2.00 AZN"
    assert format_percent(0.7576) == "75.76%"
    assert format_signed_percent(0.0624) == "+6.24%"
    assert format_small_probability(0.00039) == "~0.04%"
    assert format_small_probability(0.00001) == "<0.01%"


@pytest.mark.parametrize(("score", "label"), [(95, "ÇOX GÜCLÜ"), (86, "GÜCLÜ"), (72, "MƏQBUL"), (65, "ORTA"),
                                              (55, "ZƏİF"), (30, "QAÇIN")])
def test_confidence_bands(score, label):
    assert confidence_band(score) == label


def test_daily_overview_message():
    overview = DailyOverview(
        target_date=date(2026, 10, 5),
        loaded=True,
        blocks=(
            LeagueBlock("İngiltərə — Premyer Liqa", (_line("Brighton & Hove", "Arsenal", 95),)),
            LeagueBlock("İspaniya — La Liqa", (_line("Real Madrid", "Barcelona", 55, has_odds=False),)),
        ),
        other_leagues_count=412,
        warnings=("Əmsallar (A – B): ⚠️ Məlumat mənbəyindən cavab alınmadı.",),
    )
    msg = format_daily_overview(overview)
    plain, html = msg.render_plain(), msg.render_html()

    assert "⚽ GÜNÜN OYUNLARI" in plain
    assert "📅 5 oktyabr 2026, bazar ertəsi" in plain
    assert "Prioritet liqalarda 2 oyun tapıldı." in plain
    assert "Analizə hazır: 1 · Natamam məlumat: 1" in plain
    assert "Digər liqalarda daha 412 oyun var" in plain
    assert "🕐 19:30  Brighton & Hove – Arsenal" in plain
    assert "📊 Məlumat: 95% 🟢 Tam · 💰 Əmsal: var" in plain
    assert "📊 Məlumat: 55% 🔴 Natamam · 💰 Əmsal: yoxdur" in plain
    assert "⚠️ XƏBƏRDARLIQLAR" in plain
    assert "heç bir nəticəyə zəmanət vermir" in plain
    assert "Brighton &amp; Hove" in html and "<b>⚽ GÜNÜN OYUNLARI</b>" in html
    _assert_azerbaijani(plain)


def test_not_loaded_overview():
    plain = format_daily_overview(DailyOverview(target_date=date(2026, 10, 5), loaded=False)).render_plain()
    assert "hələ yüklənməyib" in plain and "/yenile" in plain


def test_pyramid_message(db):
    service = PyramidService(db, BankrollConfig())
    state = service.get_state()
    plain = format_pyramid(state, service.projection(state), paper_mode=True).render_plain()

    assert "💰 PİRAMİDA İRƏLİLƏYİŞİ" in plain
    assert "Simulyasiya rejimi" in plain
    assert "Cari balans: 2.00 AZN" in plain
    assert "Hədəf: 10,000.00 AZN" in plain
    assert "Hədəfə qalan: 9,998.00 AZN" in plain
    assert "Orta istifadə olunan əmsal: hələ mərc yoxdur" in plain
    assert "Təxmini qalan mərhələ: 22 (əmsal 1.50 ilə)" in plain
    assert "1. 2.00 AZN → 3.00 AZN" in plain
    assert "…" in plain and "22. " in plain
    assert "~0.04%" in plain
    _assert_azerbaijani(plain)


def test_status_message_without_api_key():
    status = SystemStatus(
        paper_mode=True, last_run_date=None, last_run_status=None, last_run_finished_local=None, quota=None,
        quota_error="⚠️ FOOTBALL_API_KEY təyin edilməyib. Açarı .env faylına əlavə edin.",
        matches=0, teams=0, odds=0, telegram_enabled=False, llm_enabled=False, next_run_local=None,
    )
    plain = format_status(status).render_plain()
    assert "Son yükləmə: hələ olmayıb" in plain
    assert "API sorğuları: məlum deyil (FOOTBALL_API_KEY təyin edilməyib." in plain
    _assert_azerbaijani(plain)


def test_status_message_with_quota():
    status = SystemStatus(
        paper_mode=True, last_run_date=date(2026, 10, 5), last_run_status="success",
        last_run_finished_local=datetime(2026, 10, 5, 8, 3, tzinfo=TZ),
        quota=QuotaInfo(plan="Pro", used=87, limit=7500, remaining=7413), quota_error=None,
        matches=1245, teams=312, odds=8940, telegram_enabled=True, llm_enabled=False,
        next_run_local=datetime(2026, 10, 6, 8, 0, tzinfo=TZ),
    )
    plain = format_status(status).render_plain()
    assert "Son yükləmə: 5 oktyabr 2026, 08:03 — ✅ Uğurlu" in plain
    assert "87 istifadə edilib, 7,413 qalıb (limit 7,500)" in plain
    assert "Bazada: 1,245 oyun · 312 komanda · 8,940 əmsal qeydi" in plain
    assert "Növbəti planlı yükləmə: 6 oktyabr 2026, 08:00" in plain


def test_welcome_lists_commands():
    plain = format_welcome("08:00").render_plain()
    for command in ("/bugun", "/piramida", "/status", "/yenile", "/komek"):
        assert command in plain
    assert "Avtomatik hesabat: hər gün saat 08:00." in plain


def test_split_message_respects_limit_and_keeps_content():
    builder = MessageBuilder()
    for block in range(60):
        builder.line(bold(f"Blok {block}"))
        for row in range(5):
            builder.line(f"Sətir {block}-{row} " + "x" * 40)
        builder.blank()
    text = builder.render_html()
    chunks = split_message(text, limit=1000)
    assert len(chunks) > 1 and all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_every_message_template_formats():
    formatter = string.Formatter()
    for key, template in MESSAGES.items():
        fields = {name for _, name, _, _ in formatter.parse(template) if name}
        rendered = t(key, **{name: "1" for name in fields})
        assert rendered


def test_unknown_message_key_fails_loudly():
    with pytest.raises(KeyError):
        t("does.not.exist")


def test_messages_contain_no_forbidden_english():
    for template in MESSAGES.values():
        _assert_azerbaijani(template)
