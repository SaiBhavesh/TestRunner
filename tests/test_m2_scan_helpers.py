"""Milestone 2: pure helpers used by the real Solari scan (no network)."""
from app.adapters import solari
from app.agent import report


def test_task_keywords_drop_filler_words():
    assert solari.task_keywords("Can a user sign up?") == ["sign"]
    assert solari.task_keywords("Can someone find the pricing page?") == ["pricing", "page"]
    assert solari.task_keywords("Go") == []


def test_same_domain_links_filters_and_caps():
    hrefs = ["/about", "https://example.com/about#team", "https://other.com/x", "mailto:a@b.c",
             "https://example.com/", "https://example.com/blog"] + [f"/p{i}" for i in range(40)]
    links = solari.same_domain_links("https://example.com/", hrefs)
    assert links[:2] == ["https://example.com/about", "https://example.com/blog"]
    assert all(l.startswith("https://example.com/") for l in links)
    assert len(links) == solari.MAX_LINKS


def test_seo_checks():
    raw = {"title": "Acme: invoices in a minute", "description": None, "h1_count": 2, "lang": "en",
           "viewport": True, "canonical": None, "robots": "noindex", "images_total": 4,
           "images_missing_alt": 1, "og_title": False}
    checks = {c["check"]: c["ok"] for c in solari.build_seo_checks(raw)}
    assert checks["Page title"] is True
    assert checks["Meta description"] is False
    assert checks["Exactly one H1"] is False
    assert checks["Page language set"] is True
    assert checks["Not blocked from search"] is False
    assert checks["Images have alt text"] is False


def test_simple_report_includes_accessibility_and_seo():
    scan = {"console_errors": [], "broken_links": [],
            "accessibility": {"violations": [{"id": "image-alt", "impact": "critical", "help": "Images need alt",
                                              "help_url": "https://x", "nodes": 2, "example_target": "img"}]},
            "seo": {"checks": [{"check": "Canonical link", "ok": False, "detail": "missing"},
                               {"check": "Page title", "ok": True, "detail": "20 characters"}]}}
    sources = [f["source"] for f in report.simple_quick_report(scan)["findings"]]
    assert sources == ["scan.accessibility.violations[0]", "scan.seo.checks[0]"]
