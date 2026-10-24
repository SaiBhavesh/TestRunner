"""Milestone 3: Exa query building and the real adapter's request shape (no network)."""
import json
from types import SimpleNamespace

import pytest

from app import config, db
from app.adapters import exa, gemini


def test_build_query_dedupes_and_orders():
    scan = {"h1": "Get paid for your Expertise", "meta_description": "Join experts earning on AI projects.",
            "title": "Terac | Get paid for your expertise"}
    # The title contains the H1, so it adds nothing and is dropped.
    assert exa.build_query(scan) == "Get paid for your Expertise. Join experts earning on AI projects"
    scan["title"] = "Terac: expert network"
    assert exa.build_query(scan).endswith(". Terac: expert network")
    assert exa.build_query({"h1": "Acme", "meta_description": "", "title": "Acme"}) == "Acme"
    assert exa.build_query({"title": "Only a title"}) == "Only a title"  # older scans lack h1/meta
    assert exa.build_query({"h1": "Fast.", "meta_description": "Private analytics.", "title": ""}) == \
        "Fast. Private analytics"  # no double full stops
    with pytest.raises(ValueError):
        exa.build_query({"h1": "", "meta_description": "", "title": ""})


class FakeExa:
    def __init__(self, results):
        self.results, self.calls = results, []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        return SimpleNamespace(results=self.results)


@pytest.fixture
def real_exa(monkeypatch):
    monkeypatch.setattr(config, "MOCK_EXA", False)

    def install(results):
        fake = FakeExa(results)
        monkeypatch.setattr(exa, "_get_client", lambda: fake)
        return fake
    return install


def test_threads_request_and_mapping(real_exa):
    fake = real_exa([SimpleNamespace(title="Hiring is hard", url="https://www.indiehackers.com/post/x",
                                     published_date="2026-08-21T00:00:00.000Z", highlights=["  a  verbatim\nsnippet "])])
    threads = exa.find_threads("Acme. Invoices in a minute")
    query, kwargs = fake.calls[0]
    assert query == "People discussing: Acme. Invoices in a minute"
    assert kwargs["timeout"] == config.EXA_TIMEOUT_SEC
    assert kwargs["num_results"] == 5 and kwargs["include_domains"] == exa.THREAD_DOMAINS
    assert kwargs["start_published_date"].endswith("Z")
    assert threads == [{"title": "Hiring is hard", "url": "https://www.indiehackers.com/post/x",
                        "published_date": "2026-08-21", "why_relevant": "a verbatim snippet"}]


def test_competitors_exclude_own_domain_and_own_profile(real_exa):
    names = ["Acme Inc", "Co 1", "Co 2", "ACME Analytics", "Co 4", "Co 5"]
    fake = real_exa([SimpleNamespace(title=n, url=f"https://exa.ai/library/organization/{i}") for i, n in enumerate(names)])
    comps = exa.find_competitors("Acme", "https://www.acme.com/pricing")
    _, kwargs = fake.calls[0]
    assert kwargs["category"] == "company" and kwargs["exclude_domains"] == ["acme.com"]
    assert kwargs["contents"] is False and kwargs["timeout"] == config.EXA_TIMEOUT_SEC
    assert [c["name"] for c in comps] == ["Co 1", "Co 2", "Co 4"]


def test_exa_failure_still_delivers_report(client, monkeypatch):
    from tests.test_m1_acceptance import pay, run_worker

    def boom(*a):
        raise TimeoutError("Exa took too long")
    monkeypatch.setattr(exa, "find_threads", boom)
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"}).json()["order_id"]
    pay(client, oid)
    run_worker()
    order = db.get_order(oid)
    assert order["status"] == "report_ready"
    report = json.loads(order["report_json"])
    assert report["where_your_customers_are"] == [] and report["similar_products"]  # other section survives
    page = client.get(f"/report/{oid}", params={"t": order["report_token"]})
    assert "Where your customers are" not in page.text and "Similar products" in page.text
    decisions = [e["message"] for e in db.all_rows("SELECT message FROM events WHERE type='decision'")]
    assert any("Exa failed (TimeoutError" in m and "Where your customers are" in m for m in decisions)


def test_quick_check_makes_no_product_summary_call(client, monkeypatch):
    calls = []
    real = gemini.generate_json
    monkeypatch.setattr(gemini, "generate_json", lambda p, s: calls.append(s.__name__) or real(p, s))
    oid = client.post("/api/orders", json={"url": "https://example.com", "tier": "quick"}).json()["order_id"]
    from tests.test_m1_acceptance import pay, run_worker
    pay(client, oid)
    run_worker()
    assert db.get_order(oid)["status"] == "report_ready"
    assert calls == ["QuickReportDraft"]
    report = json.loads(db.get_order(oid)["report_json"])
    assert report["where_your_customers_are"] and report["similar_products"]
