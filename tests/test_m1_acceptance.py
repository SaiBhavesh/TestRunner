"""Milestone 1 acceptance check (spec 10), run in-process with all adapters mocked."""
import json

import pytest

from app import config, db, worker
from app.adapters import whop
from app.agent import decisions


def pay(client, order_id, amount=None):
    tier = client.get(f"/api/orders/{order_id}").json()["tier"]
    body = json.dumps({"order_id": order_id, "payment_id": "mock_pay_123456789",
                       "amount_usd": config.PRICES[tier] if amount is None else amount}).encode()
    return client.post("/webhooks/whop", content=body, headers={"X-Mock-Signature": whop.sign_mock(body)})


def run_worker(times=10):
    for _ in range(times):
        worker.tick()


def submit(client, token, pid, **overrides):
    data = {"completed": "yes", "rating": "7", "confusing": "The sign-up button was hard to find.",
            "broken": "Nothing looked broken on this page.", "comments": ""}
    data.update(overrides)
    return client.post(f"/t/{token}", params={config.TERAC_PARTICIPANT_PARAM: pid}, data=data)


def test_quick_check_end_to_end(client):
    res = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"})
    assert res.status_code == 200
    oid = res.json()["order_id"]

    run_worker()
    assert db.get_order(oid)["status"] == "created"  # nothing runs before payment

    assert pay(client, oid).json() == {"ok": True}
    run_worker()
    order = db.get_order(oid)
    assert order["status"] == "report_ready"

    page = client.get(f"/report/{oid}", params={"t": order["report_token"]})
    assert page.status_code == 200
    assert "Top fixes" in page.text and "JavaScript error" in page.text
    assert "human_results" not in json.loads(order["report_json"])


def test_human_test_end_to_end(client):
    res = client.post("/api/orders", json={"url": "https://example.com", "tier": "human",
                                           "task": "Can a user sign up?"})
    oid = res.json()["order_id"]
    pay(client, oid)
    run_worker()
    order = db.get_order(oid)
    assert order["status"] in ("study_launched", "collecting")

    for i in range(5):
        assert submit(client, order["study_token"], f"p{i}", completed="yes" if i < 3 else "no",
                      rating=str(4 + i), broken=f"Tester {i} says the email step never loaded.").status_code == 200
    run_worker()
    order = db.get_order(oid)
    assert order["status"] == "report_ready"

    human = json.loads(order["report_json"])["human_results"]
    assert human["participants"] == 5
    assert human["completion_rate"] == 0.6
    assert human["avg_rating"] == 6.0  # (4+5+6+7+8)/5
    assert human["recurring_problems"][0]["count"] == 3
    # Quotes are copied verbatim from the field the model points at (mock: R2's "broken" answer).
    assert human["recurring_problems"][1]["example_comment"] == "Tester 1 says the email step never loaded."
    page = client.get(f"/report/{oid}", params={"t": order["report_token"]})
    assert page.status_code == 200 and "What real testers found" in page.text

    spent = db.one("SELECT SUM(amount_usd) AS s FROM spend")["s"]
    assert spent == 20.0  # mock CPI $4 x 5


