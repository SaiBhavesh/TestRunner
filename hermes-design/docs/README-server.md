# TestRunner — Solari + Terac + Whop agent company MVP

**Status: runnable interactive demo, not production integration.** Uses Node.js 20+ built-in modules; zero npm dependencies.

## Start
```bash
node server.mjs
# visit http://localhost:3000
```

## Demo in 2 minutes
1. Create a sponsor campaign.
2. Open **Agent operations** and run the demo. Observe simulated Solari inspection, simulated Whop payment and simulated Terac study with auditable events.
3. Open **Tester simulation** and enter 2–3 demo feedback submissions.
4. Open **Report**, generate/download the explicitly labeled demo report.
5. Open **Architecture** to see the production responsibilities.

## Agent design
- `CampaignAgent`: owns campaign state machine, delegates tasks, logs events.
- `SolariAgent`: browser preflight and follow-up verification. Does **not** impersonate testers.
- `TeracAgent`: creates human study and collects actual human responses when connected.
- `FinanceAgent`: verifies Whop sponsor payments when connected.
- `AnalysisAgent`: compiles grounded feedback report with citations to responses.

These are **roles inside one orchestration process**, not five separate LLMs. In demo mode only the orchestration skeleton and deterministic analysis execute.

## Production readiness
See [ADAPTERS.md](ADAPTERS.md). Real MCP/API schemas and provider costs must be checked. No provider is connected, no payments or rewards are made, and no real people are recruited. `PROVIDER_MODE=live node server.mjs` deliberately fails closed on adapter calls.

## API
- `POST /api/campaigns` — create campaign
- `GET /api/state` — state and events
- `POST /api/campaigns/:id/run` — simulate provider workflow
- `POST /api/campaigns/:id/feedback` — **demo-only** human response
- `POST /api/campaigns/:id/report` — aggregate submitted feedback
- `GET /api/campaigns/:id/report` — report JSON

## Limits
Local JSON state file; single-process server. Not secure for public deployment without the hardening described in ADAPTERS.md. The activity log uses actual local demo events but does not represent actual external provider activity.
