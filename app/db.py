"""SQLite storage. One short-lived connection per call keeps the web server and worker thread safe."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    num INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    url TEXT NOT NULL,
    tier TEXT NOT NULL,
    task TEXT,
    task_source TEXT,
    email TEXT,
    status TEXT NOT NULL,
    fail_reason TEXT,
    report_token TEXT NOT NULL,
    study_token TEXT UNIQUE,
    payment_id TEXT UNIQUE,
    checkout_id TEXT,
    amount_usd REAL,
    paid_at TEXT,
    product_summary TEXT,
    scan_json TEXT,
    report_json TEXT,
    prompt_version INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    delivered_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    type TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS studies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    request_id TEXT,
    quote_status TEXT,
    cpi_usd REAL,
    study_id TEXT,
    status TEXT,
    target INTEGER NOT NULL,
    launched_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT NOT NULL,
    participant_id TEXT,
    completed INTEGER NOT NULL,
    rating INTEGER NOT NULL,
    confusing TEXT NOT NULL,
    broken TEXT NOT NULL,
    comments TEXT,
    completion_code TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (order_id, participant_id)
);
CREATE TABLE IF NOT EXISTS prompt_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version INTEGER UNIQUE NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    reason TEXT,
    active INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS spend (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT,
    study_id TEXT,
    amount_usd REAL NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # Databases created before Milestone 4 lack this column.
        if "checkout_id" not in {r["name"] for r in conn.execute("PRAGMA table_info(orders)")}:
            conn.execute("ALTER TABLE orders ADD COLUMN checkout_id TEXT")
    from app.agent.report import seed_prompt_v1

    seed_prompt_v1()


def one(sql: str, params: tuple = ()) -> dict | None:
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def all_rows(sql: str, params: tuple = ()) -> list[dict]:
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def execute(sql: str, params: tuple = ()) -> int:
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def execute_count(sql: str, params: tuple = ()) -> int:
    """Like execute, but returns how many rows changed."""
    with connect() as conn:
        return conn.execute(sql, params).rowcount


class OrderClosed(Exception):
    """Raised when the worker tries to move an order that was already marked failed (e.g. by the stuck check)."""


def log_event(order_id: str | None, type_: str, message: str, from_status=None, to_status=None) -> None:
    execute(
        "INSERT INTO events (order_id, type, from_status, to_status, message, created_at) VALUES (?,?,?,?,?,?)",
        (order_id, type_, from_status, to_status, message, now()),
    )


def get_order(order_id: str) -> dict | None:
    return one("SELECT * FROM orders WHERE id = ?", (order_id,))


def set_status(order_id: str, new_status: str, message: str = "", **fields) -> None:
    """Change an order's status and write the matching events row (spec 4.1)."""
    order = get_order(order_id)
    if order is None:
        raise KeyError(f"order {order_id} not found")
    cols = {"status": new_status, "updated_at": now(), **fields}
    assignments = ", ".join(f"{k} = ?" for k in cols)
    # A failed order stays failed: checked inside the UPDATE so a late worker result can't overwrite it.
    guard = "" if new_status == "failed" else " AND status != 'failed'"
    if execute_count(f"UPDATE orders SET {assignments} WHERE id = ?{guard}", (*cols.values(), order_id)) == 0:
        raise OrderClosed(f"order #{order['num']} is already failed; refusing move to {new_status}")
    log_event(order_id, "transition", message or f"{order['status']} -> {new_status}", order["status"], new_status)
    from app.log import log

    log.info("order #%s: %s -> %s (%s)", order["num"], order["status"], new_status, (message or "")[:120])


def fail_if_unchanged(order: dict, reason: str) -> bool:
    """Mark failed only if status and updated_at are still what the caller saw. Returns True if it did."""
    changed = execute_count(
        "UPDATE orders SET status = 'failed', fail_reason = ?, updated_at = ? "
        "WHERE id = ? AND status = ? AND updated_at = ?",
        (reason, now(), order["id"], order["status"], order["updated_at"]),
    )
    if changed:
        log_event(order["id"], "transition", reason, order["status"], "failed")
    return bool(changed)


def update_order(order_id: str, **fields) -> None:
    cols = {"updated_at": now(), **fields}
    assignments = ", ".join(f"{k} = ?" for k in cols)
    execute(f"UPDATE orders SET {assignments} WHERE id = ?", (*cols.values(), order_id))


def loads(text: str | None):
    return json.loads(text) if text else None
