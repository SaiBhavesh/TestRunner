# TestRunner

An agent-run company that tests websites. Solari does the browser work, Terac supplies real human testers,
Gemini writes the analysis, Whop collects the money. Full spec: [TESTRUNNER_SPEC.md](TESTRUNNER_SPEC.md).

**Status: Milestone 4 (Quick Check only) + deploy.** Solari (browser scan + axe-core accessibility + basic SEO),
Gemini, Exa and Whop are real (`MOCK_*=0` with keys set). Terac is still a mock. Only Quick Check ($5) is for sale:
Human Test is hidden (`HUMAN_TEST_ENABLED=0`) because Terac's confirmed price is $30 per tester.

Run only one server against a database at a time: each server has a worker, and two workers on the same
`testrunner.db` will both process the same order.

## Run locally (Windows PowerShell)

From the project folder:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

It is ready when you see `Uvicorn running on http://127.0.0.1:8000`. Open http://localhost:8000.
Copy `.env.example` to `.env` only when you need to change settings; the defaults are all-mock.

## M1 acceptance check

**Automated:** `.\.venv\Scripts\python.exe -m pytest`. Done means `16 passed`.

**By hand**, with the server running (open a second terminal in the project folder for the scripts):

1. Quick Check: on http://localhost:8000 enter `https://example.com`, choose Quick Check, click Buy.
   The order page shows a command. Run it: `.\.venv\Scripts\python.exe scripts\fake_webhook.py <ORDER_ID>`.
   Within a few seconds the order page shows **Open your report**.
2. Human Test: same, but choose Human Test and enter a task. After the fake webhook the status becomes
   *study launched / collecting*. Then run `.\.venv\Scripts\python.exe scripts\fake_responses.py <ORDER_ID>`,
   which submits 5 tester answers through the real `/t/{token}` form. The report then includes
   **What real testers found**.
3. http://localhost:8000/ops shows both orders and the decision log.

## M2 check (real Solari + Gemini)

With `MOCK_SOLARI=0`, `MOCK_GEMINI=0` and both keys set, place a Quick Check for a real site and run the fake
webhook as above. A real scan takes about 10–20 s; Gemini usually adds a few seconds, but up to ~45 s more when
Google is busy (the adapter waits and retries on 429/503). The report shows real screenshots, findings citing
`scan.*` sources, an axe-core accessibility table and an SEO checklist.

## Timeouts, fallback and the stuck-order check

