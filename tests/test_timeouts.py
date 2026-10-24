"""Timeouts, Gemini fallback and the stuck-order check (no network)."""
import asyncio
import time

import httpx
import pytest
from google.genai import errors

from app import config, db, worker
from app.adapters import gemini, solari
from app.agent import report as rep


class FakeModels:
    """Stands in for client.models; each item in `script` is an exception to raise or a text to return."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item

        class R:
            text = item
        return R()


class FakeClient:
    def __init__(self, script):
        self.models = FakeModels(script)


def api_error(code, status):
    cls = errors.ServerError if code >= 500 else errors.ClientError
    return cls(code, {"error": {"code": code, "message": status, "status": status}})


@pytest.fixture
def real_gemini(monkeypatch):
    monkeypatch.setattr(config, "MOCK_GEMINI", False)
    monkeypatch.setattr(config, "GEMINI_MODEL", "main-model")
    monkeypatch.setattr(config, "GEMINI_FALLBACK_MODEL", "backup-model")
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def install(script):
        client = FakeClient(script)
        monkeypatch.setattr(gemini, "_get_client", lambda: client)
        return client.models
    return install


GOOD = '{"summary": "s", "main_task": "t", "why": "w"}'


def test_503_falls_back_to_second_model(real_gemini):
    models = real_gemini([api_error(503, "UNAVAILABLE"), GOOD])
    assert gemini.generate_json("p", rep.ProductSummary).summary == "s"
    assert models.calls == ["main-model", "backup-model"]


def test_deadline_504_falls_back(real_gemini):
    models = real_gemini([api_error(504, "DEADLINE_EXCEEDED"), GOOD])
    gemini.generate_json("p", rep.ProductSummary)
    assert models.calls == ["main-model", "backup-model"]


def test_network_timeout_falls_back(real_gemini):
    models = real_gemini([httpx.ReadTimeout("slow"), GOOD])
    gemini.generate_json("p", rep.ProductSummary)
    assert models.calls == ["main-model", "backup-model"]


def test_both_models_down_raises_clear_error(real_gemini):
    real_gemini([api_error(503, "UNAVAILABLE"), api_error(504, "DEADLINE_EXCEEDED")])
    with pytest.raises(gemini.GeminiUnavailable, match="main-model: 503.*backup-model: 504"):
        gemini.generate_json("p", rep.ProductSummary)


def test_429_retries_same_model(real_gemini):
    models = real_gemini([api_error(429, "RESOURCE_EXHAUSTED"), GOOD])
    gemini.generate_json("p", rep.ProductSummary)
    assert models.calls == ["main-model", "main-model"]


def test_other_errors_are_not_retried(real_gemini):
    real_gemini([api_error(400, "INVALID_ARGUMENT")])
    with pytest.raises(errors.ClientError):
        gemini.generate_json("p", rep.ProductSummary)


def test_solari_hard_limit(monkeypatch):
    async def hang(*args):
        await asyncio.sleep(10)
    monkeypatch.setattr(config, "MOCK_SOLARI", False)
    monkeypatch.setattr(config, "SOLARI_HARD_LIMIT_SEC", 0.2)
    monkeypatch.setattr(solari, "_real_scan", hang)
    with pytest.raises(solari.SolariTimeout, match="hard limit"):
        solari.scan("https://example.com", "1")


def _order_in(client, status, minutes_ago):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"}).json()["order_id"]
    old = "2020-01-01T00:00:00+00:00" if minutes_ago else db.now()
    db.execute("UPDATE orders SET status = ?, updated_at = ? WHERE id = ?", (status, old, oid))
    return oid


def test_stuck_order_is_failed_with_decision(client):
    oid = _order_in(client, "analyzing", minutes_ago=True)
    worker.fail_stuck_orders()
    order = db.get_order(oid)
    assert order["status"] == "failed" and "Stuck in 'analyzing'" in order["fail_reason"]
    decisions = db.all_rows("SELECT message FROM events WHERE order_id = ? AND type = 'decision'", (oid,))
    assert any("Stuck in 'analyzing'" in d["message"] for d in decisions)


def test_recent_and_waiting_orders_are_left_alone(client):
    fresh = _order_in(client, "analyzing", minutes_ago=False)
    created = _order_in(client, "created", minutes_ago=True)  # unpaid: not the agent's job
    worker.fail_stuck_orders()
    assert db.get_order(fresh)["status"] == "analyzing"
    assert db.get_order(created)["status"] == "created"


def test_late_result_cannot_overwrite_failed(client):
    oid = _order_in(client, "analyzing", minutes_ago=True)
    worker.fail_stuck_orders()
    with pytest.raises(db.OrderClosed):
        db.set_status(oid, "report_ready", "late")
    assert db.get_order(oid)["status"] == "failed"
