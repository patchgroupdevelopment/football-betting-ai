"""football-data.co.uk CSV files: results, match statistics (incl. xG) and odds — free, no key.

Two file layouts:
- main leagues, one file per season: /mmz4281/2627/E0.csv
  (HomeTeam, AwayTeam, FTHG, FTAG, HTHG, HTAG, Referee, HxG, AxG, HS, AS, HST, AST, HF, AF, HC, AC, HY, AY, HR, AR …)
- extra leagues, all seasons in one file: /new/ARG.csv (Home, Away, HG, AG, Season … — results only)
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


def parse_csv(text: str, division: str, season: int | None = None) -> list[CsvMatch]:
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

    def division(self, code: str, today: date) -> list[CsvMatch]:
        """Matches of a division for the current season and ``seasons_back`` earlier ones."""
        seasons_back = self._config.seasons_back
        if code.startswith(EXTRA_PREFIX):
            matches = parse_csv(self._text(f"/new/{code[len(EXTRA_PREFIX):]}.csv"), code)
            cutoff = datetime.combine(today - timedelta(days=366 * (seasons_back + 1)), datetime.min.time())
            return [m for m in matches if m.kickoff_utc >= cutoff]

        result: list[CsvMatch] = []
        current = season_start_year(today)
        for start in range(current, current - seasons_back - 1, -1):
            try:
                text = self._text(f"/mmz4281/{season_code(start)}/{code}.csv")
            except ProviderResponseError as exc:
                if "404" in exc.detail:  # the new season's file appears only once it starts
                    logger.info("football-data.co.uk: %s %s faylı yoxdur", code, season_code(start))
                    continue
                raise
            result.extend(parse_csv(text, code, season=start))
        return result
