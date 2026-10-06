"""Normalisation of bookmaker bet names/values into internal market codes.

Bet names are compared after stripping punctuation and case, so "Corners Over
Under" and "Corners Over/Under" map to the same market. Markets we do not model
return ``None`` and are not stored.
"""

from __future__ import annotations

import re

Normalized = tuple[str, str, float | None]  # (market, selection, line)

_MARKETS: dict[str, str] = {
    "matchwinner": "1X2",
    "doublechance": "DC",
    "homeaway": "DNB",
    "goalsoverunder": "OU",
    "goalsoverunderfirsthalf": "OU_1H",
    "bothteamsscore": "BTTS",
    "asianhandicap": "AH",
    "totalhome": "TEAM_TOTAL_HOME",
    "totalaway": "TEAM_TOTAL_AWAY",
    "cornersoverunder": "CORNERS_OU",
    "cardsoverunder": "CARDS_OU",
}
_OVER_UNDER_MARKETS = frozenset({"OU", "OU_1H", "TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY", "CORNERS_OU", "CARDS_OU"})

_SIDES = {"home": "1", "draw": "X", "away": "2"}
_DOUBLE_CHANCE = {"home/draw": "1X", "home/away": "12", "draw/away": "X2"}
_YES_NO = {"yes": "YES", "no": "NO"}
_OVER_UNDER = re.compile(r"^(over|under)\s*([+-]?\d+(?:\.\d+)?)$", re.IGNORECASE)
_HANDICAP = re.compile(r"^(home|away)\s*([+-]?\d+(?:\.\d+)?)$", re.IGNORECASE)


def _bet_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def normalize_bet(bet_name: str, value: str) -> Normalized | None:
    market = _MARKETS.get(_bet_key(bet_name or ""))
    if market is None:
        return None
    text = (value or "").strip()
    lowered = text.lower()

    if market == "1X2":
        side = _SIDES.get(lowered)
        return (market, side, None) if side else None
    if market == "DNB":
        side = _SIDES.get(lowered)
        return (market, side, None) if side in ("1", "2") else None
    if market == "DC":
        selection = _DOUBLE_CHANCE.get(lowered.replace(" ", ""))
        return (market, selection, None) if selection else None
    if market == "BTTS":
        selection = _YES_NO.get(lowered)
        return (market, selection, None) if selection else None
    if market == "AH":
        match = _HANDICAP.match(text)
        return (market, _SIDES[match.group(1).lower()], float(match.group(2))) if match else None
    if market in _OVER_UNDER_MARKETS:
        match = _OVER_UNDER.match(text)
        return (market, match.group(1).upper(), float(match.group(2))) if match else None
    return None
