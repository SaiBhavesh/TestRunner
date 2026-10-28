# TestRunner demo pages

Six single-file pages (HTML, CSS and JS inline) plus one shared data file.

| File | For | Whop link |
|---|---|---|
| index.html | Prospects | Links to order.html |
| order.html | Sponsors | Pay button opens the Whop checkout link (whop.checkoutUrl in status.json) |
| report.html | Sponsors | Shows paid or unpaid only |
| brief.html | Testers (preview) | None |
| console.html | Operator | Shows payments and the approval gate |
| proof.html | Anyone | Shows what each page and agent does, and the recorded MCP calls |

Run: `python3 -m http.server 8000` from the repository root (on Windows: `python -m http.server 8000`), then open http://localhost:8000/index.html
(status.json must be served over http, not opened as a file).

Pages never call Terac, Solari or Whop. An agent writes status.json; the pages display it.
Orders made in the browser are kept in localStorage and flagged "simulated" when advanced in the console.
