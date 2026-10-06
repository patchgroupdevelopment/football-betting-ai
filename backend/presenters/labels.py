"""Azerbaijani labels for bet selections, decisions and risk."""

from __future__ import annotations

from backend.i18n import t
from backend.i18n.az import DECISION_ICONS, DECISIONS, DIRECTIONS, NO_BET_REASONS, RISK_ICONS, RISK_LEVELS


def line_text(market: str, line: float | None) -> str:
    if line is None:
        return ""
    return f"{line:+g}" if market == "AH" else f"{line:g}"


def selection_label(market: str, selection: str, line: float | None, home: str, away: str) -> str:
    """1.5 ÜST · İkili şans 1X — Qarabağ uduzmaz · Arsenal — heç-heçəyə mərcsiz …"""
    params = {"home": home, "away": away, "line": line_text(market, line), "dir": DIRECTIONS.get(selection, selection)}
    for key in (f"sel.{market}.{selection}", f"sel.{market}"):
        try:
            return t(key, **params)
        except KeyError:
            continue
    return f"{market} {selection} {params['line']}".strip()


def decision_label(decision: str) -> str:
    return f"{DECISION_ICONS.get(decision, '')} {DECISIONS.get(decision, decision)}".strip()


def risk_label(risk: str | None) -> tuple[str, str]:
    """(icon, text)"""
    if not risk:
        return "", ""
    return RISK_ICONS.get(risk, ""), RISK_LEVELS.get(risk, risk)


def reasons_text(keys: list[str]) -> str:
    return "; ".join(NO_BET_REASONS.get(key, key) for key in keys)
