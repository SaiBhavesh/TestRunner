"""FastAPI web app: customer pages, Whop webhook, participant form, ops screen (spec 9)."""
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app import config, db, worker
from app.adapters import whop
from app.urlguard import validate_public_url

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.globals.update(config=config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    stop = worker.start() if config.WORKER_ENABLED else None
    yield
    if stop:
        stop.set()


if config.MOCK_WHOP and not config.IS_LOCAL and not config.WHOP_WEBHOOK_SECRET:
    # The mock fallback secret is in this public repo; anyone could fake payments on a public deploy.
    raise RuntimeError("MOCK_WHOP=1 on a public URL needs WHOP_WEBHOOK_SECRET set to your own random value")

app = FastAPI(title="TestRunner", lifespan=lifespan)
config.SHOTS_DIR.mkdir(parents=True, exist_ok=True)
# Mounted before /static so screenshots can live outside the code folder (e.g. on a Render disk).
app.mount("/static/shots", StaticFiles(directory=config.SHOTS_DIR), name="shots")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


# ---- Customer pages ----

class OrderIn(BaseModel):
    url: str
    tier: str
    task: str | None = None
    email: str | None = None


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.post("/api/orders")
def create_order(body: OrderIn):
    if body.tier not in config.PRICES:
        raise HTTPException(400, "tier must be 'quick' or 'human'")
    try:
        url = validate_public_url(body.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    task = (body.task or "").strip() or None
    if task and len(task) > 200:
        raise HTTPException(400, "Task must be 200 characters or fewer")
    if body.tier == "quick":
        task = None
    email = (body.email or "").strip() or None
    if email and ("@" not in email or len(email) > 200):
        raise HTTPException(400, "Email looks invalid")

    order_id = secrets.token_hex(12)  # hex, so it never starts with "-" (scripts take it as an argument)
    db.execute(
        "INSERT INTO orders (id, url, tier, task, task_source, email, status, report_token, study_token, "
        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (order_id, url, body.tier, task, "customer" if task else None, email, "created",
         secrets.token_urlsafe(24), secrets.token_urlsafe(24) if body.tier == "human" else None,
         db.now(), db.now()),
    )
    db.log_event(order_id, "transition", f"Order created ({config.TIER_NAMES[body.tier]})", None, "created")
    return {"order_id": order_id, "checkout_url": whop.checkout_url(order_id, body.tier)}


def _order_or_404(order_id: str) -> dict:
    order = db.get_order(order_id)
    if order is None:
        raise HTTPException(404, "Order not found")
    return order


@app.get("/order/{order_id}", response_class=HTMLResponse)
def order_page(request: Request, order_id: str):
    order = _order_or_404(order_id)
    return templates.TemplateResponse(request, "order.html", {"order": order})


@app.get("/api/orders/{order_id}")
def order_status(order_id: str):
    order = _order_or_404(order_id)
    return {"order_id": order["id"], "tier": order["tier"], "status": order["status"]}


@app.get("/report/{order_id}", response_class=HTMLResponse)
def report_page(request: Request, order_id: str, t: str = ""):
    order = db.get_order(order_id)
    if (order is None or not order["paid_at"] or order["status"] != "report_ready"
            or not hmac.compare_digest(t, order["report_token"])):
        raise HTTPException(404, "Report not found")
    report = json.loads(order["report_json"])
    return templates.TemplateResponse(request, "report.html", {
        "order": order, "report": report, "scan": json.loads(order["scan_json"])})


# ---- Whop webhook ----

@app.post("/webhooks/whop")
async def whop_webhook(request: Request):
    body = await request.body()
    try:
        event = whop.parse_webhook(body, request.headers)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, f"Invalid webhook: {e}")
    order = db.get_order(event["order_id"])
    if order is None:
        raise HTTPException(404, "Unknown order")
    if order["status"] != "created" or order["payment_id"]:
        return {"ok": True, "duplicate": True}
    price = config.PRICES[order["tier"]]
    if event["amount_usd"] < price:
        db.log_event(order["id"], "decision",
                     f"Payment of ${event['amount_usd']:.2f} is below the ${price:.2f} price; not marking paid.")
        return JSONResponse({"ok": False, "error": "amount below price"}, status_code=200)
    db.set_status(order["id"], "paid", f"Whop payment received (${event['amount_usd']:.2f})",
                  payment_id=event["payment_id"], amount_usd=event["amount_usd"], paid_at=db.now())
    return {"ok": True}


# ---- Participant feedback form (spec 5.6) ----

OPEN_FOR_RESPONSES = ("study_launched", "collecting")


def _study_order_or_404(study_token: str) -> dict:
    order = db.one("SELECT * FROM orders WHERE study_token = ?", (study_token,))
    if order is None:
        raise HTTPException(404, "Study not found")
    return order


@app.get("/t/{study_token}", response_class=HTMLResponse)
def participant_form(request: Request, study_token: str):
    order = _study_order_or_404(study_token)
    pid = request.query_params.get(config.TERAC_PARTICIPANT_PARAM, "")
    return templates.TemplateResponse(request, "participant.html", {
        "order": order, "open": order["status"] in OPEN_FOR_RESPONSES,
        "participant_id": pid, "errors": [], "form": {}, "completion_code": None})


@app.post("/t/{study_token}", response_class=HTMLResponse)
async def participant_submit(request: Request, study_token: str):
    order = _study_order_or_404(study_token)
    form = dict(await request.form())
    pid = (request.query_params.get(config.TERAC_PARTICIPANT_PARAM) or form.get("participant_id") or "").strip()
    ctx = {"order": order, "open": order["status"] in OPEN_FOR_RESPONSES, "participant_id": pid,
           "form": form, "completion_code": None}
    if not ctx["open"]:
        ctx["errors"] = []
        return templates.TemplateResponse(request, "participant.html", ctx, status_code=410)

    errors = []
    completed = form.get("completed")
    if completed not in ("yes", "no"):
        errors.append("Please say whether you completed the task.")
    try:
        rating = int(form.get("rating", ""))
        if not 1 <= rating <= 10:
            raise ValueError
    except ValueError:
        rating = None
        errors.append("Please choose an ease rating from 1 to 10.")
    confusing = (form.get("confusing") or "").strip()
    broken = (form.get("broken") or "").strip()
    if len(confusing) < 15:
        errors.append('"What was confusing?" needs at least 15 characters.')
    if len(broken) < 15:
        errors.append('"Did anything look broken?" needs at least 15 characters.')
    if pid and db.one("SELECT id FROM responses WHERE order_id = ? AND participant_id = ?", (order["id"], pid)):
        errors.append("A response from this participant has already been recorded.")
    if errors:
        ctx["errors"] = errors
        return templates.TemplateResponse(request, "participant.html", ctx, status_code=400)

    code = secrets.token_hex(4).upper()
    db.execute(
        "INSERT INTO responses (order_id, participant_id, completed, rating, confusing, broken, comments, "
        "completion_code, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (order["id"], pid or None, 1 if completed == "yes" else 0, rating, confusing, broken,
         (form.get("comments") or "").strip() or None, code, db.now()),
    )
    db.log_event(order["id"], "info", "Tester response received")
    ctx.update(errors=[], completion_code=code)
    return templates.TemplateResponse(request, "participant.html", ctx)


