"""Configuration.

Two sources:
- ``.env`` -> ``Settings``: secrets and environment switches (never committed).
- ``config.yaml`` -> ``AppConfig``: tunable model, selection and schedule parameters.

A few ``.env`` variables (``MIN_ODDS``, ``STARTING_BANKROLL`` …) override their
``config.yaml`` counterparts, so a deployment can change them without editing
the versioned file.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.i18n import t

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(Exception):
    """Invalid or missing configuration; ``user_message`` is shown to the user."""

    def __init__(self, user_message: str) -> None:
        super().__init__(user_message)
        self.user_message = user_message


# --------------------------------------------------------------------------- yaml


class BankrollConfig(BaseModel):
    starting: float = Field(2.0, gt=0)
    target: float = Field(10_000.0, gt=0)
    mode: Literal["classic", "milestone_lock"] = "classic"
    milestones: list[float] = Field(default_factory=lambda: [50.0, 250.0, 1000.0, 5000.0])
    lock_fraction: float = Field(0.5, ge=0, le=1)
    projection_odds: float = Field(1.50, gt=1)
    assumed_ev: float = Field(0.05, ge=0, lt=1)

    @model_validator(mode="after")
    def _check_target(self) -> BankrollConfig:
        if self.target <= self.starting:
            raise ValueError(t("config.target_below_start"))
        return self


ALL_MARKETS = ["1X2", "DC", "DNB", "OU", "OU_1H", "BTTS", "TEAM_TOTAL_HOME", "TEAM_TOTAL_AWAY", "AH", "CORNERS_OU", "CARDS_OU"]


class SelectionConfig(BaseModel):
    min_odds: float = Field(1.20, gt=1)
    max_odds: float = Field(1.70, gt=1)
    min_confidence: int = Field(75, ge=0, le=100)
    min_ev: float = Field(0.03, ge=0)
    max_daily_picks: int = Field(3, ge=0, le=10)
    allow_combos: bool = False
    watch_margin: int = Field(10, ge=0, le=50)  # confidence points below the minimum that still count as "watch"
    markets: list[str] = Field(default_factory=lambda: list(ALL_MARKETS))

    @field_validator("markets")
    @classmethod
    def _known_markets(cls, markets: list[str]) -> list[str]:
        unknown = [m for m in markets if m not in ALL_MARKETS]
        if unknown:
            raise ValueError(t("config.unknown_market", markets=", ".join(unknown), allowed=", ".join(ALL_MARKETS)))
        return markets

    @model_validator(mode="after")
    def _check_range(self) -> SelectionConfig:
        if self.min_odds >= self.max_odds:
            raise ValueError(t("config.odds_range"))
        return self


class ConfidenceWeights(BaseModel):
    form: float = Field(0.20, ge=0, le=1)
    home_away: float = Field(0.15, ge=0, le=1)
    xg: float = Field(0.15, ge=0, le=1)
    injuries: float = Field(0.15, ge=0, le=1)
    strength: float = Field(0.10, ge=0, le=1)
    h2h: float = Field(0.05, ge=0, le=1)
    motivation: float = Field(0.10, ge=0, le=1)
    fatigue: float = Field(0.05, ge=0, le=1)
    market: float = Field(0.05, ge=0, le=1)

    @model_validator(mode="after")
    def _check_sum(self) -> ConfidenceWeights:
        total = sum(self.model_dump().values())
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(t("config.weights_sum", total=f"{total:.2f}"))
        return self


class ModelConfig(BaseModel):
    market_blend_weight: float = Field(0.4, ge=0, le=1)
    llm_max_adjustment_pp: float = Field(4, ge=0, le=20)
    elo_weight: float = Field(0.25, ge=0, le=1)
    xg_weight: float = Field(0.6, ge=0, le=1)
    half_life_days: float = Field(180, gt=0)
    prior_matches: float = Field(3, ge=0)
    fit_window_days: int = Field(540, ge=30)
    max_contradiction_pp: float = Field(15, gt=0, le=50)
    corners_dispersion: float = Field(1.25, ge=1)
    cards_dispersion: float = Field(1.35, ge=1)
    replacement_factor: float = Field(0.5, ge=0, le=1)
    max_injury_attack_loss: float = Field(0.20, ge=0, le=0.5)
    max_injury_defence_gain: float = Field(0.15, ge=0, le=0.5)
    fatigue_penalty: float = Field(0.03, ge=0, le=0.15)
    short_rest_days: float = Field(3, ge=1, le=7)
    motivation_max_effect: float = Field(0.03, ge=0, le=0.15)
    version: str = "dc-elo-v1"


class IngestionConfig(BaseModel):
    history_depth: int = Field(20, ge=5, le=40)
    include_other_leagues: bool = False
    max_deep_matches: int = Field(60, ge=1, le=500)


class ApiFootballConfig(BaseModel):
    base_url: str = "https://v3.football.api-sports.io"
    requests_per_minute: int = Field(10, ge=1)
    daily_reserve: int = Field(5, ge=0)
    timeout_seconds: float = Field(20, gt=0)
    max_retries: int = Field(3, ge=0, le=10)


class CacheTtlConfig(BaseModel):
    fixtures: int = Field(21_600, ge=0)
    standings: int = Field(21_600, ge=0)
    team_history: int = Field(43_200, ge=0)
    injuries: int = Field(7_200, ge=0)
    odds: int = Field(600, ge=0)
    lineups: int = Field(120, ge=0)
    h2h: int = Field(86_400, ge=0)


class ScheduleConfig(BaseModel):
    daily_pipeline: str = "08:00"
    second_run: str = "15:00"
    cache_cleanup: str = "03:00"
    catch_up_on_start: bool = True

    @field_validator("daily_pipeline", "second_run", "cache_cleanup")
    @classmethod
    def _check_time(cls, value: str) -> str:
        if not _HHMM.match(value):
            raise ValueError(t("config.bad_time"))
        return value


class LeagueConfig(BaseModel):
    api_id: int = Field(gt=0)
    name_az: str = Field(min_length=1)
    tier: int = Field(1, ge=1, le=3)
    country: str | None = None
    # Free history sources: football-data.org competition code (e.g. PL) and
    # football-data.co.uk division (e.g. E0, or "new:ARG" for the extra-leagues files).
    fd_org: str | None = None
    fd_couk: str | None = None


class FootballDataOrgConfig(BaseModel):
    base_url: str = "https://api.football-data.org/v4"
    requests_per_minute: int = Field(10, ge=1)
    timeout_seconds: float = Field(30, gt=0)
    max_retries: int = Field(3, ge=0, le=10)


class FootballDataCoUkConfig(BaseModel):
    base_url: str = "https://www.football-data.co.uk"
    seasons_back: int = Field(1, ge=0, le=5)  # previous seasons loaded besides the current one
    timeout_seconds: float = Field(60, gt=0)
    max_retries: int = Field(3, ge=0, le=10)


class ExternalDataConfig(BaseModel):
    enabled: bool = True
    cache_hours: float = Field(12, ge=0)
    name_match_threshold: float = Field(0.84, ge=0.5, le=1.0)
    football_data_org: FootballDataOrgConfig = Field(default_factory=FootballDataOrgConfig)
    football_data_couk: FootballDataCoUkConfig = Field(default_factory=FootballDataCoUkConfig)


class AppConfig(BaseModel):
    bankroll: BankrollConfig = Field(default_factory=BankrollConfig)
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    confidence_weights: ConfidenceWeights = Field(default_factory=ConfidenceWeights)
    model: ModelConfig = Field(default_factory=ModelConfig)
    lineup_monitoring: bool = True
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    api_football: ApiFootballConfig = Field(default_factory=ApiFootballConfig)
    cache_ttl_seconds: CacheTtlConfig = Field(default_factory=CacheTtlConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    external_data: ExternalDataConfig = Field(default_factory=ExternalDataConfig)
    leagues: list[LeagueConfig] = Field(default_factory=list)

    @field_validator("leagues")
    @classmethod
    def _unique_league_ids(cls, leagues: list[LeagueConfig]) -> list[LeagueConfig]:
        seen: set[int] = set()
        for league in leagues:
            if league.api_id in seen:
                raise ValueError(t("config.duplicate_league", api_id=league.api_id))
            seen.add(league.api_id)
        return leagues


# --------------------------------------------------------------------------- .env


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        protected_namespaces=("settings_",),
    )

    football_api_key: SecretStr = SecretStr("")
    football_data_org_key: SecretStr = SecretStr("")
    odds_api_key: SecretStr = SecretStr("")

    llm_enabled: bool = False
    llm_provider: Literal["anthropic", "openrouter"] = "anthropic"
    claude_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    model_name: str = "claude-opus-5-5"
    llm_light_model: str = "claude-haiku-4-5"

    telegram_enabled: bool = True
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    paper_mode: bool = True
    timezone: str = "Asia/Baku"
    database_url: str = "sqlite:///data/app.db"
    dashboard_password: SecretStr = SecretStr("")
    default_language: str = "az"
    log_level: str = "INFO"
    config_path: Path = PROJECT_ROOT / "config.yaml"
    web_host: str = "127.0.0.1"
    web_port: int = 8000

    # Optional overrides of config.yaml values.
    starting_bankroll: float | None = None
    target_bankroll: float | None = None
    min_odds: float | None = None
    max_odds: float | None = None
    min_confidence: int | None = None
    max_daily_picks: int | None = None
    lineup_monitoring: bool | None = None

    @field_validator("timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(t("config.bad_timezone")) from None
        return value

    @field_validator("telegram_chat_id")
    @classmethod
    def _check_chat_ids(cls, value: str) -> str:
        for part in _split_ids(value):
            if not re.fullmatch(r"-?\d+", part):
                raise ValueError(t("config.bad_chat_id"))
        return value

    @field_validator("database_url")
    @classmethod
    def _resolve_sqlite_path(cls, value: str) -> str:
        """Relative SQLite paths are resolved against the project root, not the CWD."""
        prefix = "sqlite:///"
        if not value.startswith(prefix) or value.endswith(":memory:"):
            return value
        path = Path(value.removeprefix(prefix))
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return f"{prefix}{path.as_posix()}"

    @property
    def telegram_chat_ids(self) -> list[int]:
        return [int(part) for part in _split_ids(self.telegram_chat_id)]

    @property
    def telegram_configured(self) -> bool:
        return self.telegram_enabled and bool(self.telegram_bot_token.get_secret_value())


def _split_ids(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


_ENV_OVERRIDES: dict[str, tuple[str, ...]] = {
    "starting_bankroll": ("bankroll", "starting"),
    "target_bankroll": ("bankroll", "target"),
    "min_odds": ("selection", "min_odds"),
    "max_odds": ("selection", "max_odds"),
    "min_confidence": ("selection", "min_confidence"),
    "max_daily_picks": ("selection", "max_daily_picks"),
    "lineup_monitoring": ("lineup_monitoring",),
}


def _apply_env_overrides(raw: dict[str, Any], settings: Settings) -> None:
    for attr, path in _ENV_OVERRIDES.items():
        value = getattr(settings, attr)
        if value is None:
            continue
        node = raw
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value


# ----------------------------------------------------------------- error text

_ERROR_KEYS = {
    "greater_than": "config.err.greater_than",
    "greater_than_equal": "config.err.greater_than_equal",
    "less_than": "config.err.less_than",
    "less_than_equal": "config.err.less_than_equal",
    "missing": "config.err.missing",
    "float_parsing": "config.err.number",
    "float_type": "config.err.number",
    "int_parsing": "config.err.integer",
    "int_type": "config.err.integer",
    "int_from_float": "config.err.integer",
    "bool_parsing": "config.err.boolean",
    "bool_type": "config.err.boolean",
    "literal_error": "config.err.literal",
}


def describe_validation_error(exc: ValidationError) -> str:
    """Pydantic's messages are English; rebuild them in Azerbaijani."""
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "config"
        ctx = error.get("ctx") or {}
        if error["type"] == "value_error":
            message = str(ctx.get("error", "")) or t("config.err.invalid")
        elif error["type"] in _ERROR_KEYS:
            try:
                message = t(_ERROR_KEYS[error["type"]], **ctx)
            except KeyError:
                message = t("config.err.invalid")
        else:
            message = t("config.err.invalid")
        parts.append(f"{location}: {message}")
    return "; ".join(parts)


# --------------------------------------------------------------------- loaders


def load_settings(**overrides: Any) -> Settings:
    try:
        return Settings(**overrides)
    except ValidationError as exc:
        raise ConfigError(t("error.config", detail=describe_validation_error(exc))) from exc


def load_config(path: Path, settings: Settings | None = None) -> AppConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise ConfigError(t("error.config_missing", path=str(path))) from None
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else "?"
        raise ConfigError(t("error.config_yaml", line=line)) from exc
    if not isinstance(raw, dict):
        raise ConfigError(t("error.config_yaml", line="?"))
    if settings is not None:
        _apply_env_overrides(raw, settings)
    try:
        return AppConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(t("error.config", detail=describe_validation_error(exc))) from exc


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return load_settings()


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    settings = get_settings()
    return load_config(settings.config_path, settings)
