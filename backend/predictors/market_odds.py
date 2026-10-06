"""Bookmaker consensus: margin-free market probabilities and best available prices.

For every bookmaker that quotes a complete set of outcomes (e.g. 1/X/2, or
OVER/UNDER at one line) the implied probabilities are normalised to sum to 1
(proportional margin removal). The consensus is the average over bookmakers.
Double chance is derived from the 1X2 consensus for consistency.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from statistics import fmean

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import OddsSnapshot

Key = tuple[str, str, float | None]  # (market, selection, line)

_PAIRS = {
    "OU": ("OVER", "UNDER"),
    "OU_1H": ("OVER", "UNDER"),
    "TEAM_TOTAL_HOME": ("OVER", "UNDER"),
    "TEAM_TOTAL_AWAY": ("OVER", "UNDER"),
    "CORNERS_OU": ("OVER", "UNDER"),
    "CARDS_OU": ("OVER", "UNDER"),
    "BTTS": ("YES", "NO"),
    "DNB": ("1", "2"),
}


@dataclass(frozen=True)
class OddsQuote:
    bookmaker: str
    bookmaker_id: int | None
    market: str
    selection: str
    line: float | None
    price: float


@dataclass(frozen=True)
class MarketPrice:
    market: str
    selection: str
    line: float | None
    fair_probability: float | None  # None when no bookmaker quotes the full outcome set
    best_odds: float
    best_bookmaker: str
    bookmakers: int


def latest_quotes(session: Session, match_id: int) -> list[OddsQuote]:
    rows = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.match_id == match_id)
        .order_by(OddsSnapshot.captured_at, OddsSnapshot.id)
    )
    latest: dict[tuple[object, ...], OddsSnapshot] = {}
    for row in rows:
        latest[(row.bookmaker_api_id, row.bookmaker, row.market, row.selection, row.line)] = row
    return [
        OddsQuote(r.bookmaker, r.bookmaker_api_id, r.market, r.selection, r.line, r.price) for r in latest.values()
    ]


def _group_key(quote: OddsQuote) -> tuple[str, float | None] | None:
    if quote.market == "1X2":
        return ("1X2", None)
    if quote.market == "AH":
        # Home -1.5 pairs with away +1.5: group by the home team's line.
        home_line = quote.line if quote.selection == "1" else (-quote.line if quote.line is not None else None)
        return ("AH", home_line)
    if quote.market in _PAIRS:
        return (quote.market, quote.line)
    return None


def _members(group: tuple[str, float | None]) -> list[Key]:
    market, line = group
    if market == "1X2":
        return [("1X2", "1", None), ("1X2", "X", None), ("1X2", "2", None)]
    if market == "AH":
        return [("AH", "1", line), ("AH", "2", -line if line is not None else None)]
    first, second = _PAIRS[market]
    return [(market, first, line), (market, second, line)]


def build_market(quotes: Iterable[OddsQuote]) -> dict[Key, MarketPrice]:
    quotes = [q for q in quotes if q.price > 1.0]
    best: dict[Key, OddsQuote] = {}
    counts: dict[Key, int] = defaultdict(int)
    by_book_group: dict[tuple[str, tuple[str, float | None]], dict[Key, float]] = defaultdict(dict)

    for quote in quotes:
        key: Key = (quote.market, quote.selection, quote.line)
        counts[key] += 1
        if key not in best or quote.price > best[key].price:
            best[key] = quote
        group = _group_key(quote)
        if group is not None:
            by_book_group[(quote.bookmaker, group)][key] = quote.price

    fair_samples: dict[Key, list[float]] = defaultdict(list)
    for (_bookmaker, group), prices in by_book_group.items():
        members = _members(group)
        if not all(member in prices for member in members):
            continue
        inverse = {member: 1.0 / prices[member] for member in members}
        overround = sum(inverse.values())
        for member, value in inverse.items():
            fair_samples[member].append(value / overround)

    fair = {key: fmean(values) for key, values in fair_samples.items()}
    one, draw, two = fair.get(("1X2", "1", None)), fair.get(("1X2", "X", None)), fair.get(("1X2", "2", None))
    if None not in (one, draw, two):
        fair[("DC", "1X", None)] = one + draw
        fair[("DC", "X2", None)] = draw + two
        fair[("DC", "12", None)] = one + two

    return {
        key: MarketPrice(
            market=key[0],
            selection=key[1],
            line=key[2],
            fair_probability=fair.get(key),
            best_odds=quote.price,
            best_bookmaker=quote.bookmaker,
            bookmakers=counts[key],
        )
        for key, quote in best.items()
    }


def market_draw_probability(market: dict[Key, MarketPrice]) -> float | None:
    price = market.get(("1X2", "X", None))
    return price.fair_probability if price else None
