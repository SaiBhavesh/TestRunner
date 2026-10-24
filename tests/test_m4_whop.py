"""Milestone 4: real-mode Whop logic with the Whop API stubbed (no network)."""
import base64
import json
from datetime import datetime, timezone

from standardwebhooks import Webhook

from app import config, db
from app.adapters import whop

SECRET = "ws_test_secret"


def real_whop(monkeypatch, payments):
    monkeypatch.setattr(config, "MOCK_WHOP", False)
    monkeypatch.setattr(config, "WHOP_WEBHOOK_SECRET", SECRET)
    calls = []

    def fake_api(method, path, **kwargs):
        calls.append((method, path))
        if path == "/checkout_configurations":
            return {"id": "ch_test1", "purchase_url": "https://whop.com/checkout/plan_x/?session=ch_test1"}
        if path == "/payments":
            return {"data": list(payments.values()), "page_info": {}}
        return payments[path.rsplit("/", 1)[1]]

    monkeypatch.setattr(whop, "_api", fake_api)
    return calls


def signed(event: dict) -> tuple[bytes, dict]:
    body = json.dumps(event).encode()
    # Whop signs with the literal secret bytes (see whop_sdk.lib.verify_webhook).
    wh = Webhook(base64.b64encode(SECRET.encode()).decode())
    ts = datetime.now(timezone.utc)
    headers = {"webhook-id": "msg_1", "webhook-timestamp": str(int(ts.timestamp())),
               "webhook-signature": wh.sign("msg_1", ts, body.decode())}
    return body, headers


def payment(order_id, status="paid", total=5.0):
    return {"id": "pay_abc123456", "status": status, "usd_total": total, "metadata": {"order_id": order_id}}


def new_order(client):
    res = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"})
    assert res.status_code == 200
    return res.json()


def test_checkout_stores_id_and_returns_whop_url(client, monkeypatch):
    real_whop(monkeypatch, {})
    data = new_order(client)
    assert data["checkout_url"].startswith("https://whop.com/checkout/")
    assert db.get_order(data["order_id"])["checkout_id"] == "ch_test1"


def test_signed_webhook_marks_paid_after_api_check(client, monkeypatch):
    payments = {}
    real_whop(monkeypatch, payments)
    oid = new_order(client)["order_id"]
    payments["pay_abc123456"] = payment(oid)
    body, headers = signed({"type": "payment.succeeded", "data": {"id": "pay_abc123456"}})
    assert client.post("/webhooks/whop", content=body, headers=headers).json() == {"ok": True}
    order = db.get_order(oid)
    assert order["status"] == "paid" and order["payment_id"] == "pay_abc123456"
    # Replayed delivery is a no-op.
    assert client.post("/webhooks/whop", content=body, headers=headers).json()["duplicate"] is True


def test_webhook_bad_signature_rejected(client, monkeypatch):
    real_whop(monkeypatch, {})
    body, headers = signed({"type": "payment.succeeded", "data": {"id": "pay_x"}})
    headers["webhook-signature"] = "v1,AAAA"
    assert client.post("/webhooks/whop", content=body, headers=headers).status_code == 400


def test_webhook_unpaid_payment_rejected(client, monkeypatch):
    payments = {}
    real_whop(monkeypatch, payments)
    oid = new_order(client)["order_id"]
    payments["pay_abc123456"] = payment(oid, status="open")
    body, headers = signed({"type": "payment.succeeded", "data": {"id": "pay_abc123456"}})
    assert client.post("/webhooks/whop", content=body, headers=headers).status_code == 400
    assert db.get_order(oid)["status"] == "created"


def test_other_events_ignored(client, monkeypatch):
    real_whop(monkeypatch, {})
    body, headers = signed({"type": "membership.activated", "data": {"id": "mem_1"}})
    assert client.post("/webhooks/whop", content=body, headers=headers).json()["ignored"] is True


def test_order_page_payment_check_marks_paid(client, monkeypatch):
    payments = {}
    real_whop(monkeypatch, payments)
    oid = new_order(client)["order_id"]
    assert client.get(f"/order/{oid}").status_code == 200
    assert db.get_order(oid)["status"] == "created"  # nothing paid yet
    payments["pay_abc123456"] = payment(oid)
    from app import main
    main._last_payment_check.clear()
    client.get(f"/order/{oid}")
    assert db.get_order(oid)["status"] == "paid"


def test_human_test_hidden_by_default(client, monkeypatch):
    monkeypatch.setattr(config, "HUMAN_TEST_ENABLED", False)
    res = client.post("/api/orders", json={"url": "https://example.com", "tier": "human", "task": "Sign up"})
    assert res.status_code == 400 and "coming soon" in res.json()["detail"]
    page = client.get("/")
    assert "Human Test with real testers coming soon" in page.text and 'value="human"' not in page.text
