"""Builds the static dashboard (one self-contained HTML page) for GitHub Pages or local viewing."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from backend.config import AppConfig
from backend.dashboard.collect import collect
from backend.database.session import Database

TEMPLATE = Path(__file__).with_name("template.html")
PLACEHOLDER = "/*__DATA__*/null"


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    payload = payload.replace("</", "<\\/")  # the JSON sits inside a <script> element
    return TEMPLATE.read_text(encoding="utf-8").replace(PLACEHOLDER, payload)


def build_site(db: Database, config: AppConfig, tz: ZoneInfo, day: date, out_dir: Path, *, paper_mode: bool) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    page = out_dir / "index.html"
    page.write_text(render(collect(db, config, tz, day, paper_mode=paper_mode)), encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    return page
