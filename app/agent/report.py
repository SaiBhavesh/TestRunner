"""Report schemas, prompts, prompt versioning and the number-crunching for human results (spec 6)."""
import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app import db
from app.adapters import gemini

PROMPT_V1 = """You are TestRunner, writing a website quality report for the site owner.
Rules:
- Use ONLY the evidence provided below. Never invent human feedback, numbers, or quotes.
- Every finding must cite its source, e.g. "scan.console_errors[0]" or "3 of 5 testers".
- Plain language. Give specific fixes, ranked from most to least important.
- Never claim you patched or changed the site's source code."""


# ---- Structured outputs requested from Gemini ----

class ProductSummary(BaseModel):
    summary: str
    main_task: str
    why: str


class Finding(BaseModel):
    title: str
    severity: Literal["high", "medium", "low"]
    evidence: str
    source: str
    fix: str


class QuickReportDraft(BaseModel):
    summary: str
    score_0_100: int = Field(ge=0, le=100)
    findings: list[Finding]
    top_fixes: list[str]


class HumanProblem(BaseModel):
    problem: str
    response_numbers: list[int]
    quote_field: Literal["confusing", "broken", "comments"]


class HumanProblemsDraft(BaseModel):
    recurring_problems: list[HumanProblem]
    fixes: list[str]


class ReportGenerationError(Exception):
    pass


# ---- Prompt versions ----

def seed_prompt_v1() -> None:
    if db.one("SELECT id FROM prompt_versions WHERE version = 1") is None:
        db.execute(
            "INSERT INTO prompt_versions (version, text, created_at, created_by, reason, active) VALUES (?,?,?,?,?,1)",
            (1, PROMPT_V1, db.now(), "human", "Initial prompt"),
        )


def active_prompt() -> dict:
    row = db.one("SELECT * FROM prompt_versions WHERE active = 1 ORDER BY version DESC LIMIT 1")
    if row is None:
        raise RuntimeError("No active prompt version in prompt_versions")
    return row


# ---- Prompts ----

def _scan_evidence(scan: dict) -> str:
    keep = ["url", "reachable", "final_url", "http_status", "title", "headings", "text",
            "console_errors", "broken_links", "load_time_ms", "task_element_found", "accessibility", "seo", "notes"]
    return json.dumps({k: scan.get(k) for k in keep}, indent=2)


def product_summary_prompt(scan: dict) -> str:
    return (
        "Summarise in one or two sentences what this website offers, and name the single most important "
        "task a new visitor would try to do (e.g. 'Sign up for an account'). Explain why in one sentence.\n"
        f"Title: {scan.get('title')}\nHeadings: {scan.get('headings')}\nText: {scan.get('text')}"
    )


def quick_report_prompt(base: str, scan: dict) -> str:
    return (
        f"{base}\n\nWrite the report for this browser scan. Cite sources as scan.<field>[index], e.g. "
        "scan.console_errors[0], scan.accessibility.violations[1], scan.seo.checks[2]. Cover functional problems, "
        "accessibility violations and failed SEO checks, ranked by impact on visitors. score_0_100 is your "
        f"overall quality judgement from this evidence.\n\nscan = {_scan_evidence(scan)}"
    )


def human_problems_prompt(base: str, task: str, responses: list[dict]) -> str:
    lines = [
        f"R{i}: completed={'yes' if r['completed'] else 'no'}; rating={r['rating']}; "
        f"confusing={r['confusing']!r}; broken={r['broken']!r}; comments={r['comments'] or ''!r}"
        for i, r in enumerate(responses, start=1)
    ]
    return (
        f"{base}\n\nTesters were asked to: {task}\nGroup the problems that more than one tester mentioned. "
        "For each, list the response numbers (R1 = 1) that mention it, and give quote_field: which answer "
        "(confusing, broken or comments) of the FIRST listed response mentions it. Then suggest fixes.\n\n"
        + "\n".join(lines)
    )


# ---- Generation with retry ----

def generate_with_retry(prompt: str, schema: type[BaseModel]) -> BaseModel:
    """Spec 6/7: validate, retry once, then raise so the caller can fall back and log it."""
    last_error = None
    for _ in range(2):
        try:
            return gemini.generate_json(prompt, schema)
        except (ValidationError, ValueError) as e:
            last_error = e
    raise ReportGenerationError(f"{schema.__name__} failed validation twice: {last_error}")


def simple_quick_report(scan: dict) -> dict:
    """Fallback report built in Python straight from scan evidence (spec 7: 'simpler report')."""
    findings = []
    for i, err in enumerate(scan.get("console_errors") or []):
        findings.append({"title": "JavaScript console error", "severity": "high", "evidence": err,
                         "source": f"scan.console_errors[{i}]", "fix": "Investigate and fix this error."})
    for i, link in enumerate(scan.get("broken_links") or []):
        findings.append({"title": "Broken link", "severity": "medium",
                         "evidence": f"{link['href']} returned {link['status']}",
                         "source": f"scan.broken_links[{i}]", "fix": "Fix or remove this link."})
    for i, v in enumerate((scan.get("accessibility") or {}).get("violations") or []):
        findings.append({"title": f"Accessibility: {v['help']}",
                         "severity": "high" if v["impact"] in ("critical", "serious") else "low",
                         "evidence": f"{v['nodes']} element(s), e.g. {v['example_target']}",
                         "source": f"scan.accessibility.violations[{i}]", "fix": f"See {v['help_url']}"})
    for i, c in enumerate((scan.get("seo") or {}).get("checks") or []):
        if not c["ok"]:
            findings.append({"title": f"SEO: {c['check']}", "severity": "low", "evidence": c["detail"],
                             "source": f"scan.seo.checks[{i}]", "fix": f"Fix: {c['check'].lower()}."})
    return {
        "summary": "Automated summary unavailable; findings are listed directly from the browser scan.",
        "score_0_100": None,
        "findings": findings,
        "top_fixes": [f["fix"] + f" ({f['source']})" for f in findings[:3]],
    }


def compute_human_results(task: str, responses: list[dict], draft: HumanProblemsDraft | None) -> dict:
    """All numbers here come from our responses table, never from the LLM (spec 6)."""
    n = len(responses)
    problems = []
    if draft is not None:
        for p in draft.recurring_problems:
            idx = sorted({k for k in p.response_numbers if 1 <= k <= n})
            if not idx:
                continue
            problems.append({
                "problem": p.problem,
                "count": len(idx),
                "example_comment": responses[idx[0] - 1][p.quote_field] or "",
            })
        problems.sort(key=lambda p: p["count"], reverse=True)
    return {
        "participants": n,
        "task": task,
        "completion_rate": round(sum(1 for r in responses if r["completed"]) / n, 2) if n else 0.0,
        "avg_rating": round(sum(r["rating"] for r in responses) / n, 1) if n else 0.0,
        "recurring_problems": problems,
        "verified_by_browser": [],
    }
