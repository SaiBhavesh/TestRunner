"""Rules the agent uses to decide (spec 7). Pure functions plus a logger for type='decision' events."""
from datetime import datetime, timezone

from app import config, db


def log_decision(order_id: str | None, message: str) -> None:
    db.log_event(order_id, "decision", message)


def terac_spent_usd() -> float:
    row = db.one("SELECT COALESCE(SUM(amount_usd), 0) AS total FROM spend")
    return float(row["total"])


def budget_check(cpi_usd: float, participants: int) -> tuple[bool, str]:
    cost = round(cpi_usd * participants, 2)
    spent = terac_spent_usd()
    if cost > config.TERAC_MAX_PER_ORDER:
        return False, f"Study would cost ${cost:.2f}, over the per-order limit of ${config.TERAC_MAX_PER_ORDER:.2f}."
    if spent + cost > config.TERAC_BUDGET_TOTAL:
        return False, (f"Study would cost ${cost:.2f}; ${spent:.2f} already spent of "
                       f"${config.TERAC_BUDGET_TOTAL:.2f} total budget.")
    return True, f"Study costs ${cost:.2f} (${cpi_usd:.2f} x {participants}); within budget."


def minutes_since(iso: str) -> float:
    return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 60


def stall_decision(launched_at: str, received: int) -> str:
    """Returns 'continue' (not stalled), 'analyze' (stalled, enough data) or 'wait' (stalled, too few)."""
    if minutes_since(launched_at) < config.TERAC_STALL_MIN:
        return "continue"
    return "analyze" if received >= 3 else "wait"
