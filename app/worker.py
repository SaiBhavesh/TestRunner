"""In-process worker: a background thread that polls the DB and advances orders (spec 3).

A second thread, the stuck-order check, fails orders that make no progress for STUCK_LIMIT_MIN. It runs
separately so it still works if the worker thread itself is blocked.
"""
import threading
import traceback

from app import config, db
from app.agent import pipeline
from app.agent.decisions import log_decision, minutes_since
from app.log import log

STUCK_CHECK_INTERVAL_SEC = 30


def tick() -> None:
    placeholders = ",".join("?" for _ in pipeline.ACTIVE_STATUSES)
    rows = db.all_rows(f"SELECT id, num FROM orders WHERE status IN ({placeholders}) ORDER BY num",
                       pipeline.ACTIVE_STATUSES)
    for row in rows:
        try:
            pipeline.step(row["id"])
        except db.OrderClosed as e:
            log.warning("order #%s: late result discarded (%s)", row["num"], e)
        except Exception as e:
            traceback.print_exc()
            reason = f"{type(e).__name__}: {e}"[:300]
            if db.get_order(row["id"])["status"] == "failed":  # stuck check got there first; keep its reason
                log.warning("order #%s: error after it was already failed: %s", row["num"], reason)
                continue
            log.error("order #%s: failed: %s", row["num"], reason)
            db.set_status(row["id"], "failed", reason, fail_reason=reason)


def fail_stuck_orders() -> None:
    """Spec-adjacent safety net: Human Test orders waiting on testers or a Terac quote are excluded,
    because those waits are legitimately long (the stall rule in spec 7 covers them)."""
    rows = db.all_rows(
        "SELECT * FROM orders WHERE status IN ('paid', 'scanning', 'analyzing') "
        "OR (status = 'scanned' AND tier = 'quick')")
    for order in rows:
        stuck_for = minutes_since(order["updated_at"])
        if stuck_for < config.STUCK_LIMIT_MIN:
            continue
        reason = (f"Stuck in '{order['status']}' for {stuck_for:.0f} min with no progress "
                  f"(limit {config.STUCK_LIMIT_MIN:.0f} min); marked failed.")
        if db.fail_if_unchanged(order, reason):
            log_decision(order["id"], reason)
            log.error("order #%s: %s", order["num"], reason)


def run_forever(stop: threading.Event) -> None:
    while not stop.is_set():
        tick()
        stop.wait(config.WORKER_INTERVAL_SEC)


def run_stuck_checks(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            fail_stuck_orders()
        except Exception:
            traceback.print_exc()
            log.error("stuck-order check crashed this round; will retry in %ss", STUCK_CHECK_INTERVAL_SEC)
        stop.wait(STUCK_CHECK_INTERVAL_SEC)


def start() -> threading.Event:
    stop = threading.Event()
    threading.Thread(target=run_forever, args=(stop,), daemon=True, name="testrunner-worker").start()
    threading.Thread(target=run_stuck_checks, args=(stop,), daemon=True, name="testrunner-stuck-check").start()
    log.info("worker started (Gemini %s, fallback %s, timeout %.0fs; stuck limit %.0f min)",
             config.GEMINI_MODEL, config.GEMINI_FALLBACK_MODEL or "none",
             config.GEMINI_TIMEOUT_SEC, config.STUCK_LIMIT_MIN)
    return stop
