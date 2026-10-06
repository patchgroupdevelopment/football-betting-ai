"""Display formatting in the user's conventions (Azerbaijani dates, AZN amounts)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from backend.i18n.az import CONFIDENCE_BANDS, COUNTRIES, MONTHS, WEEKDAYS

_CENT = Decimal("0.01")


def format_date(day: date) -> str:
    """5 oktyabr 2026"""
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def format_date_with_weekday(day: date) -> str:
    """5 oktyabr 2026, bazar ertəsi"""
    return f"{format_date(day)}, {WEEKDAYS[day.weekday()]}"


def format_time(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def format_datetime(moment: datetime) -> str:
    """5 oktyabr 2026, 08:00"""
    return f"{format_date(moment.date())}, {format_time(moment)}"


def format_money(amount: float | Decimal) -> str:
    """10,000.00 AZN"""
    value = Decimal(str(amount)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return f"{value:,.2f} AZN"


def format_number(value: int) -> str:
    return f"{value:,}"


def format_odds(odds: float) -> str:
    return f"{odds:.2f}"


def format_percent(fraction: float, decimals: int = 2) -> str:
    """0.7576 -> 75.76%"""
    return f"{fraction * 100:.{decimals}f}%"


def format_signed_percent(fraction: float, decimals: int = 2) -> str:
    """0.0624 -> +6.24%"""
    return f"{fraction * 100:+.{decimals}f}%"


def format_small_probability(probability: float) -> str:
    """Readable form for probabilities that can be very small (pyramid odds)."""
    if probability <= 0:
        return "0%"
    if probability < 0.0001:
        return "<0.01%"
    if probability < 0.01:
        return f"~{probability * 100:.2f}%"
    return f"~{probability * 100:.1f}%"


def country_name(country: str | None) -> str | None:
    if not country:
        return None
    return COUNTRIES.get(country, country)


def confidence_band(score: float) -> str:
    for threshold, label in CONFIDENCE_BANDS:
        if score >= threshold:
            return label
    return CONFIDENCE_BANDS[-1][1]


def az_upper(text: str) -> str:
    """Upper case with Azerbaijani dotted/dotless i: 'Misli' -> 'MİSLİ', 'ı' -> 'I'."""
    return text.replace("i", "İ").replace("ı", "I").upper()