# ---- Ops screen ----

@app.get("/ops", response_class=HTMLResponse)
def ops(request: Request):
    orders = db.all_rows(
        "SELECT o.num, o.tier, o.status, o.payment_id, o.amount_usd, o.paid_at, o.scan_json, o.prompt_version, "
        "o.delivered_at, o.fail_reason, s.study_id, s.target, "
        "(SELECT COUNT(*) FROM responses r WHERE r.order_id = o.id) AS responses "
        "FROM orders o LEFT JOIN studies s ON s.order_id = o.id ORDER BY o.num DESC LIMIT 50")
    for o in orders:
        scan = json.loads(o.pop("scan_json")) if o["scan_json"] else None
        o["shot"] = "/" + scan["screenshots"]["desktop"] if scan and scan["screenshots"]["desktop"] else None
        o["errors"] = len(scan["console_errors"]) + len(scan["broken_links"]) if scan else None
        o["payment_tail"] = o.pop("payment_id")[-6:] if o["payment_id"] else None
    events = db.all_rows(
        "SELECT e.created_at, e.type, e.message, o.num FROM events e LEFT JOIN orders o ON o.id = e.order_id "
        "ORDER BY e.id DESC LIMIT 30")
    revenue = db.one("SELECT COALESCE(SUM(amount_usd), 0) AS total FROM orders WHERE paid_at IS NOT NULL")["total"]
    spent = db.one("SELECT COALESCE(SUM(amount_usd), 0) AS total FROM spend")["total"]
    prompts = db.all_rows("SELECT version, created_by, reason, created_at, active FROM prompt_versions ORDER BY version")
    return templates.TemplateResponse(request, "ops.html", {
        "orders": orders, "events": events, "revenue": revenue, "spent": spent, "prompts": prompts})
