"""Exa search (spec 5.2). Queries are built from the scan (H1, meta description, title); no Gemini call.

Verified against exa-py 2.26.0 on 2026-10-24:
- search(..., timeout=) bounds each call; the SDK default is 600s, so we always pass EXA_TIMEOUT_SEC.
- category="company" returns company profiles hosted on exa.ai (no company website field), and returns
  nothing when page text is requested, so competitors are fetched without contents.
- find_similar() is deprecated in this SDK version and returned the site's own listings, so it is not used.
"""
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

from app import config
from app.log import log

THREAD_DOMAINS = ["reddit.com", "news.ycombinator.com", "indiehackers.com"]
THREAD_DAYS = 90
MAX_THREADS = 5
MAX_COMPETITORS = 3
QUERY_MAX_CHARS = 300

_client = None


def build_query(scan: dict) -> str:
    """H1, meta description, then title, with repeats dropped (H1 and title often say the same thing)."""
    parts = []
    for text in (scan.get("h1"), scan.get("meta_description"), scan.get("title")):
        text = " ".join((text or "").split()).rstrip(".!?;: ")
        if text and not any(text.lower() in p.lower() or p.lower() in text.lower() for p in parts):
            parts.append(text)
    if not parts:
        raise ValueError("Page has no H1, meta description or title to build an Exa query from")
    return ". ".join(parts)[:QUERY_MAX_CHARS]


def find_threads(query: str) -> list[dict]:
    if config.MOCK_EXA:
        log.info("Exa (mock): find_threads")
        return [
            {"title": "[mock] Anyone else find onboarding flows confusing?", "url": "https://example.com/mock-thread-1",
             "published_date": "2026-10-01", "why_relevant": "[mock] People describing the problem this product solves."},
            {"title": "[mock] Looking for a simpler tool for this", "url": "https://example.com/mock-thread-2",
             "published_date": "2026-09-15", "why_relevant": "[mock] Users asking for alternatives."},
        ]
    since = (datetime.now(timezone.utc) - timedelta(days=THREAD_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    response = _search("find_threads", f"People discussing: {query}", num_results=MAX_THREADS,
                       include_domains=THREAD_DOMAINS, start_published_date=since,
                       contents={"highlights": {"max_characters": 300}})
    return [
        {"title": r.title or r.url, "url": r.url, "published_date": (r.published_date or "")[:10],
         # Verbatim snippet from the page (Exa highlight), not a generated summary.
         "why_relevant": " ".join((r.highlights or [""])[0].split())}
        for r in response.results[:MAX_THREADS]
    ]


def find_competitors(query: str, site_url: str) -> list[dict]:
    if config.MOCK_EXA:
        log.info("Exa (mock): find_competitors")
        return [
            {"name": "[mock] Similar Product A", "url": "https://example.com/mock-product-a"},
            {"name": "[mock] Similar Product B", "url": "https://example.com/mock-product-b"},
        ]
    host = (urlparse(site_url).hostname or "").removeprefix("www.")
    # Company profiles live on exa.ai, so excluding the site's domain doesn't remove the site's own profile.
    # Ask for extra results and drop any whose name contains the site's name (e.g. "plausible" for plausible.io).
    own_name = host.split(".")[0].lower() if host else ""
    response = _search("find_competitors", query, num_results=MAX_COMPETITORS + 3, category="company",
                       exclude_domains=[host] if host else None, contents=False)
    others = [r for r in response.results if not (own_name and own_name in (r.title or "").lower())]
    return [{"name": r.title or r.url, "url": r.url} for r in others[:MAX_COMPETITORS]]


def _get_client():
    from exa_py import Exa

    global _client
    if _client is None:
        if not config.EXA_API_KEY:
            raise RuntimeError("EXA_API_KEY is not set (or set MOCK_EXA=1)")
        _client = Exa(api_key=config.EXA_API_KEY)
    return _client


def _search(label: str, query: str, **kwargs):
    log.info("Exa: %s started (timeout %.0fs)", label, config.EXA_TIMEOUT_SEC)
    started = time.monotonic()
    response = _get_client().search(query, timeout=config.EXA_TIMEOUT_SEC, **kwargs)
    log.info("Exa: %s finished in %.1fs with %d results", label, time.monotonic() - started, len(response.results))
    return response
