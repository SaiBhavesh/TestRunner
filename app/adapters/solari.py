"""Solari browser scan (spec 5.1), plus axe-core accessibility and basic SEO checks.

Real mode drives Solari's remote Chromium through the solari-browser SDK (Playwright API via patchright).
Mock mode returns fixed data and writes placeholder screenshots.
"""
import asyncio
import re
import struct
import time
import zlib
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from app import config
from app.log import log
from app.urlguard import validate_public_url

AXE_JS = Path(__file__).resolve().parent / "vendor" / "axe.min.js"  # axe-core 4.10.2, MPL-2.0

SCAN_LIMIT_SEC = 25  # page work; partial results after this (spec 5.1)
LAUNCH_TIMEOUT_SEC = 30
CLOSE_TIMEOUT_SEC = 15
SOLARI_HTTP_TIMEOUT_MS = 15_000  # Solari SDK's own HTTP calls (session create/release); SDK default is 90s
NAV_TIMEOUT_MS = 15_000
MAX_LINKS = 20
DESKTOP = {"viewport": {"width": 1366, "height": 768}}
MOBILE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}

class SolariTimeout(RuntimeError):
    """A Solari step ran past its time limit. Separate from TimeoutError, which asyncio also raises."""


TASK_STOPWORDS = {"can", "could", "user", "users", "does", "their", "they", "into", "from", "this", "that",
                  "what", "your", "able", "will", "with", "should", "someone", "people", "visitor", "find"}


def scan(url: str, shot_key: str, task: str | None = None) -> dict:
    """shot_key names the screenshot files. Use the order number, never the order id (ops shows shots publicly)."""
    if config.MOCK_SOLARI:
        log.info("Solari (mock): scan of %s", url)
        return _mock_scan(url, shot_key, task)
    log.info("Solari: scan of %s started (hard limit %.0fs)", url, config.SOLARI_HARD_LIMIT_SEC)
    started = time.monotonic()
    try:
        result = asyncio.run(asyncio.wait_for(_real_scan(url, shot_key, task), config.SOLARI_HARD_LIMIT_SEC))
    except asyncio.TimeoutError:
        raise SolariTimeout(f"Solari scan exceeded the {config.SOLARI_HARD_LIMIT_SEC:.0f}s hard limit")
    log.info("Solari: scan finished in %.1fs (reachable=%s)", time.monotonic() - started, result["reachable"])
    return result


# ---------------------------------------------------------------- real scan

def _empty_result(url: str) -> dict:
    return {
        "url": url, "reachable": False, "final_url": url, "http_status": None,
        "title": "", "h1": "", "meta_description": "", "headings": [], "text": "",
        "screenshots": {"desktop": None, "mobile": None},
        "console_errors": [], "broken_links": [], "load_time_ms": None,
        "task_element_found": None,
        "accessibility": {"violations": []},
        "seo": {"checks": []},
        "notes": [],
    }


async def _real_scan(url: str, shot_key: str, task: str | None) -> dict:
    from solari_browser import Solari

    result = _empty_result(url)
    started = time.monotonic()
    async with Solari(api_key=config.SOLARI_API_KEY, timeout_ms=SOLARI_HTTP_TIMEOUT_MS) as solari:
        try:
            browser = await asyncio.wait_for(solari.launch(), LAUNCH_TIMEOUT_SEC)
        except asyncio.TimeoutError:
            raise SolariTimeout(f"Solari browser session did not start within {LAUNCH_TIMEOUT_SEC}s")
        log.info("Solari: browser session started in %.1fs", time.monotonic() - started)
        try:
            await asyncio.wait_for(_run_passes(browser, result, shot_key, task),
                                   timeout=SCAN_LIMIT_SEC - (time.monotonic() - started))
        except asyncio.TimeoutError:
            result["notes"].append(f"Scan hit the {SCAN_LIMIT_SEC}s limit; results are partial.")
            log.warning("Solari: scan hit the %ss limit; keeping partial results", SCAN_LIMIT_SEC)
        finally:
            try:
                await asyncio.wait_for(browser.close(), CLOSE_TIMEOUT_SEC)  # also releases the Solari session
            except asyncio.TimeoutError:
                # The scan itself succeeded; the session auto-releases at its deadline, so record it, don't fail.
                note = f"Solari session release timed out after {CLOSE_TIMEOUT_SEC}s; it auto-expires at {browser.expires_at}."
                result["notes"].append(note)
                log.warning("Solari: %s", note)
    return result