def test_report_requires_token(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    assert client.get(f"/report/{oid}", params={"t": "wrong"}).status_code == 404
    assert client.get(f"/report/{oid}").status_code == 404


def test_webhook_rejects_bad_signature_and_is_idempotent(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"}).json()["order_id"]
    body = json.dumps({"order_id": oid, "payment_id": "x", "amount_usd": 5}).encode()
    assert client.post("/webhooks/whop", content=body, headers={"X-Mock-Signature": "bad"}).status_code == 400
    assert pay(client, oid).json() == {"ok": True}
    assert pay(client, oid).json()["duplicate"] is True


def test_underpayment_not_marked_paid(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "human",
                                           "task": "Sign up"}).json()["order_id"]
    assert pay(client, oid, amount=1).json()["ok"] is False
    assert db.get_order(oid)["status"] == "created"


@pytest.mark.parametrize("url", ["ftp://example.com", "http://localhost/", "https://example.com:8080/",
                                 "not a url"])
def test_ssrf_guard_rejects(client, url):
    assert client.post("/api/orders", json={"url": url, "tier": "quick"}).status_code == 400


def test_ssrf_guard_rejects_private_ip_after_dns(client, monkeypatch):
    from app import urlguard
    monkeypatch.setattr(urlguard, "resolve_host", lambda host: ["10.0.0.5"])
    assert client.post("/api/orders", json={"url": "https://internal.example", "tier": "quick"}).status_code == 400


def test_participant_form_validation(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "human",
                                           "task": "Sign up"}).json()["order_id"]
    token = db.get_order(oid)["study_token"]
    assert client.get(f"/t/{token}").status_code == 200  # renders, but closed until paid + launched
    assert submit(client, token, "p1").status_code == 410

    pay(client, oid)
    run_worker()
    assert submit(client, token, "p1", confusing="too short").status_code == 400
    assert submit(client, token, "p1", rating="11").status_code == 400
    assert submit(client, token, "p1", completed="").status_code == 400
    assert submit(client, token, "p1").status_code == 200
    assert submit(client, token, "p1").status_code == 400  # same participant twice
    assert client.get("/t/not-a-real-token").status_code == 404


def test_budget_guard_blocks_launch(client, monkeypatch):
    monkeypatch.setattr(config, "TERAC_MAX_PER_ORDER", 10.0)
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "human",
                                           "task": "Sign up"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    order = db.get_order(oid)
    assert order["status"] == "report_ready"
    assert "human_results" not in json.loads(order["report_json"])
    assert db.one("SELECT COUNT(*) AS n FROM spend")["n"] == 0
    msgs = [e["message"] for e in db.all_rows("SELECT message FROM events WHERE type='decision'")]
    assert any("Not launching study" in m and "refund" in m for m in msgs)


def test_unreachable_site_pauses_before_terac(client, monkeypatch):
    from app.adapters import solari
    real_scan = solari._mock_scan
    monkeypatch.setattr(solari, "_mock_scan", lambda *a: {**real_scan(*a), "reachable": False})
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "human",
                                           "task": "Sign up"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    assert db.get_order(oid)["status"] == "report_ready"
    assert db.one("SELECT COUNT(*) AS n FROM studies")["n"] == 0
    statuses = [e["to_status"] for e in db.all_rows("SELECT to_status FROM events WHERE order_id=?", (oid,))]
    assert "paused_unreachable" in statuses


def test_agent_picks_task_when_none_given(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "human"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    order = db.get_order(oid)
    assert order["task"] and order["task_source"] == "agent"


def test_stall_decision(monkeypatch):
    monkeypatch.setattr(config, "TERAC_STALL_MIN", 20)
    assert decisions.stall_decision(db.now(), 1) == "continue"
    old = "2020-01-01T00:00:00+00:00"
    assert decisions.stall_decision(old, 3) == "analyze"
    assert decisions.stall_decision(old, 2) == "wait"


def test_public_url_with_mock_whop_requires_own_secret(monkeypatch):
    import importlib
    from app import main
    monkeypatch.setattr(config, "IS_LOCAL", False)
    monkeypatch.setattr(config, "WHOP_WEBHOOK_SECRET", "")
    with pytest.raises(RuntimeError, match="WHOP_WEBHOOK_SECRET"):
        importlib.reload(main)
    monkeypatch.setattr(config, "IS_LOCAL", True)
    importlib.reload(main)  # restore a working module for later tests


def test_shots_served_from_shots_dir(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    shot = json.loads(db.get_order(oid)["scan_json"])["screenshots"]["desktop"]
    assert (config.SHOTS_DIR / shot.rsplit("/", 1)[1]).exists()
    assert client.get("/" + shot).status_code == 200


def test_pages_render_and_ops_hides_secrets(client):
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "human", "task": "Sign up",
                                           "email": "secret@example.com"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    order = db.get_order(oid)
    assert client.get("/").status_code == 200
    assert client.get(f"/order/{oid}").status_code == 200
    ops = client.get("/ops")
    assert ops.status_code == 200
    for secret in ("secret@example.com", order["report_token"], order["study_token"], order["payment_id"], oid):
        assert secret not in ops.text
    assert order["payment_id"][-6:] in ops.text
