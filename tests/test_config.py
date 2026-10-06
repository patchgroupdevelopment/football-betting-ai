from __future__ import annotations

from pathlib import Path

import pytest

from backend.config import PROJECT_ROOT, ConfigError, Settings, load_config, load_settings


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_project_config_loads():
    config = load_config(PROJECT_ROOT / "config.yaml", _settings())
    assert config.bankroll.starting == 2.0 and config.bankroll.target == 10_000.0
    assert config.selection.min_odds == 1.20 and config.selection.max_odds == 1.70
    assert len(config.leagues) >= 13 and config.leagues[0].api_id == 2
    assert config.schedule.daily_pipeline == "08:00"


def test_env_overrides_yaml():
    config = load_config(PROJECT_ROOT / "config.yaml", _settings(min_odds=1.3, starting_bankroll=5, lineup_monitoring=False))
    assert config.selection.min_odds == 1.3
    assert config.bankroll.starting == 5
    assert config.lineup_monitoring is False


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_weights_must_sum_to_one(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, "confidence_weights:\n  form: 0.9\n"))
    assert "cəmi 1.00 olmalıdır" in info.value.user_message


def test_bad_schedule_time(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, 'schedule:\n  daily_pipeline: "25:00"\n'))
    assert "schedule.daily_pipeline" in info.value.user_message and "SS:DD" in info.value.user_message


def test_numeric_errors_are_azerbaijani(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, "selection:\n  min_odds: abc\n"))
    assert "selection.min_odds: rəqəm olmalıdır" in info.value.user_message


def test_odds_range_validated(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, "selection:\n  min_odds: 1.8\n  max_odds: 1.5\n"))
    assert "min_odds" in info.value.user_message


def test_yaml_syntax_error(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_config(_write(tmp_path, "bankroll: [unclosed\n"))
    assert "sintaksis xətası" in info.value.user_message


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError) as info:
        load_config(tmp_path / "absent.yaml")
    assert "tapılmadı" in info.value.user_message


def test_relative_sqlite_path_resolves_to_project_root():
    settings = _settings(database_url="sqlite:///data/app.db")
    assert settings.database_url == f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}"


def test_chat_ids_parsing():
    assert _settings(telegram_chat_id="123, -456").telegram_chat_ids == [123, -456]
    with pytest.raises(ConfigError) as info:
        load_settings(_env_file=None, telegram_chat_id="abc")
    assert "TELEGRAM_CHAT_ID" in info.value.user_message


def test_bad_timezone():
    with pytest.raises(ConfigError) as info:
        load_settings(_env_file=None, timezone="Mars/Olympus")
    assert "vaxt zonası" in info.value.user_message
