"""Web API. The full dashboard comes later; for now: status, today's matches, pyramid.

If DASHBOARD_PASSWORD is set, every route except /api/health requires HTTP
Basic auth (any username). All error texts are Azerbaijani.
"""

from __future__ import annotations

import html
import secrets
from datetime import date
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.i18n import t
from backend.services.errors import ProviderError
from backend.services.overview import build_daily_overview
from backend.services.picks import build_daily_analysis, get_match_analysis
from backend.services.system_status import collect_status
from backend.utils.timeutils import local_today

if TYPE_CHECKING:
    from backend.container import AppContainer

router = APIRouter()
_basic = HTTPBasic(auto_error=False)


def _container(request: Request) -> AppContainer:
    return request.app.state.container


def require_auth(
    request: Request, credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)]
) -> None:
    password = _container(request).settings.dashboard_password.get_secret_value()
    if not password:
        return
    if credentials is None or not secrets.compare_digest(
        credentials.password.encode("utf-8"), password.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail=t("web.unauthorized"), headers={"WWW-Authenticate": "Basic"})


PROTECTED = [Depends(require_auth)]


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/status", dependencies=PROTECTED)
def api_status(request: Request) -> dict[str, Any]:
    runner = getattr(request.app.state, "runner", None)
    status = collect_status(_container(request), runner.next_daily_run() if runner else None)
    return jsonable_encoder(status)


@router.get("/api/matches", dependencies=PROTECTED)
def api_matches(request: Request, day: Annotated[date | None, Query(alias="date")] = None) -> dict[str, Any]:
    container = _container(request)
    overview = build_daily_overview(
        container.db, day or local_today(container.tz), container.tz, container.daily_cutoff
    )
    data = jsonable_encoder(overview)
    data.update(
        priority_count=overview.priority_count,
        ready_count=overview.ready_count,
        incomplete_count=overview.incomplete_count,
    )
    return data


@router.get("/api/picks", dependencies=PROTECTED)
def api_picks(request: Request, day: Annotated[date | None, Query(alias="date")] = None) -> dict[str, Any]:
    container = _container(request)
    return jsonable_encoder(build_daily_analysis(container.db, day or local_today(container.tz), container.tz))


@router.get("/api/analysis/{match_id}", dependencies=PROTECTED)
def api_match_analysis(request: Request, match_id: int) -> dict[str, Any]:
    container = _container(request)
    view = get_match_analysis(container.db, match_id, container.tz)
    if view is None:
        raise HTTPException(status_code=404)
    return jsonable_encoder(view)


@router.get("/api/pyramid", dependencies=PROTECTED)
def api_pyramid(request: Request) -> dict[str, Any]:
    service = _container(request).pyramid_service()
    state = service.get_state()
    return {
        "state": jsonable_encoder(state)
        | {"remaining": state.remaining, "total_staked": state.total_staked, "target_reached": state.target_reached},
        "projection": jsonable_encoder(service.projection(state)),
    }


@router.get("/", response_class=HTMLResponse, dependencies=PROTECTED)
def index() -> str:
    links = [
        ("/api/status", t("web.endpoint.status")),
        ("/api/matches", t("web.endpoint.matches")),
        ("/api/picks", t("web.endpoint.picks")),
        ("/api/pyramid", t("web.endpoint.pyramid")),
    ]
    items = "".join(f'<li><a href="{url}">{html.escape(label)}</a> <code>{url}</code></li>' for url, label in links)
    return f"""<!doctype html>
<html lang="az">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(t("web.title"))}</title>
<style>
  :root {{ --bg: #f7f7f5; --fg: #1d1d1b; --muted: #6b6b66; --accent: #0b6e4f; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --bg: #141413; --fg: #ecebe6; --muted: #9a9993; --accent: #4fc79a; }} }}
  body {{ margin: 0; padding: 32px 16px; background: var(--bg); color: var(--fg);
         font: 16px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width: 640px; margin: 0 auto; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 8px; }}
  p {{ color: var(--muted); }}
  a {{ color: var(--accent); }}
  code {{ color: var(--muted); font-size: .85em; }}
</style>
</head>
<body>
<main>
  <h1>⚽ {html.escape(t("web.title"))}</h1>
  <p>{html.escape(t("web.running"))}</p>
  <p>{html.escape(t("web.dashboard_soon"))}</p>
  <ul>{items}</ul>
</main>
</body>
</html>"""


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 401:
        detail = t("web.unauthorized")
    elif exc.status_code == 404:
        detail = t("web.not_found")
    else:
        detail = t("web.error")
    return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))


async def _validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": t("web.invalid_request")}, status_code=422)


async def _provider_error(_request: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse({"detail": exc.user_message}, status_code=503)


def register_routes(app: FastAPI) -> None:
    app.include_router(router)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(ProviderError, _provider_error)
