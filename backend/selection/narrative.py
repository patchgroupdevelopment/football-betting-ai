"""Azerbaijani explanations built only from computed facts — nothing is invented.

- ``why``     — the strongest supporting factors, each as a concrete fact;
- ``against`` — the weakest factors, the plain loss probability and the data
                gaps. It is never empty: every pick has a case against it;
- ``analysis_lines`` — the ANALİZ bullets (form, home/away, H2H, absences, xG …).
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.analyzers.context import MatchContext, TeamContext
from backend.analyzers.form import (
    FormStats,
    btts_rate,
    cards_over_rate,
    corners_over_rate,
    first_half_over_rate,
    team_goals_over_rate,
    total_goals_over_rate,
)
from backend.analyzers.injuries import Absence
from backend.i18n import t
from backend.i18n.az import ABSENCE_KINDS, DIRECTIONS, FORM_LETTERS, MOTIVATION_TAGS, PLAYER_ROLES, POSITIONS
from backend.predictors.model import MatchModel
from backend.selection.candidates import Candidate

WHY_THRESHOLD = 60.0
AGAINST_THRESHOLD = 45.0
MAX_WHY = 4
MAX_AGAINST = 6
ROLE_PRIORITY = ["top_scorer", "key_goalkeeper", "top_assister", "key_attacker", "key_defender", "key_midfielder"]


# ------------------------------------------------------------------ helpers


def pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}"


def num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def line_text(line: float | None) -> str:
    return "" if line is None else f"{line:g}"


def sequence(form: FormStats) -> str:
    return "".join(FORM_LETTERS[letter] for letter in form.sequence) or "—"


def record(form: FormStats) -> str:
    return t("an.record", w=form.wins, d=form.draws, l=form.losses, pts=form.points)


def rest_text(team: TeamContext) -> str:
    rest = team.fatigue.rest_days
    return t("common.unknown") if rest is None else f"{rest:g}"


def motivation_text(team: TeamContext) -> str:
    label = MOTIVATION_TAGS.get(team.motivation.tags[0], team.motivation.tags[0]) if team.motivation.tags else ""
    standing = team.motivation.standing
    if standing is not None:
        return f"{label} {t('an.standing', rank=standing.rank, points=standing.points)}"
    return label


def player_text(absence: Absence, *, with_kind: bool = True) -> str:
    details: list[str] = []
    role = absence.role
    if role is not None:
        ordered = sorted((r for r in role.roles if r in PLAYER_ROLES), key=ROLE_PRIORITY.index)
        labels = [PLAYER_ROLES[r] for r in ordered]
        if labels:
            details.append(", ".join(labels[:2]))
        elif role.position in POSITIONS:
            details.append(POSITIONS[role.position])
        if role.goals:
            details.append(t("an.goals_count", goals=role.goals))
    if with_kind:
        details.append(ABSENCE_KINDS.get(absence.kind, absence.kind))
    return f"{absence.name} ({', '.join(details)})" if details else absence.name


def players_text(absences: Sequence[Absence], *, with_kind: bool = True) -> str:
    # Most important first: regulars, then by minutes share.
    ordered = sorted(absences, key=lambda a: (a.role.minutes_share if a.role else 0.0), reverse=True)
    return "; ".join(player_text(a, with_kind=with_kind) for a in ordered[:4])


def _rate_in_direction(rate_value: float | None, direction: str | None) -> float | None:
    if rate_value is None:
        return None
    return rate_value if direction in ("OVER", "YES") else 1.0 - rate_value


def _expected_xg(attacking: FormStats, defending: FormStats) -> float | None:
    if attacking.xg_for_avg is None or defending.xg_against_avg is None:
        return None
    return (attacking.xg_for_avg + defending.xg_against_avg) / 2


def _xgd(form: FormStats) -> str:
    if form.xg_for_avg is None or form.xg_against_avg is None:
        return "—"
    return f"{form.xg_for_avg - form.xg_against_avg:+.2f}"


# ------------------------------------------------------------------- facts


def _fact(factor: str, c: Candidate, ctx: MatchContext, mm: MatchModel, supporting: bool) -> str | None:
    home, away = ctx.home, ctx.away
    direction = DIRECTIONS.get(c.direction or "", "")
    line = line_text(c.line)

    if factor == "market":
        key = "fact.market" if supporting or c.p_market >= c.p_model else "fact.market_disagrees"
        return t(key, market=pct(c.p_market), model=pct(c.p_model))
    if factor == "strength" and c.family == "side":
        return t("fact.elo", home=home.name, home_rating=round(mm.elo_home), away=away.name, away_rating=round(mm.elo_away))
    if factor == "motivation" and c.family in ("side", "team_goals"):
        return t("fact.motivation", home=home.name, home_reason=motivation_text(home), away=away.name, away_reason=motivation_text(away))
    if factor == "fatigue":
        return t("fact.rest", home=home.name, home_rest=rest_text(home), away=away.name, away_rest=rest_text(away))

    if c.family == "side":
        own, opp = (home, away) if c.side == "home" else (away, home)
        if factor == "form":
            return t("fact.form_points", team=own.name, points=own.form5.points, sequence=sequence(own.form5),
                     opponent=opp.name, opp_points=opp.form5.points, opp_sequence=sequence(opp.form5))
        if factor == "home_away":
            return t("fact.venue_points", home=home.name, n=home.venue5.matches, points=home.venue5.points,
                     away=away.name, opp_n=away.venue5.matches, opp_points=away.venue5.points)
        if factor == "xg":
            if home.form10.xg_for_avg is None or away.form10.xg_for_avg is None:
                return t("fact.no_xg")
            return t("fact.xg_diff", home=home.name, home_xgd=_xgd(home.form10), away=away.name, away_xgd=_xgd(away.form10))
        if factor == "injuries":
            team = opp if supporting else own
            return t("fact.absences", team=team.name, players=players_text(team.absences.absences)) if team.absences.absences else None
        if factor == "h2h" and ctx.h2h.count:
            return t("fact.h2h_record", n=ctx.h2h.count, home=home.name, home_wins=ctx.h2h.home_wins,
                     draws=ctx.h2h.draws, away=away.name, away_wins=ctx.h2h.away_wins)
        return None

    if factor == "injuries":
        absences = [*home.absences.absences, *away.absences.absences]
        return t("fact.absences_both", players=players_text(absences)) if absences else None

    if c.family in ("goals", "first_half"):
        over = first_half_over_rate if c.family == "first_half" else total_goals_over_rate
        if factor == "form":
            return t("fact.total_rates", line=line, dir=direction, home=home.name,
                     home_rate=pct(_rate_in_direction(over(home.history[:5], c.line or 0), c.direction)),
                     away=away.name, away_rate=pct(_rate_in_direction(over(away.history[:5], c.line or 0), c.direction)))
        if factor == "home_away":
            return t("fact.total_venue_rates", line=line, dir=direction, home=home.name,
                     home_rate=pct(_rate_in_direction(over(home.venue_history[:10], c.line or 0), c.direction)),
                     away=away.name, away_rate=pct(_rate_in_direction(over(away.venue_history[:10], c.line or 0), c.direction)))
        if factor == "xg":
            eh, ea = _expected_xg(home.form10, away.form10), _expected_xg(away.form10, home.form10)
            if eh is None or ea is None:
                return t("fact.no_xg")
            scale = mm.goal_model.first_half_fraction if c.family == "first_half" else 1.0
            return t("fact.xg_expected_total", expected=num((eh + ea) * scale), line=line)
        if factor == "strength":
            lh, la = mm.lambdas
            return t("fact.model_goals", home=home.name, lh=num(lh), away=away.name, la=num(la), total=num(lh + la))
        if factor == "h2h" and ctx.h2h.count:
            return t("fact.h2h_total", n=ctx.h2h.count, line=line, dir=direction,
                     rate=pct(_rate_in_direction(over(list(ctx.h2h.matches), c.line or 0), c.direction)),
                     avg=num(ctx.h2h.avg_goals, 1))
        if factor == "motivation":
            levels = (home.motivation.level, away.motivation.level)
            if min(levels) >= 85:
                return t("fact.motivation_high_both")
            if min(levels) <= 45:
                return t("fact.motivation_low")
        return None

    if c.family == "btts":
        if factor == "form":
            return t("fact.btts_rates", home=home.name, home_rate=pct(btts_rate(home.history[:5])),
                     away=away.name, away_rate=pct(btts_rate(away.history[:5])))
        if factor == "home_away":
            return t("fact.btts_venue_rates", home=home.name, home_rate=pct(btts_rate(home.venue_history[:10])),
                     away=away.name, away_rate=pct(btts_rate(away.venue_history[:10])))
        if factor == "xg":
            eh, ea = _expected_xg(home.form10, away.form10), _expected_xg(away.form10, home.form10)
            if eh is None or ea is None:
                return t("fact.no_xg")
            return t("fact.xg_expected_teams", home=home.name, eh=num(eh), away=away.name, ea=num(ea))
        if factor == "strength":
            return t("fact.model_btts", p=pct(mm.goal_model.btts()))
        if factor == "h2h" and ctx.h2h.count:
            return t("fact.h2h_btts", n=ctx.h2h.count, rate=pct(btts_rate(list(ctx.h2h.matches))))
        return None

    if c.family == "team_goals":
        team, opp = (home, away) if c.side == "home" else (away, home)
        if factor == "form":
            return t("fact.team_scoring", team=team.name, gf=num(team.form5.goals_for_avg, 1),
                     opponent=opp.name, ga=num(opp.form5.goals_against_avg, 1))
        if factor == "home_away":
            return t("fact.team_venue_scoring", team=team.name, gf=num(team.venue5.goals_for_avg, 1),
                     opponent=opp.name, ga=num(opp.venue5.goals_against_avg, 1))
        if factor == "xg":
            expected = _expected_xg(team.form10, opp.form10)
            return t("fact.xg_expected_team", team=team.name, expected=num(expected), line=line) if expected is not None else t("fact.no_xg")
        if factor == "strength":
            lam = mm.lambdas[0] if c.side == "home" else mm.lambdas[1]
            return t("fact.model_team_goals", team=team.name, lam=num(lam), line=line)
        if factor == "h2h" and ctx.h2h.count:
            hits = sum(1 for m in ctx.h2h.matches if (m.goals_for if c.side == "home" else m.goals_against) > (c.line or 0))
            rate_value = _rate_in_direction(hits / ctx.h2h.count, c.direction)
            return t("fact.h2h_team_goals", n=ctx.h2h.count, rate=pct(rate_value), team=team.name, line=line, dir=direction)
        return None

    # corners / cards
    corners = c.family == "corners"
    over = corners_over_rate if corners else cards_over_rate
    if factor == "form":
        return t("fact.corners_rates" if corners else "fact.cards_rates", line=line, dir=direction, home=home.name,
                 home_rate=pct(_rate_in_direction(over(home.history[:10], c.line or 0), c.direction)),
                 away=away.name, away_rate=pct(_rate_in_direction(over(away.history[:10], c.line or 0), c.direction)))
    if factor == "home_away":
        return t("fact.corners_venue" if corners else "fact.cards_venue", line=line, dir=direction, home=home.name,
                 home_rate=pct(_rate_in_direction(over(home.venue_history[:10], c.line or 0), c.direction)),
                 away=away.name, away_rate=pct(_rate_in_direction(over(away.venue_history[:10], c.line or 0), c.direction)))
    if factor == "strength":
        mean = mm.goal_model.corners_mean if corners else mm.goal_model.cards_mean
        return t("fact.model_corners" if corners else "fact.model_cards", expected=num(mean, 1), line=line)
    return None


# -------------------------------------------------------------- why/against


def why(c: Candidate, factors: dict[str, float | None], ctx: MatchContext, mm: MatchModel) -> list[str]:
    strong = sorted(
        ((name, value) for name, value in factors.items() if value is not None and value >= WHY_THRESHOLD),
        key=lambda item: item[1],
        reverse=True,
    )
    lines: list[str] = []
    for name, _value in strong:
        fact = _fact(name, c, ctx, mm, supporting=True)
        if fact and fact not in lines:
            lines.append(fact)
        if len(lines) >= MAX_WHY:
            break
    if c.family == "cards" and ctx.referee_cards_avg is not None and ctx.referee:
        lines.append(t("fact.referee_cards", referee=ctx.referee, avg=num(ctx.referee_cards_avg, 1)))
    return lines


def against(c: Candidate, factors: dict[str, float | None], ctx: MatchContext, mm: MatchModel) -> list[str]:
    weak = sorted(
        ((name, value) for name, value in factors.items() if value is not None and value <= AGAINST_THRESHOLD),
        key=lambda item: item[1],
    )
    lines: list[str] = []
    for name, _value in weak[:3]:
        fact = _fact(name, c, ctx, mm, supporting=False)
        if fact and fact not in lines:
            lines.append(fact)

    loss = (1 - c.push) * (1 - c.p_final)
    if loss > 0:
        lines.append(t("fact.loss_probability", loss=pct(loss), n=max(2, round(1 / loss))))
    for team in (ctx.home, ctx.away):
        if len(team.history) < 5:
            lines.append(t("fact.thin_history", team=team.name, n=len(team.history)))
    doubtful = [a for a in (*ctx.home.absences.absences, *ctx.away.absences.absences) if a.kind == "doubtful"]
    if doubtful:
        lines.append(t("fact.doubtful", players=players_text(doubtful, with_kind=False)))
    if c.family not in ("corners", "cards") and (ctx.home.form10.xg_for_avg is None or ctx.away.form10.xg_for_avg is None):
        fact = t("fact.no_xg")
        if fact not in lines:
            lines.append(fact)
    lines.append(t("fact.lineups_unknown"))
    return lines[:MAX_AGAINST]


def top_factors(factors: dict[str, float | None], count: int = 3) -> list[str]:
    ranked = sorted(
        ((name, value) for name, value in factors.items() if value is not None and value >= WHY_THRESHOLD),
        key=lambda item: item[1],
        reverse=True,
    )
    return [name for name, _ in ranked[:count]]


# ----------------------------------------------------------------- analysis


def _absence_list(team: TeamContext, kind: str) -> str:
    selected = [a for a in team.absences.absences if a.kind == kind]
    return players_text(selected, with_kind=False) if selected else t("an.none")


def analysis_lines(ctx: MatchContext, mm: MatchModel) -> list[str]:
    home, away = ctx.home, ctx.away
    lines = [
        t("an.form5", home=home.name, home_seq=sequence(home.form5), home_record=record(home.form5),
          away=away.name, away_seq=sequence(away.form5), away_record=record(away.form5)),
        t("an.form10", home=home.name, home_pts=home.form10.points, home_gf=round(home.form10.goals_for_avg * home.form10.matches),
          home_ga=round(home.form10.goals_against_avg * home.form10.matches), away=away.name, away_pts=away.form10.points,
          away_gf=round(away.form10.goals_for_avg * away.form10.matches), away_ga=round(away.form10.goals_against_avg * away.form10.matches)),
        t("an.venue", home=home.name, home_n=home.venue5.matches, home_record=record(home.venue5),
          away=away.name, away_n=away.venue5.matches, away_record=record(away.venue5)),
    ]
    if ctx.h2h.count:
        lines.append(t("an.h2h", n=ctx.h2h.count, home=home.name, home_wins=ctx.h2h.home_wins, draws=ctx.h2h.draws,
                       away=away.name, away_wins=ctx.h2h.away_wins, avg=num(ctx.h2h.avg_goals, 1)))
    else:
        lines.append(t("an.h2h_none"))
    lines.append(t("an.injuries", home=home.name, home_list=_absence_list(home, "injury"), away=away.name, away_list=_absence_list(away, "injury")))
    lines.append(t("an.suspensions", home=home.name, home_list=_absence_list(home, "suspension"), away=away.name, away_list=_absence_list(away, "suspension")))
    if any(a.kind == "doubtful" for a in (*home.absences.absences, *away.absences.absences)):
        lines.append(t("an.doubtful", home=home.name, home_list=_absence_list(home, "doubtful"), away=away.name, away_list=_absence_list(away, "doubtful")))
    if home.form10.xg_for_avg is not None and away.form10.xg_for_avg is not None:
        lines.append(t("an.xg", home=home.name, home_xg=num(home.form10.xg_for_avg), home_xga=num(home.form10.xg_against_avg),
                       away=away.name, away_xg=num(away.form10.xg_for_avg), away_xga=num(away.form10.xg_against_avg)))
    else:
        lines.append(t("an.xg_none"))
    lines.append(t("an.attack", home=home.name, home_gf=num(home.form10.goals_for_avg, 1), home_fts=pct(home.form10.failed_to_score_rate),
                   away=away.name, away_gf=num(away.form10.goals_for_avg, 1), away_fts=pct(away.form10.failed_to_score_rate)))
    lines.append(t("an.defence", home=home.name, home_ga=num(home.form10.goals_against_avg, 1), home_cs=pct(home.form10.clean_sheet_rate),
                   away=away.name, away_ga=num(away.form10.goals_against_avg, 1), away_cs=pct(away.form10.clean_sheet_rate)))
    lines.append(t("an.motivation", home=home.name, home_label=motivation_text(home), away=away.name, away_label=motivation_text(away)))
    lines.append(t("an.fatigue", home=home.name, home_rest=rest_text(home), away=away.name, away_rest=rest_text(away)))
    for team in (home, away):
        for note in team.absences.notes:
            lines.append(t(f"an.note.{note}", team=team.name))
    lines.append(t("an.model", home=home.name, lh=num(mm.lambdas[0]), away=away.name, la=num(mm.lambdas[1])))
    return lines
