# TestRunner: Build Spec

> An agent-run company that tests websites. Solari does the computer work, Terac supplies real human testers, Gemini writes the analysis, Whop collects the money. No human runs operations.

Built at the Zero Human Company Hackathon NYC (Oct 24, 2026). Submissions lock at 6:45 PM ET. Build the smallest version that works end to end first. Every milestone must leave the app submittable.

## 0. Rules for Claude Code
1. Build milestones in order (Section 10). Do not start one until the previous passes its acceptance check.
2. Every external service (Solari, Terac, Exa, Gemini, Whop) sits behind an adapter with a mock mode, so the full flow runs locally before real keys work.
3. Where this spec says VERIFY, look up the real API (docs or MCP tool list). Never guess endpoints or payload fields.
4. Never commit secrets. The repo will be public.

## 1. Hackathon rules the product must satisfy
| Rule | How TestRunner satisfies it |
|---|---|
| Agent runs the company autonomously | Agent takes orders, inspects sites, launches studies, analyzes, delivers, logs every decision |
| Must use Terac to bring real humans | Human Test tier recruits testers via Terac. Self-improvement loop also uses Terac |
| Human input makes the project measurably better (before/after) | Section 8 |
| Terac studies target General Population | US adults 18+ |
| All payments through Whop only, no Stripe | Whop checkout + webhook |
| Real organic revenue | Real customers pay. Never pay anyone to buy |
| Submit: Whop Business ID, public repo, deployed link, 2-min video | Section 12 |

## 2. Product
### 2.1 Tiers
| Tier | Price (config) | What the customer gets | Terac? |
|---|---|---|---|
| Quick Check | $5 | Solari opens the site on desktop + mobile, screenshots, console errors, broken links, load time. Gemini writes ranked fixes. Delivered in minutes | No |
| Human Test | $20 | Quick Check + real people (via Terac) attempt a task and give feedback. Agent reports completion rate, avg rating, recurring problems, fixes | Yes |

Prices in config (PRICE_QUICK, PRICE_HUMAN). Human Test price must exceed Terac cost per order.

### 2.2 Customer flow
1. Customer on `/` enters URL, picks tier. Human Test also takes one task ("Can a user sign up?"). Email optional.
2. Server creates order, returns Whop checkout link.
3. Customer pays on Whop. Webhook marks order paid.
4. Agent runs the pipeline for that tier.
5. Customer sees status at `/order/{id}`, then report at `/report/{id}?t={token}`.

## 3. Tech stack
Single Python service: Python 3.11+, FastAPI, Uvicorn, SQLite (SQLModel or sqlite3), Jinja2 HTML + vanilla JS + plain CSS (no React), Gemini via `google-genai` (model `gemini-2.5-flash`, configurable), Solari Python SDK `solari-browser` (VERIFY), Exa via `exa-py`, Whop API + webhooks (VERIFY), Terac REST API (VERIFY), in-process worker thread polling the DB (no Redis), deploy on Render, single instance.

Optional: a teammate may build the Solari scanner as a separate Node service implementing the exact `/scan` contract in 5.1. Keep the contract identical.

## 4. Architecture
Customer → FastAPI web (+ Whop webhook, + participant form) → SQLite (orders, events, studies, responses, prompt_versions, spend) ← agent worker → adapters: Solari, Exa, Gemini, Terac, Whop (each with mock mode).

### 4.1 Order state machine
created → paid → scanning → scanned
- Quick Check: scanned → analyzing → report_ready
- Human Test: scanned → study_launched → collecting → analyzing → report_ready
- Any → failed (with reason)
- scanned with site unreachable → paused_unreachable (no Terac spend)

Rules: nothing runs until paid (webhook confirmed, never by browser). Never launch a Terac study if the scan says unreachable. Every transition writes an `events` row.

### 4.2 Repo layout
```
app/
  main.py, config.py, db.py, worker.py
  agent/ pipeline.py, decisions.py, report.py
  adapters/ solari.py, exa.py, gemini.py, terac.py, whop.py
  templates/ index.html, order.html, report.html, ops.html, participant.html
  static/ style.css, app.js
scripts/ fake_webhook.py, fake_responses.py
tests/
.env.example, requirements.txt, README.md, TESTRUNNER_SPEC.md
```

## 5. Adapter contracts
All adapters have real mode + mock mode (MOCK_SOLARI=1 etc.). Mocks return realistic fixed data.

### 5.1 Solari: scan(url) -> ScanResult (max 25s, partial results on timeout)
```json
{
  "url": "", "reachable": true, "final_url": "", "http_status": 200,
  "title": "", "headings": [], "text": "first ~2000 chars",
  "screenshots": {"desktop": "static/shots/{order}_desktop.png", "mobile": "static/shots/{order}_mobile.png"},
  "console_errors": [], "broken_links": [{"href": "", "status": 404}],
  "load_time_ms": 2300, "task_element_found": true, "notes": []
}
```
Desktop 1366x768, mobile 390x844. Check up to 20 same-domain links. For Human Test, try to locate elements matching task keywords. VERIFY Solari SDK calls. Always close sessions.