- **Gemini:** every call has a deadline (`GEMINI_TIMEOUT_SEC`, default 180; Google's minimum is 10). On 503 or a
  timeout it switches to `GEMINI_FALLBACK_MODEL` (default `gemini-flash-latest`); on 429 it waits and retries the
  same model. If both models fail, the order fails with both reasons.
- **Solari:** 30s to start a session, 15s for its HTTP calls and for closing, 25s for page work (partial results),
  and `SOLARI_HARD_LIMIT_SEC` (default 90) for the whole scan.
- **Exa:** `EXA_TIMEOUT_SEC` (default 20) per search. If a search fails or times out, the report is delivered
  without that section and the reason goes in the decision log.
- **Stuck orders:** a separate thread fails any order with no progress for `STUCK_LIMIT_MIN` (default 15) in
  `paid`, `scanning`, `analyzing`, or `scanned` (Quick Check only), and writes the reason to the decision log.
  Human Tests waiting on testers or a Terac quote are excluded. A late worker result can't overwrite a failed order.
- **Console:** the server prints each stage (`[testrunner]` lines): transitions, scan start/end, every Gemini call
  with its duration, retries and fallbacks.

## Deploy (Render)

One paid web service with a persistent disk (the free plan erases the SQLite file whenever it sleeps).
`render.yaml` is a Blueprint for exactly this: Render dashboard → **New → Blueprint** → pick this repo. It asks
for the secret values (`sync: false` entries) once, on creation.

- **Runtime:** Python (version pinned by `.python-version`). **Build:** `pip install -r requirements.txt`.
  **Start:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Keep it to ONE instance and ONE uvicorn
  worker: each process runs its own background worker.
- **Disk:** mount at `/var/data`, then set `DB_PATH=/var/data/testrunner.db` and `SHOTS_DIR=/var/data/shots`.
- **Environment:** the keys from `.env.example`. `BASE_URL` can be left unset: it falls back to Render's
  `RENDER_EXTERNAL_URL`. While `MOCK_WHOP=1`, `WHOP_WEBHOOK_SECRET` must be your own random value, or the app
  refuses to start (the built-in mock secret is public in this repo). Use the same value in your local `.env`
  to run `fake_webhook.py --base-url https://<your-app>.onrender.com`.
- **Participant form check:** place a Human Test order on the public site, pay with `fake_webhook.py`, then open
  the tester-form link the order page shows (mock Terac only), or run `fake_responses.py --token=... --base-url ...`.

## Architecture

FastAPI web app + SQLite + an in-process worker thread (`app/worker.py`) that advances paid orders one stage
at a time (`app/agent/pipeline.py`). Every status change writes an `events` row; agent decisions are
`type='decision'` events shown on `/ops`. External services live in `app/adapters/`.

## Whop (Milestone 4, Quick Check)

Four settings: `WHOP_API_KEY` (Account API key), `WHOP_WEBHOOK_SECRET` (`ws_...`, from the webhook you create for
`https://<your-app>/webhooks/whop` with event `payment.succeeded`), `WHOP_BUSINESS_ID` (`biz_...`) and
`WHOP_PLAN_QUICK` (`plan_...`, a $5 one-time plan). With `MOCK_WHOP=0` the app refuses to start if one is missing.

Flow: Buy → `POST /checkout_configurations` (plan, `metadata.order_id`, `redirect_url` back to the order page) →
customer pays on Whop → `payment.succeeded` webhook (signature checked with `whop_sdk`'s `unwrap`) → the payment is
re-read from `GET /payments/{id}` and must be `paid`, for this order, and at least the price. If the webhook never
arrives, the order page itself asks Whop (`GET /payments?checkout_configuration_ids=...`, at most every 10 s).

## Integration notes (spec section 13)

Unverified items. The mocks deliberately make no claims about the real APIs:
- **Whop (verified from docs.whop.com, M4):** see the Whop section. Not yet checked against a live payment:
  whether `purchase_url` is a full URL (the app refuses a bare path rather than guessing a host), that `usd_total`
  is in dollars, and how the list endpoint wants array query parameters. Refunds API: not built.
- **Terac:** REST launch API, completion-URL format, and the query parameter Terac uses for participant ids
  (`TERAC_PARTICIPANT_PARAM`, default `participant_id` is a placeholder) are to verify in M6. Until then the
  participant form shows a completion code.
- **Solari (verified, M2):** `solari-browser` 0.2.3. `Solari(api_key).launch()` returns a remote Chromium driven
  through patchright (Playwright API); `browser.close()` also releases the session. Viewports via
  `browser.new_context(viewport=..., is_mobile=...)`; console errors via `page.on("console")`.
- **axe-core:** 4.10.2 vendored at `app/adapters/vendor/axe.min.js` (MPL-2.0; byte-identical to the npm release),
  injected with `page.evaluate`, so the site's Content-Security-Policy can't block it.
- **Gemini (verified, M2):** `google-genai` 2.33.0, `client.models.generate_content` with `response_schema` set
  to our Pydantic model. `gemini-2.5-flash` returns 404 "no longer available to new users"; default is now
  `gemini-3.8-flash`. Free-tier limits: not yet known.
- **Exa (verified, M3):** `exa-py` 2.26.0, `search()` only, each call with `timeout=EXA_TIMEOUT_SEC` (SDK
  default is 600s). Query = H1 + meta description + title, deduplicated (no Gemini call). Threads: "People
  discussing: <query>" limited to reddit.com / news.ycombinator.com / indiehackers.com, last 90 days, with a
  verbatim highlight snippet. Similar products: `category="company"`, own domain excluded and the site's own
  company profile filtered out by name. `find_similar()` is deprecated in this SDK and returned the site's own
  listings; `category="company"` returns no results when page text is requested. Links are sometimes the
  company's site, sometimes an exa.ai company profile.
