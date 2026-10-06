"""football-data.co.uk CSV files: results, match statistics (incl. xG) and odds — free, no key.

Two file layouts:
- main leagues, one file per season: /mmz4281/2627/E0.csv
  (HomeTeam, AwayTeam, FTHG, FTAG, HTHG, HTAG, Referee, HxG, AxG, HS, AS, HST, AST, HF, AF, HC, AC, HY, AY, HR, AR …)
- extra leagues, all seasons in one file: /new/ARG.csv (Home, Away, HG, AG, Season, PSCH … — results
  and closing 1X2 odds only)
Odds: per bookmaker, as collected before the round (``B365H``) and at kick-off (``B365CH``).
Kick-off times are UK local time.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from backend.config import FootballDataCoUkConfig
from backend.predictors.market_odds import OddsQuote
from backend.services.cache import ResponseCache
from backend.services.errors import ProviderResponseError
from backend.services.http_client import HttpJsonClient, RetryPolicy

logger = logging.getLogger(__name__)

UK = ZoneInfo("Europe/London")
SOURCE = "football_data_couk"
EXTRA_PREFIX = "new:"
DEFAULT_KICKOFF = "15:00"

# MatchStats column -> (home column, away column)
STAT_COLUMNS: dict[str, tuple[str, str]] = {
    "shots_total": ("HS", "AS"),
    "shots_on_target": ("HST", "AST"),
    "fouls": ("HF", "AF"),
    "corners": ("HC", "AC"),
    "yellow_cards": ("HY", "AY"),
    "red_cards": ("HR", "AR"),
    "xg": ("HxG", "AxG"),
}
FLOAT_COLUMNS = frozenset({"xg"})

# Column prefixes of individual bookmakers. Max/Avg (aggregates over many bookmakers) and
# BFE (Betfair exchange) are left out: neither is a price one bookmaker actually offers.
BOOKMAKERS_1X2 = {
    "B365": "Bet365", "BFD": "Betfred", "BMGM": "BetMGM", "BV": "BetVictor", "BW": "bwin",
    "CL": "Coral", "LB": "Ladbrokes", "PS": "Pinnacle", "PP": "Paddy Power", "SKB": "Sky Bet",
    "WH": "William Hill", "IW": "Interwetten", "VC": "VC Bet", "1XB": "1xBet",
}
BOOKMAKERS_LINES = {"B365": "Bet365", "P": "Pinnacle"}  # over/under 2.5 and Asian handicap


@dataclass(frozen=True)
class CsvMatch:
    division: str
    season: int
    kickoff_utc: datetime
    home: str
    away: str
    home_goals: int
    away_goals: int
    ht_home_goals: int | None = None
    ht_away_goals: int | None = None
    referee: str | None = None
    home_stats: dict[str, float | int | None] = field(default_factory=dict)
    away_stats: dict[str, float | int | None] = field(default_factory=dict)
    odds: tuple[OddsQuote, ...] = ()  # before the round (main leagues only)
    closing_odds: tuple[OddsQuote, ...] = ()

    @property
    def external_id(self) -> str:
        return f"{self.division}|{self.kickoff_utc:%Y%m%d}|{self.home}|{self.away}"[:120]

    @property
    def has_xg(self) -> bool:
        return self.home_stats.get("xg") is not None and self.away_stats.get("xg") is not None


def season_start_year(today: date) -> int:
    """European seasons start in summer: October 2026 belongs to 2026/27."""
    return today.year if today.month >= 7 else today.year - 1


def season_code(start_year: int) -> str:
    return f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"


def _int(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


def _float(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_kickoff(day: str, clock: str | None) -> datetime | None:
    day = (day or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            parsed = datetime.strptime(day, fmt)
            break
        except ValueError:
            continue
    else:
        return None
    hour, minute = 15, 0
    if clock and ":" in clock:
        try:
            hour, minute = (int(part) for part in clock.strip().split(":")[:2])
        except ValueError:
            pass
    local = parsed.replace(hour=hour, minute=minute, tzinfo=UK)
    return local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def _season_from_text(text: str | None, kickoff: datetime) -> int:
    """'2026' or '2025/2026' -> start year; falls back to the kick-off year."""
    digits = (text or "").strip()[:4]
    return int(digits) if digits.isdigit() else kickoff.year


def _quote(row: dict[str, str], column: str, bookmaker: str, market: str, selection: str, line: float | None) -> OddsQuote | None:
    price = _float(row.get(column))
    return OddsQuote(bookmaker, None, market, selection, line, price) if price is not None and price > 1.0 else None


def parse_odds(row: dict[str, str], closing: bool) -> tuple[OddsQuote, ...]:
    """1X2, over/under 2.5 and Asian handicap quotes of one CSV row."""
    c = "C" if closing else ""
    quotes: list[OddsQuote | None] = []
    for code, name in BOOKMAKERS_1X2.items():
        for suffix, selection in (("H", "1"), ("D", "X"), ("A", "2")):
            quotes.append(_quote(row, f"{code}{c}{suffix}", name, "1X2", selection, None))
    for code, name in BOOKMAKERS_LINES.items():
        quotes.append(_quote(row, f"{code}{c}>2.5", name, "OU", "OVER", 2.5))
        quotes.append(_quote(row, f"{code}{c}<2.5", name, "OU", "UNDER", 2.5))
    line = _float(row.get("AHCh" if closing else "AHh"))  # the home team's handicap
    if line is not None:
        for code, name in BOOKMAKERS_LINES.items():
            quotes.append(_quote(row, f"{code}{c}AHH", name, "AH", "1", line))
            quotes.append(_quote(row, f"{code}{c}AHA", name, "AH", "2", -line if line else 0.0))
    return tuple(q for q in quotes if q is not None)


def parse_csv(text: str, division: str, season: int | None = None, *, with_odds: bool = False) -> list[CsvMatch]:
    matches: list[CsvMatch] = []
    for row in csv.DictReader(io.StringIO(text)):
        home = (row.get("HomeTeam") or row.get("Home") or "").strip()
        away = (row.get("AwayTeam") or row.get("Away") or "").strip()
        home_goals = _int(row.get("FTHG") if "FTHG" in row else row.get("HG"))
        away_goals = _int(row.get("FTAG") if "FTAG" in row else row.get("AG"))
        kickoff = parse_kickoff(row.get("Date") or "", row.get("Time"))
        if not home or not away or home_goals is None or away_goals is None or kickoff is None:
            continue
        home_stats: dict[str, float | int | None] = {}
        away_stats: dict[str, float | int | None] = {}
        for column, (home_col, away_col) in STAT_COLUMNS.items():
            if home_col in row:
                convert = _float if column in FLOAT_COLUMNS else _int
                home_stats[column] = convert(row.get(home_col))
                away_stats[column] = convert(row.get(away_col))
        matches.append(
            CsvMatch(
                division=division,
                season=season if season is not None else _season_from_text(row.get("Season"), kickoff),
                kickoff_utc=kickoff,
                home=home,
                away=away,
                home_goals=home_goals,
                away_goals=away_goals,
                ht_home_goals=_int(row.get("HTHG")),
                ht_away_goals=_int(row.get("HTAG")),
                referee=(row.get("Referee") or "").strip() or None,
                home_stats=home_stats,
                away_stats=away_stats,
                odds=parse_odds(row, closing=False) if with_odds else (),
                closing_odds=parse_odds(row, closing=True) if with_odds else (),
            )
        )
    return matches


class FootballDataCoUk:
    def __init__(
        self,
        config: FootballDataCoUkConfig,
        cache: ResponseCache | None = None,
        cache_hours: float = 12,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._config = config
        self._cache = cache
        self._ttl = int(cache_hours * 3600)
        self._http = HttpJsonClient(
            config.base_url,
            timeout=config.timeout_seconds,
            retry=RetryPolicy(max_retries=config.max_retries),
            auth_key_name="football-data.co.uk",
            transport=transport,
            sleep=sleep,
        )

    def close(self) -> None:
        self._http.close()

    def _text(self, path: str) -> str:
        params = {"path": path}
        if self._cache is not None and self._ttl > 0:
            cached = self._cache.get(SOURCE, params)
            if cached is not None:
                return cached["text"]
        text = self._http.get_text(path)
        if not text.strip():
            raise ProviderResponseError(f"{path}: empty response")  # never cache an empty file
        if self._cache is not None and self._ttl > 0:
            self._cache.set(SOURCE, params, {"text": text}, self._ttl)
        return text

    def season_text(self, code: str, start_year: int) -> str:
        return self._text(f"/mmz4281/{season_code(start_year)}/{code}.csv")

    def extra_text(self, code: str) -> str:
        return self._text(f"/new/{code[len(EXTRA_PREFIX):]}.csv")

    def division(
        self, code: str, today: date, *, seasons_back: int | None = None, with_odds: bool = False
    ) -> list[CsvMatch]:
        """Matches of a division for the current season and ``seasons_back`` earlier ones."""
        if seasons_back is None:
            seasons_back = self._config.seasons_back
        if code.startswith(EXTRA_PREFIX):
            matches = parse_csv(self.extra_text(code), code, with_odds=with_odds)
            cutoff = datetime.combine(today - timedelta(days=366 * (seasons_back + 1)), datetime.min.time())
            return [m for m in matches if m.kickoff_utc >= cutoff]

        result: list[CsvMatch] = []
        current = season_start_year(today)
        for start in range(current, current - seasons_back - 1, -1):
            try:
                text = self.season_text(code, start)
            except ProviderResponseError as exc:
                if "404" in exc.detail:  # the new season's file appears only once it starts
                    logger.info("football-data.co.uk: %s %s faylı yoxdur", code, season_code(start))
                    continue
                raise
            result.extend(parse_csv(text, code, season=start, with_odds=with_odds))
        return result