async def _run_passes(browser, result: dict, shot_key: str, task: str | None) -> None:
    errors: list[str] = []

    # Desktop pass: navigation, content, screenshot, SEO, accessibility, task element, link list.
    ctx = await browser.new_context(**DESKTOP)
    page = await ctx.new_page()
    _listen_for_errors(page, errors, "desktop")
    try:
        response = await page.goto(result["url"], wait_until="load", timeout=NAV_TIMEOUT_MS)
    except Exception as e:
        result["notes"].append(f"Could not load the page: {type(e).__name__}: {str(e)[:200]}")
        return
    result["http_status"] = response.status if response else None
    result["final_url"] = page.url
    result["reachable"] = response is None or response.status < 400
    if not result["reachable"]:
        result["notes"].append(f"Page returned HTTP {response.status}.")

    content = await page.evaluate(_CONTENT_JS)
    result.update(title=content["title"], headings=content["headings"], text=content["text"],
                  load_time_ms=content["load_ms"])
    result["screenshots"]["desktop"] = await _screenshot(page, shot_key, "desktop")
    result["console_errors"] = _dedupe(errors)

    seo_raw = await page.evaluate(_SEO_JS)
    result["seo"]["checks"] = build_seo_checks(seo_raw)
    result["h1"] = seo_raw["h1_text"]
    result["meta_description"] = seo_raw["description"] or ""

    await page.evaluate(AXE_JS.read_text(encoding="utf-8"))
    result["accessibility"]["violations"] = await page.evaluate(_AXE_RUN_JS)

    keywords = task_keywords(task) if task else []
    if task and keywords:
        matches = await page.evaluate(_TASK_JS, keywords)
        result["task_element_found"] = bool(matches)
        result["notes"].append(f"Task keywords {keywords}: matched elements {matches[:3]}" if matches
                               else f"No buttons or links matched task keywords {keywords}.")
    elif task:
        result["notes"].append("Task had no searchable keywords; did not check for a matching element.")

    hrefs = await page.evaluate("() => Array.from(document.querySelectorAll('a[href]'), a => a.href)")
    links = same_domain_links(result["final_url"], hrefs)
    await ctx.close()

    # Mobile pass: screenshot plus any mobile-only console errors.
    ctx = await browser.new_context(**MOBILE)
    page = await ctx.new_page()
    _listen_for_errors(page, errors, "mobile")
    try:
        await page.goto(result["final_url"], wait_until="load", timeout=NAV_TIMEOUT_MS)
        result["screenshots"]["mobile"] = await _screenshot(page, shot_key, "mobile")
    except Exception as e:
        result["notes"].append(f"Mobile pass failed: {type(e).__name__}: {str(e)[:200]}")
    result["console_errors"] = _dedupe(errors)
    await ctx.close()

    # Robots/sitemap and broken links are checked from our server, through the SSRF guard.
    origin = "{0.scheme}://{0.netloc}".format(urlparse(result["final_url"]))
    async with httpx.AsyncClient(timeout=5, follow_redirects=False,
                                 headers={"User-Agent": "TestRunner link checker"}) as http:
        for name in ("robots.txt", "sitemap.xml"):
            status = await _check_url(http, f"{origin}/{name}")
            result["seo"]["checks"].append({"check": f"/{name} exists", "ok": status == 200,
                                            "detail": f"HTTP {status}" if status else "not reachable"})
        statuses = await asyncio.gather(*(_check_url(http, link) for link in links))
    result["broken_links"] = [{"href": link, "status": s} for link, s in zip(links, statuses)
                              if s is None or s >= 400]
    result["notes"].append(f"Checked {len(links)} same-domain links (redirects count as working).")


