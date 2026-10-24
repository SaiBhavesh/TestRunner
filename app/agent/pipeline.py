"""The order pipeline (spec 4.1). step() advances one order by one stage; the worker calls it repeatedly."""
import json

from app import config, db
from app.adapters import exa, solari, terac
from app.agent import report as rep
from app.agent.decisions import budget_check, log_decision, stall_decision
from app.log import log

ACTIVE_STATUSES = ("paid", "scanning", "scanned", "study_launched", "collecting", "analyzing")


def step(order_id: str) -> None:
    order = db.get_order(order_id)
    status = order["status"]
    if status in ("paid", "scanning"):
        _scan(order)
    elif status == "scanned":
        if order["tier"] == "quick":
            _analyze(order, human=False)
        else:
            _human_after_scan(order)
    elif status == "study_launched":
        db.set_status(order_id, "collecting", "Waiting for tester responses")
    elif status == "collecting":
        _collect(order)
    elif status == "analyzing":
        _analyze(order, human=order["tier"] == "human" and _study_launched(order_id))


def _scan(order: dict) -> None:
    if order["status"] == "paid":
        db.set_status(order["id"], "scanning", "Solari scan started")
    scan = solari.scan(order["url"], str(order["num"]), order["task"])
    db.set_status(order["id"], "scanned", f"Scan done: reachable={scan['reachable']}, "
                  f"{len(scan['console_errors'])} console errors, {len(scan['broken_links'])} broken links",
                  scan_json=json.dumps(scan))


def _product_summary(order: dict, scan: dict) -> dict:
    if order["product_summary"]:
        return json.loads(order["product_summary"])
    result = rep.generate_with_retry(rep.product_summary_prompt(scan), rep.ProductSummary).model_dump()
    db.update_order(order["id"], product_summary=json.dumps(result))
    return result


def _study_launched(order_id: str) -> bool:
    study = db.one("SELECT study_id FROM studies WHERE order_id = ?", (order_id,))
    return bool(study and study["study_id"])


def _human_after_scan(order: dict) -> None:
    oid = order["id"]
    scan = json.loads(order["scan_json"])

    # task_element_found is None when the scan couldn't check, which is not a reason to pause.
    if not scan["reachable"] or (order["task"] and scan["task_element_found"] is False):
        why = "site unreachable" if not scan["reachable"] else "task element not found on page"
        log_decision(oid, f"Paused before Terac spend: {why}. Delivering a Quick Check style report instead.")
        db.set_status(oid, "paused_unreachable", why)
        _analyze(db.get_order(oid), human=False, note=f"Human test not run: {why}.")
        return

    if not order["task"]:
        summary = _product_summary(order, scan)
        db.update_order(oid, task=summary["main_task"], task_source="agent")
        log_decision(oid, f"No task given; agent chose \"{summary['main_task']}\". Reason: {summary['why']}")
        order = db.get_order(oid)

    study = db.one("SELECT * FROM studies WHERE order_id = ?", (oid,))
    if study is None:
        spec = {
            "title": f"Test a website: {scan.get('title') or order['url']}",
            "audience": "US adults 18+",
            "participants": config.TERAC_PARTICIPANTS,
            "format": "unmoderated, about 5 minutes",
            "task": order["task"],
            "instructions_url": f"{config.BASE_URL}/t/{order['study_token']}",
        }
        request_id = terac.request_quote(spec)
        db.execute(
            "INSERT INTO studies (order_id, request_id, quote_status, target, created_at) VALUES (?,?,?,?,?)",
            (oid, request_id, "pending", config.TERAC_PARTICIPANTS, db.now()),
        )
        db.log_event(oid, "info", "Requested Terac quote")
        return

    if study["quote_status"] == "pending":
        quote = terac.get_quote(study["request_id"])
        if quote["status"] == "pending":
            return
        db.execute("UPDATE studies SET quote_status = ?, cpi_usd = ? WHERE id = ?",
                   (quote["status"], quote.get("cpi_usd"), study["id"]))
        if quote["status"] != "priced":
            log_decision(oid, f"Terac quote {quote['status']}; not launching. Delivering Quick Check report; "
                              "flagged for refund.")
            _analyze(order, human=False, note="Human test could not be priced by Terac.")
            return
        ok, reason = budget_check(quote["cpi_usd"], study["target"])
        log_decision(oid, ("Launching study. " if ok else "Not launching study. ") + reason
                     + ("" if ok else " Delivering Quick Check report; flagged for refund."))
        if not ok:
            _analyze(order, human=False, note="Human test skipped: over Terac budget.")
            return
        study_id = terac.launch(study["request_id"], f"{config.BASE_URL}/t/{order['study_token']}")
        db.execute("UPDATE studies SET study_id = ?, status = 'active', launched_at = ? WHERE id = ?",
                   (study_id, db.now(), study["id"]))
        db.execute("INSERT INTO spend (order_id, study_id, amount_usd, kind, created_at) VALUES (?,?,?,?,?)",
                   (oid, study_id, round(quote["cpi_usd"] * study["target"], 2), "terac_study", db.now()))
        db.set_status(oid, "study_launched", f"Terac study {study_id} launched")