### 5.2 Exa
- find_threads(product_summary): up to 5 recent posts (Reddit, HN, forums, last 90 days) where people describe the problem the site solves
- find_competitors(product_summary): up to 3 "similar products" (never claim "funded")
Product summary = one short Gemini call over title + headings + text.

### 5.3 Gemini: generate_json(prompt, schema)
Structured JSON output, validate with Pydantic, retry once. Free tier: max ~3 calls per order, backoff on 429.

### 5.4 Terac
Known facts:
- MCP: https://terac.com/api/mcp. Tools: terac_list_opportunities, terac_request_feasibility, terac_get_feasibility_request, terac_launch_draft_opportunity, terac_get_submissions, terac_get_context, terac_pause_opportunity
- Flow: request feasibility → poll until PRICED (returns cpi_usd, may be priced by a person, can take time) → launch draft → poll submissions (pay only on approved)
- Typed REST API exists for server use (terac.com/docs/developers). Deployed app uses REST, not MCP
- Terac has NO answer form. Participants answer on our page (5.6)
- Payment auto-approval works when our page sends participants back to a Terac completion URL. VERIFY exact format in docs
- Account: org TestRunner, $100 credits. Existing feasibility request id: kktzf6t55d3i5ae3wt9s53a9

Interface:
```
request_quote(spec) -> request_id
get_quote(request_id) -> {status, cpi_usd}
launch(request_id, participant_url) -> study_id
get_study(study_id) -> {status, submissions_received, target}
```
Study spec: title "Test a website: {site_title}", audience US adults 18+, participants TERAC_PARTICIPANTS (default 5), unmoderated ~5 min, instructions link to {BASE_URL}/t/{study_token}.

Budget guard (required): TERAC_BUDGET_TOTAL (100) and TERAC_MAX_PER_ORDER. Track spend. If cpi_usd × participants exceeds limits, do not launch, log the decision (Section 7).

If Terac can only be driven via MCP, fallback: scripts/terac_operator.py or a Claude Code session reads pending studies from DB, launches, writes back. Document the working path in README.

### 5.5 Whop (VERIFY in Whop dev docs, webhooks, sandbox, Whop Claude Code plugin)
1. Two products/plans (Quick Check, Human Test); IDs in env
2. Checkout link per order with order_id as metadata (or redirect URL with order id + server-side API check)
3. Webhook: verify signature, mark paid, store payment id + amount, idempotent
4. Test in sandbox first
Never send paid content to the browser before payment, not even hidden or blurred.

### 5.6 Participant feedback form
- TestRunner hosts the answer form: GET/POST /t/{study_token}
- Each Human Test order gets a unique unguessable study_token
- Page shows: website link (new tab), the task, then fields: completed task (yes/no), ease rating 1-10, "What was confusing?", "Did anything look broken?", optional comments
- On submit: save to `responses` table linked to the order, then send participant to Terac's completion URL (VERIFY format; fall back to showing a completion code if docs say so)
- Anti-spam: all fields required, min 15 chars for written answers, one submission per Terac participant id if passed in URL
- Agent analyzes responses from OUR database, not from Terac
- Must be on the public deployed URL before a real study launches

## 6. Reports
Rules for every prompt: use only provided evidence, never invent human feedback, numbers, or quotes. Every finding cites its source ("scan.console_errors[0]", "3 of 5 testers"). Plain language. Specific ranked fixes. No claims of source-code patches.

Quick Check schema:
```json
{"summary": "", "score_0_100": 72,
 "findings": [{"title": "", "severity": "high|medium|low", "evidence": "", "source": "", "fix": ""}],
 "top_fixes": [], "where_your_customers_are": [{"title": "", "url": "", "why_relevant": ""}],
 "similar_products": [{"name": "", "url": ""}]}
```
Human Test adds:
```json
{"human_results": {"participants": 5, "task": "", "completion_rate": 0.6, "avg_rating": 6.4,
 "recurring_problems": [{"problem": "", "count": 3, "example_comment": ""}],
 "verified_by_browser": [{"problem": "", "confirmed": true, "how": ""}]}}
```
Compute rates, averages, counts in Python, not the LLM. verified_by_browser is stretch.

Prompt versioning: `prompt_versions` table (id, version, text, created_at, created_by human|agent, reason). Active version used for new reports.

## 7. Agent decisions (logged as events, type="decision")
| Situation | Decision |
|---|---|
| Site unreachable / task element not found | Pause before Terac spend, mark paused_unreachable, deliver Quick Check style report |
| Terac quote exceeds budget | Don't launch, deliver Quick Check report, log reason, flag for refund (VERIFY Whop refunds API) |
| No task given | Agent picks main task from page content, logs why |
| Responses stalled (TERAC_STALL_MIN) | Analyze if ≥3 responses, else wait and log |
| Report fails validation | Retry once, then simpler report, log |
Stretch CEO loop: every 30 min, recommend headline/price changes. Guardrails: ≥15 checkout starts before any price change, max 1 change/hour, floor $3. Only log recommendations unless Whop API supports price changes.

