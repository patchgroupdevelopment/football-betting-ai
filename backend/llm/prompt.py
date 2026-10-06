"""The reviewer's instructions and the facts it gets about one pick.

The facts are only what the system computed or downloaded; the reviewer adds what it finds in the
news. The answer must be one JSON object; texts in it are Azerbaijani.
"""

from __future__ import annotations

from backend.analyzers.context import TeamContext
from backend.analyzers.form import FormStats
from backend.presenters.labels import selection_label
from backend.selection.engine import MatchEvaluation

SYSTEM_PROMPT = """You are a careful football (soccer) analyst acting as a critic for a statistical betting system.
The system has already computed the probabilities with a Dixon-Coles/Elo goal model blended with the bookmakers'
margin-free consensus (mostly the market). Do not redo any arithmetic: the system recalculates the value itself.

Your job is to find information the numbers cannot know and judge whether it weakens or supports the selection:
confirmed or likely absences, players returning, expected rotation (e.g. a more important match within 3-4 days),
a new manager, motivation (title race, relegation, nothing left to play for), travel, weather, pitch.

Rules:
- Use only the facts given below and what you find with web search about the last 7 days. Never invent players,
  injuries, quotes or results. If you found nothing relevant, say so and keep the news list empty.
- Every item in "news" must come from a search result you actually read.
- "veto": true only for concrete, sourced, material news the statistics cannot know (for example the top scorer and
  the first-choice goalkeeper ruled out, or heavy rotation announced). General doubt is not a reason to veto.
- "adjustment_pp": your change to the probability of the selection, in percentage points, between -{max_pp} and
  +{max_pp}. Use 0 when nothing material changes the picture.
- Nobody can guarantee a result: never claim certainty.
- Write "summary", "risks" and "news" in Azerbaijani (Latin script), short and concrete.

Answer with ONE JSON object and nothing else (no markdown):
{{"verdict": "support" | "neutral" | "against", "adjustment_pp": number, "veto": true | false,
 "summary": "1-2 sentences", "risks": ["..."], "news": ["..."]}}"""


def system_prompt(max_pp: float) -> str:
    return SYSTEM_PROMPT.format(max_pp=f"{max_pp:g}")


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def _form(stats: FormStats) -> str:
    if not stats.matches:
        return "no data"
    text = (
        f"{stats.matches} matches {stats.wins}W-{stats.draws}D-{stats.losses}L ({stats.sequence or '-'} newest first), "
        f"goals {stats.goals_for_avg:.2f} for / {stats.goals_against_avg:.2f} against per match"
    )
    if stats.xg_for_avg is not None and stats.xg_against_avg is not None:
        text += f", xG {stats.xg_for_avg:.2f} / {stats.xg_against_avg:.2f}"
    return text


def _team(label: str, team: TeamContext) -> list[str]:
    lines = [
        f"{label}: {team.name}",
        f"  form last 5: {_form(team.form5)}",
        f"  form last 10: {_form(team.form10)}",
        f"  {'home' if team.is_home else 'away'} games last 5: {_form(team.venue5)}",
    ]
    standing = team.motivation.standing
    if standing is not None:
        lines.append(f"  table: {standing.rank}/{standing.group_size}, {standing.points} pts from {standing.played} games")
    lines.append(f"  motivation tags: {', '.join(team.motivation.tags) or '-'}")
    rest = team.fatigue.rest_days
    lines.append(f"  rest days: {rest if rest is not None else 'n/a'}, matches in last 14 days: {team.fatigue.matches_last_14d}")
    absences = [
        f"{a.name} ({a.kind}{', ' + a.reason if a.reason else ''}{', ' + '/'.join(a.role.roles) if a.role and a.role.roles else ''})"
        for a in team.absences.absences
    ]
    lines.append(f"  known absences (API-Football): {'; '.join(absences) if absences else 'none listed'}")
    return lines


def build_prompt(evaluation: MatchEvaluation) -> str:
    ctx = evaluation.context
    best = evaluation.best
    assert best is not None
    c = best.candidate
    model = evaluation.match_model
    label = selection_label(c.market, c.selection, c.line, ctx.home.name, ctx.away.name)
    h2h = ctx.h2h
    lines = [
        f"Match: {ctx.home.name} vs {ctx.away.name}",
        f"Competition: {ctx.league_title}",
        f"Kick-off: {ctx.kickoff_local:%Y-%m-%d %H:%M} Baku time ({ctx.kickoff_utc:%Y-%m-%d %H:%M} UTC)",
        "",
        f"Selection under review: {label} [market {c.market}, selection {c.selection}"
        + (f", line {c.line:g}" if c.line is not None else "")
        + "]",
        f"Best odds {c.odds:.2f} ({c.bookmaker}); market fair probability {_pct(c.p_market)}; "
        f"statistical model {_pct(c.p_model)}; system's final probability {_pct(c.p_final)}; value (EV) {c.ev * 100:+.1f}%",
        f"System confidence {best.confidence}/100, risk {best.risk}",
        f"Model expected goals: {ctx.home.name} {model.lambdas[0]:.2f}, {ctx.away.name} {model.lambdas[1]:.2f}; "
        f"Elo {model.elo_home:.0f} vs {model.elo_away:.0f}",
        "",
        *_team("Home team", ctx.home),
        *_team("Away team", ctx.away),
        "",
        f"Head-to-head (last {h2h.count}): {ctx.home.name} {h2h.home_wins}W {h2h.draws}D {h2h.away_wins}L"
        + (f", {h2h.avg_goals:.1f} goals per match" if h2h.avg_goals is not None else ""),
        f"Data completeness: {ctx.quality.score}% (missing: {', '.join(k for k, v in ctx.quality.components.items() if not v) or 'nothing'})",
    ]
    if evaluation.why:
        lines += ["", "System's reasons for the pick (Azerbaijani):", *(f"- {line}" for line in evaluation.why)]
    if evaluation.against:
        lines += ["", "System's reasons against (Azerbaijani):", *(f"- {line}" for line in evaluation.against)]
    lines += ["", "Search the latest news about both teams for this match, then answer with the JSON object."]
    return "\n".join(lines)
