# Python build templates (reference only)

These two files come from the separate Python/FastAPI build of TestRunner (real Solari scan, Gemini report, Exa research). They are **Jinja2 templates**, not standalone pages: they extend a `base.html` layout and are filled in by that server, so opening them directly in a browser shows raw `{% ... %}` tags.

| File | In the Python build |
|---|---|
| `participant.html` | Tester answer form served at `/t/{study_token}` (Terac has no answer form of its own) |
| `report-simple.html` | Customer report page served at `/report/{id}` |

In this repository, the static equivalents are `brief.html` (tester view) and `report.html` (report).

`report-simple.html` was truncated in the hackathon bundle. Only its closing tags were missing; they have been restored.
