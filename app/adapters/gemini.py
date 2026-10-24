"""Gemini structured JSON (spec 5.3). The caller passes a Pydantic model; output is validated against it.

Every call has a deadline (GEMINI_TIMEOUT_SEC). On 503 or timeout the main model is abandoned for
GEMINI_FALLBACK_MODEL. On 429 (rate limit) the same model is retried after a wait.
"""
import time

import httpx
from pydantic import BaseModel

from app import config
from app.log import log

RATE_LIMIT_WAITS_SEC = (15, 30)  # waits between 429 retries on the same model, then give up
UNAVAILABLE_CODES = (503, 504)  # 503 = Google "high demand"; 504 = our deadline expired (verified 2026-10-24)

_client = None


class GeminiUnavailable(RuntimeError):
    """The model was overloaded (503) or timed out. Triggers the fallback model."""


def generate_json(prompt: str, schema: type[BaseModel]) -> BaseModel:
    if config.MOCK_GEMINI:
        return schema.model_validate(_MOCK_OUTPUTS[schema.__name__])
    return schema.model_validate_json(_call_gemini(prompt, schema))


def _get_client():
    from google import genai
    from google.genai import types

    global _client
    if _client is None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set (or set MOCK_GEMINI=1)")
        _client = genai.Client(api_key=config.GEMINI_API_KEY,
                               http_options=types.HttpOptions(timeout=int(config.GEMINI_TIMEOUT_SEC * 1000)))
    return _client


def _call_gemini(prompt: str, schema: type[BaseModel]) -> str:
    models = [config.GEMINI_MODEL]
    if config.GEMINI_FALLBACK_MODEL and config.GEMINI_FALLBACK_MODEL != config.GEMINI_MODEL:
        models.append(config.GEMINI_FALLBACK_MODEL)
    failures = []
    for i, model in enumerate(models):
        try:
            return _call_model(_get_client(), model, prompt, schema)
        except GeminiUnavailable as e:
            failures.append(f"{model}: {e}")
            if i + 1 < len(models):
                log.warning("Gemini %s unavailable (%s); falling back to %s", model, e, models[i + 1])
    raise GeminiUnavailable("all Gemini models unavailable: " + "; ".join(failures))


def _call_model(client, model: str, prompt: str, schema: type[BaseModel]) -> str:
    from google.genai import errors, types

    gen_config = types.GenerateContentConfig(
        response_mime_type="application/json", response_schema=schema, temperature=0.2)
    for attempt, wait in enumerate((*RATE_LIMIT_WAITS_SEC, None), start=1):
        log.info("Gemini %s: %s call started (attempt %d, timeout %.0fs)",
                 model, schema.__name__, attempt, config.GEMINI_TIMEOUT_SEC)
        started = time.monotonic()
        try:
            response = client.models.generate_content(model=model, contents=prompt, config=gen_config)
        except errors.APIError as e:
            took = time.monotonic() - started
            if e.code in UNAVAILABLE_CODES:
                raise GeminiUnavailable(f"{e.code} {e.status} after {took:.0f}s")
            if e.code != 429 or wait is None:
                raise
            log.warning("Gemini %s: 429 rate limited after %.0fs; retrying in %ds", model, took, wait)
            time.sleep(wait)
            continue
        except httpx.TimeoutException as e:
            raise GeminiUnavailable(f"network timeout after {time.monotonic() - started:.0f}s ({type(e).__name__})")
        log.info("Gemini %s: %s call finished in %.1fs", model, schema.__name__, time.monotonic() - started)
        if not response.text:
            raise ValueError("Gemini returned an empty response")
        return response.text
    raise AssertionError("unreachable")


_MOCK_OUTPUTS = {
    "ProductSummary": {
        "summary": "[mock] A web app that helps small teams get set up quickly.",
        "main_task": "Sign up for a free account",
        "why": "[mock] 'Sign up free' is the most prominent call to action in the page headings.",
    },
    "QuickReportDraft": {
        "summary": "[mock] The site loads but has one JavaScript error and one broken link.",
        "score_0_100": 72,
        "findings": [
            {
                "title": "[mock] JavaScript error on page load",
                "severity": "high",
                "evidence": "TypeError: Cannot read properties of undefined (reading 'map')",
                "source": "scan.console_errors[0]",
                "fix": "[mock] Guard the list before calling .map() in app.js line 42.",
            },
            {
                "title": "[mock] Broken link to old pricing page",
                "severity": "medium",
                "evidence": "/old-pricing returns 404",
                "source": "scan.broken_links[0]",
                "fix": "[mock] Point the link at the current pricing page or add a redirect.",
            },
            {
                "title": "[mock] Slow first load",
                "severity": "low",
                "evidence": "Page took 2300 ms to load",
                "source": "scan.load_time_ms",
                "fix": "[mock] Compress the largest images and defer non-critical scripts.",
            },
        ],
        "top_fixes": [
            "[mock] Fix the JavaScript error in app.js",
            "[mock] Repair the /old-pricing link",
            "[mock] Reduce load time below 2 seconds",
        ],
    },
    "HumanProblemsDraft": {
        "recurring_problems": [
            {"problem": "[mock] Sign-up button was hard to find", "response_numbers": [1, 2, 3],
             "quote_field": "confusing"},
            {"problem": "[mock] Confirmation email step was unclear", "response_numbers": [2, 4],
             "quote_field": "broken"},
        ],
        "fixes": ["[mock] Move the sign-up button above the fold", "[mock] Explain the email step on screen"],
    },
}