## 8. Self-improvement with Terac (required before/after)
1. Generate 3 sample reports on real public sites with prompt v1
2. Terac study "Rate this website report": usefulness 1-10, clarity 1-10, would pay $5 (yes/no), what was missing. Answers via our form
3. Agent writes prompt v2 from feedback (created_by=agent, reason)
4. Regenerate same 3 reports with v2, run same study
5. Ops screen shows v1 vs v2 scores and the agent's stated changes
Run study 1 as early as possible.

## 9. Pages
| Route | Purpose |
|---|---|
| GET / | Landing: headline, 3-step how it works, two tier cards, URL + task inputs, Buy |
| POST /api/orders | {url, tier, task?, email?} → validate, create order, return {order_id, checkout_url} |
| GET /order/{id} | Status page, refresh every 5s, no paid content |
| GET /api/orders/{id} | JSON status only |
| POST /webhooks/whop | Verify signature, mark paid, enqueue |
| GET /report/{id}?t= | Report: requires paid + report_ready + valid token |
| GET/POST /t/{study_token} | Participant feedback form (5.6) |
| GET /ops | Public read-only operations screen |

Ops screen per order: Whop payment (paid, last 6 chars of payment id, amount) → Solari inspection (screenshot thumb, errors) → Terac study (id, responses x/5) → Agent analysis (prompt version) → Report (delivered time). Plus live decision log (last 30 events), total revenue, Terac spend vs budget, Section 8 before/after panel. Never show emails, tokens, full payment ids.

Style: clean, light, mobile-friendly, one accent color, plain CSS.

## 10. Milestones (in order)
M1 Skeleton + mocks (45 min): FastAPI app, all tables (orders, events, studies, responses, prompt_versions, spend), worker loop, all adapters mocked, landing, status, report, participant form, ops pages. ✅ With mocks: create Quick Check order → scripts/fake_webhook.py → report_ready → report renders. Also: create Human Test order → fake webhook → study_launched → scripts/fake_responses.py submits 5 responses via /t/{token} → report_ready with human_results.
M2 Real Solari + real Gemini Quick Check (60 min). ✅ Real URL → real screenshots/errors → real report.
M3 Exa (20 min). ✅ Real threads + similar products in report.
M4 Whop (60 min). ✅ Sandbox purchase → webhook → paid → report unlocks. Then live.
M5 Deploy (30 min). ✅ Public URL, webhook pointed there, one live Quick Check purchase works.
M6 Terac Human Test (60 min). ✅ Paid Human Test launches a real study, responses arrive via our form, report includes human results.
M7 Self-improvement (45 min, start study early). ✅ Ops shows v1 vs v2 from real responses.
M8 Polish: ops visuals, decision log, stretch goals.

## 11. Config and security
.env.example: GEMINI_API_KEY, GEMINI_MODEL=gemini-2.5-flash, EXA_API_KEY, SOLARI_API_KEY, TERAC_API_KEY, WHOP_API_KEY, WHOP_WEBHOOK_SECRET, WHOP_BUSINESS_ID, WHOP_PRODUCT_QUICK, WHOP_PRODUCT_HUMAN, BASE_URL=http://localhost:8000, PRICE_QUICK=5, PRICE_HUMAN=20, TERAC_PARTICIPANTS=5, TERAC_BUDGET_TOTAL=100, TERAC_MAX_PER_ORDER=25, TERAC_STALL_MIN=20, MOCK_SOLARI=1, MOCK_EXA=1, MOCK_GEMINI=1, MOCK_TERAC=1, MOCK_WHOP=1

Security: .env gitignored, never log keys. SSRF guard on customer URLs: http/https only, reject localhost, private and link-local IPs (after DNS resolution), non-standard ports. Verify Whop webhook signatures, idempotent. Report and study tokens via secrets.token_urlsafe(24). Only test what's publicly visible: no security scanning, no login attempts.

## 12. Submission checklist
Whop Business ID, public GitHub repo with README (what it is, architecture, how each sponsor tool is used, run locally, before/after results), deployed link, ≥1 real paid order, Terac before/after on Ops, 2-min video (problem 15s → payment 15s → ops screen: Solari, Terac, agent decisions 60s → report 15s → before/after + revenue 15s), no secrets in git history.

## 13. Open questions (record answers in README "Integration notes")
1. Terac REST API for server-side launch; completion URL format; cpi_usd for request kktzf6t55d3i5ae3wt9s53a9; OK to use hackathon credits for paid orders
2. Whop checkout metadata, webhook events + signature, refunds API, price change API
3. Solari Python SDK calls for viewport, screenshots, console logs
4. Gemini free-tier limits
