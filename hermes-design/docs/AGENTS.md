# Agent execution contract
Goal: deliver a paid, real-human website testing campaign with Solari preflight and Terac fulfillment, not synthetic engagement.

1. Verify installed Solari, Terac, Whop schemas and credentials; never guess tool names.
2. Implement provider adapters and integration tests behind `adapters` in server.mjs.
3. Require genuine verified Whop payment and sufficient Terac budget before launching a study.
4. Solari verifies website/task feasibility; capture evidence and avoid private URLs.
5. Terac recruits genuine humans; collect real response IDs, never generate or fabricate participant responses.
6. Analyze only collected responses; report evidence and limitations.
7. Maintain auditable activity log; demo and live results must be unmistakably labeled.
8. Test end-to-end with a real authorized test campaign, verify payment, response IDs, and report delivery.
9. Only mark K5-ready after repeatable tests pass and store a git tag with evidence.