def _listen_for_errors(page, errors: list[str], label: str) -> None:
    page.on("console", lambda m: errors.append(f"[{label}] {m.text}") if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(f"[{label}] Uncaught: {e}"))


async def _screenshot(page, shot_key: str, kind: str) -> str:
    config.SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(config.SHOTS_DIR / f"{shot_key}_{kind}.png"), timeout=8_000)
    return f"static/shots/{shot_key}_{kind}.png"


async def _check_url(http: httpx.AsyncClient, url: str) -> int | None:
    """HTTP status for url, or None if it can't be reached. Refuses non-public addresses."""
    try:
        validate_public_url(url)
    except ValueError:
        return None
    try:
        res = await http.head(url)
        if res.status_code in (405, 501):  # server doesn't support HEAD
            res = await http.get(url)
        return res.status_code
    except httpx.HTTPError:
        return None


def _dedupe(items: list[str], limit: int = 20) -> list[str]:
    seen, out = set(), []
    for item in items:
        key = item.split("] ", 1)[-1]
        if key not in seen:
            seen.add(key)
            out.append(item[:300])
    return out[:limit]


# ---------------------------------------------------------------- pure helpers (unit-tested)

def task_keywords(task: str) -> list[str]:
    words = re.findall(r"[a-z]+", task.lower())
    return [w for w in dict.fromkeys(words) if len(w) >= 4 and w not in TASK_STOPWORDS][:5]


def same_domain_links(page_url: str, hrefs: list[str]) -> list[str]:
    host = urlparse(page_url).hostname
    current = urldefrag(page_url)[0]
    out = []
    for href in hrefs:
        link = urldefrag(urljoin(page_url, href))[0]
        parsed = urlparse(link)
        if parsed.scheme in ("http", "https") and parsed.hostname == host and link != current and link not in out:
            out.append(link)
        if len(out) >= MAX_LINKS:
            break
    return out


def build_seo_checks(raw: dict) -> list[dict]:
    title_len = len(raw["title"])
    desc_len = len(raw["description"] or "")
    return [
        {"check": "Page title", "ok": 10 <= title_len <= 60,
         "detail": f"{title_len} characters" + ("" if title_len else " (missing)") + "; aim for 10–60"},
        {"check": "Meta description", "ok": 50 <= desc_len <= 160,
         "detail": f"{desc_len} characters" + ("" if desc_len else " (missing)") + "; aim for 50–160"},
        {"check": "Exactly one H1", "ok": raw["h1_count"] == 1, "detail": f"{raw['h1_count']} H1 headings"},
        {"check": "Page language set", "ok": bool(raw["lang"]), "detail": raw["lang"] or "no lang attribute on <html>"},
        {"check": "Mobile viewport tag", "ok": raw["viewport"], "detail": "present" if raw["viewport"] else "missing"},
        {"check": "Canonical link", "ok": bool(raw["canonical"]), "detail": raw["canonical"] or "missing"},
        {"check": "Not blocked from search", "ok": "noindex" not in (raw["robots"] or "").lower(),
         "detail": f"robots meta: {raw['robots']}" if raw["robots"] else "no robots meta"},
        {"check": "Images have alt text", "ok": raw["images_missing_alt"] == 0,
         "detail": f"{raw['images_missing_alt']} of {raw['images_total']} images missing alt"},
        {"check": "Social preview (og:title)", "ok": raw["og_title"], "detail": "present" if raw["og_title"] else "missing"},
    ]


# ---------------------------------------------------------------- in-page scripts

_CONTENT_JS = """() => {
  const nav = performance.getEntriesByType('navigation')[0];
  return {
    title: document.title || '',
    headings: Array.from(document.querySelectorAll('h1, h2, h3'), h => h.innerText.trim()).filter(Boolean).slice(0, 20),
    text: (document.body ? document.body.innerText : '').slice(0, 2000),
    load_ms: nav && nav.loadEventEnd > 0 ? Math.round(nav.loadEventEnd - nav.startTime) : null,
  };
}"""