def _collect(order: dict) -> None:
    oid = order["id"]
    study = db.one("SELECT * FROM studies WHERE order_id = ?", (oid,))
    received = db.one("SELECT COUNT(*) AS n FROM responses WHERE order_id = ?", (oid,))["n"]
    if received >= study["target"]:
        _analyze(order, human=True)
        return
    decision = stall_decision(study["launched_at"], received)
    if decision == "analyze":
        log_decision(oid, f"Responses stalled at {received}/{study['target']}; analyzing with what we have (>=3).")
        _analyze(order, human=True)
    elif decision == "wait" and study["status"] != "stalled_waiting":
        log_decision(oid, f"Responses stalled at {received}/{study['target']}; fewer than 3, so waiting.")
        db.execute("UPDATE studies SET status = 'stalled_waiting' WHERE id = ?", (study["id"],))


def _exa_section(oid: str, section: str, search, scan: dict) -> list[dict]:
    """Exa results are optional extras: on any Exa failure, deliver the report without this section and log why."""
    try:
        return search(exa.build_query(scan))  # query from H1 / meta description / title: no Gemini call
    except Exception as e:
        log.exception("Exa failed for section '%s'", section)
        log_decision(oid, f"Exa failed ({type(e).__name__}: {str(e)[:200]}); "
                          f"delivering the report without the '{section}' section.")
        return []


def _analyze(order: dict, human: bool, note: str | None = None) -> None:
    oid = order["id"]
    if order["status"] != "analyzing":
        db.set_status(oid, "analyzing", "Agent analysis started")
    order = db.get_order(oid)
    scan = json.loads(order["scan_json"])
    prompt = rep.active_prompt()

    threads = _exa_section(oid, "Where your customers are", lambda q: exa.find_threads(q), scan)
    competitors = _exa_section(oid, "Similar products",
                               lambda q: exa.find_competitors(q, scan.get("final_url") or order["url"]), scan)

    try:
        draft = rep.generate_with_retry(rep.quick_report_prompt(prompt["text"], scan), rep.QuickReportDraft).model_dump()
    except rep.ReportGenerationError as e:
        log_decision(oid, f"Report failed validation after retry; using simpler scan-only report. ({e})")
        draft = rep.simple_quick_report(scan)

    report = {**draft, "where_your_customers_are": threads, "similar_products": competitors,
              "notes": [note] if note else [], "mock_data": config.ANY_MOCK}

    if human:
        responses = db.all_rows("SELECT * FROM responses WHERE order_id = ? ORDER BY id", (oid,))
        try:
            problems = rep.generate_with_retry(
                rep.human_problems_prompt(prompt["text"], order["task"], responses), rep.HumanProblemsDraft)
        except rep.ReportGenerationError as e:
            log_decision(oid, f"Human problem grouping failed validation after retry; reporting numbers only. ({e})")
            problems = None
        report["human_results"] = rep.compute_human_results(order["task"], responses, problems)
        if problems is not None:
            report["top_fixes"] = problems.fixes + report["top_fixes"]

    db.set_status(oid, "report_ready", "Report delivered", report_json=json.dumps(report),
                  prompt_version=prompt["version"], delivered_at=db.now())
