"""/misli_12 1.45 — checks a price at the user's own bookmaker against the market's fair price."""

from __future__ import annotations

from backend.i18n import t
from backend.i18n.az import DECISIONS
from backend.models.constants import Decision
from backend.presenters.formatters import min_odds, text_message
from backend.presenters.labels import selection_label
from backend.presenters.messages import MessageBuilder, bold
from backend.services.picks import PickView
from backend.services.pyramid import PyramidState, next_balance
from backend.utils.formatting import format_money, format_odds, format_signed_percent

MIN_ODDS, MAX_ODDS = 1.01, 100.0


def price_value(view: PickView, odds: float) -> float:
    """EV at the given price with the system's final probability (refund on push not counted)."""
    return (view.p_final or 0.0) * odds - 1


def format_price_check(view: PickView | None, odds: float | None, bookmaker: str, state: PyramidState) -> MessageBuilder:
    if view is None or not view.has_candidate or not view.p_final:
        return text_message(t("price.no_pick"))
    if odds is None:
        return text_message(t("price.usage"))
    if not MIN_ODDS <= odds <= MAX_ODDS:
        return text_message(t("price.bad_odds"))

    ev = price_value(view, odds)
    minimum = min_odds(view)
    msg = MessageBuilder()
    msg.line(bold(t("price.title", bookmaker=bookmaker)))
    msg.blank()
    label = selection_label(view.market, view.selection, view.line, view.home, view.away)
    msg.line(t("price.pick", home=view.home, away=view.away, label=label))
    msg.line(
        t(
            "price.numbers",
            bookmaker=bookmaker,
            odds=format_odds(odds),
            fair=format_odds(minimum) if minimum else "—",
            best=format_odds(view.odds) if view.odds else "—",
        )
    )
    msg.line(t("price.ev", ev=format_signed_percent(ev, 1)))
    msg.blank()
    msg.line(t("price.ok") if ev >= 0 else t("price.bad"))
    if view.decision != Decision.BET:
        msg.line(t("price.not_bet", decision=DECISIONS.get(view.decision, view.decision)))
    if ev >= 0:
        msg.line(t("price.pyramid", before=format_money(state.balance), after=format_money(next_balance(state.balance, odds))))
    return msg