_SEO_JS = """() => {
  const meta = name => { const m = document.querySelector(`meta[name="${name}"]`); return m ? m.content : null; };
  const imgs = Array.from(document.images);
  return {
    title: document.title || '',
    description: meta('description'),
    h1_count: document.querySelectorAll('h1').length,
    h1_text: ((document.querySelector('h1') || {}).innerText || '').trim().slice(0, 200),
    lang: document.documentElement.getAttribute('lang'),
    viewport: !!document.querySelector('meta[name="viewport"]'),
    canonical: (document.querySelector('link[rel="canonical"]') || {}).href || null,
    robots: meta('robots'),
    images_total: imgs.length,
    images_missing_alt: imgs.filter(i => !i.hasAttribute('alt')).length,
    og_title: !!document.querySelector('meta[property="og:title"]'),
  };
}"""

_AXE_RUN_JS = """async () => {
  const r = await axe.run(document, { resultTypes: ['violations'] });
  const rank = { critical: 0, serious: 1, moderate: 2, minor: 3 };
  return r.violations
    .sort((a, b) => (rank[a.impact] ?? 4) - (rank[b.impact] ?? 4))
    .slice(0, 15)
    .map(v => ({ id: v.id, impact: v.impact, help: v.help, help_url: v.helpUrl, nodes: v.nodes.length,
                 example_target: v.nodes.length ? String(v.nodes[0].target[0]).slice(0, 120) : '' }));
}"""

_TASK_JS = """(keywords) => {
  const els = document.querySelectorAll('a, button, [role="button"], input[type="submit"], input[type="button"]');
  const hits = [];
  for (const el of els) {
    const label = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().toLowerCase();
    if (label && keywords.some(k => label.includes(k))) hits.push(label.slice(0, 60));
  }
  return hits.slice(0, 10);
}"""


# ---------------------------------------------------------------- mock

def _mock_scan(url: str, shot_key: str, task: str | None) -> dict:
    host = urlparse(url).hostname or url
    config.SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    _write_png(config.SHOTS_DIR / f"{shot_key}_desktop.png", 683, 384, (226, 232, 240))
    _write_png(config.SHOTS_DIR / f"{shot_key}_mobile.png", 195, 422, (203, 213, 225))
    return {
        "url": url,
        "reachable": True,
        "final_url": url,
        "http_status": 200,
        "title": f"[mock] {host}",
        "h1": "[mock] Ship faster with less hassle",
        "meta_description": "[mock] A tool that helps small teams ship faster.",
        "headings": ["[mock] Ship faster with less hassle", "[mock] Pricing", "[mock] Sign up free"],
        "text": "[mock] This is placeholder page text from the mock Solari adapter. "
        "A real scan reads the first ~2000 characters of the page.",
        "screenshots": {
            "desktop": f"static/shots/{shot_key}_desktop.png",
            "mobile": f"static/shots/{shot_key}_mobile.png",
        },
        "console_errors": ["[mock] TypeError: Cannot read properties of undefined (reading 'map') at app.js:42"],
        "broken_links": [{"href": url.rstrip("/") + "/old-pricing", "status": 404}],
        "load_time_ms": 2300,
        "task_element_found": True,
        "accessibility": {"violations": [
            {"id": "color-contrast", "impact": "serious", "help": "[mock] Elements must meet minimum color contrast",
             "help_url": "https://dequeuniversity.com/rules/axe/4.10/color-contrast", "nodes": 3,
             "example_target": ".hero p"}]},
        "seo": {"checks": [
            {"check": "Page title", "ok": True, "detail": "[mock] 32 characters; aim for 10–60"},
            {"check": "Meta description", "ok": False, "detail": "[mock] 0 characters (missing); aim for 50–160"}]},
        "notes": ["Mock scan: no real browser was opened."],
    }


def _write_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Write a solid-colour PNG using only the standard library."""
    row = b"\x00" + bytes(rgb) * width
    raw = row * height

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)
