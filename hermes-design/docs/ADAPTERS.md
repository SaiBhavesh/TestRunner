# Real integration contracts (not implemented)

The MVP is **DEMO ONLY**. It intentionally does not claim to have made Solari MCP calls, launched Terac studies, charged Whop, or paid humans. `PROVIDER_MODE=live` fails closed until adapters are implemented. Never switch to live by replacing demo return values with fictional successes.

## SolariAgent
Input: `{campaignId, website, task}`. Output: `{sessionId, website, screenshotUrl, checks:[{name,result,evidence}], mode:'live'}`. In a trusted backend/agent runtime, invoke the **actual installed Solari MCP tool names** to create browser session, navigate, inspect page, screenshot and collect errors. Verify the MCP schema before coding. Run against public pages initially. Prevent SSRF/private-network URLs and enforce timeouts in production. Do not put MCP credentials in frontend code.

## TeracAgent
Input: `{campaignId, website, task, participants, maxBudget}`. Output: `{studyId, status, estimatedCost, responseIds, mode:'live'}`. Use the installed Terac MCP/API to create a study, poll for completion and fetch real human feedback. Confirm cost, consent, participant eligibility, turnaround and reward handling before charging a sponsor. Do not invent results or use synthetic responses as real testers.

## FinanceAgent / Whop
Use Whop checkout for sponsor purchase. Verify signed webhook server-side before setting `paid=true`; store transaction ID and amount. Never trust frontend redirect/query string. Handle refunds, payment failures, duplicate webhook events and provider fees. In the demo `payment_check` is explicitly **simulated**.

## Production orchestration
Recommended order: `created → Solari preflight → quote Terac costs → sponsor payment verified → Terac study launched → poll human responses → Solari follow-up verification → report generated → delivered`. The demo intentionally simulates payment and study creation; it does not claim to satisfy this full production flow.

## Security checklist
- Add authentication, authorization, rate limiting, signed webhook verification, safe URL resolution, private IP/DNS rebinding protection, and provider timeouts before exposing publicly.
- Restrict browser automation to authorized scopes and disallow purchases/account changes without permission.
- Do not expose personal tester data; use only necessary aggregated data.
- Store event IDs, evidence references and actual provider statuses.
