"""All settings come from environment variables (or a local .env file)."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _flag(name: str, default: str = "1") -> bool:
    return os.environ.get(name, default).strip() == "1"


GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Spec said gemini-2.5-flash, but Google now refuses it for new users (404, 2026-10-24).
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")
# Used when the main model returns 503 or times out. Empty disables the fallback.
GEMINI_FALLBACK_MODEL = os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-flash-latest")
# Per-call deadline sent to Google (minimum 10s). Normal calls measured at 70-140s on 2026-10-24.
GEMINI_TIMEOUT_SEC = float(os.environ.get("GEMINI_TIMEOUT_SEC", "180"))
# Per Exa search call. The exa-py default is 600s.
EXA_TIMEOUT_SEC = float(os.environ.get("EXA_TIMEOUT_SEC", "20"))
# Hard cap on a whole Solari scan, including starting and releasing the browser session.
SOLARI_HARD_LIMIT_SEC = float(os.environ.get("SOLARI_HARD_LIMIT_SEC", "90"))
# An order with no progress for this long in paid/scanning/analyzing is marked failed.
STUCK_LIMIT_MIN = float(os.environ.get("STUCK_LIMIT_MIN", "15"))
EXA_API_KEY = os.environ.get("EXA_API_KEY", "")
SOLARI_API_KEY = os.environ.get("SOLARI_API_KEY", "")
TERAC_API_KEY = os.environ.get("TERAC_API_KEY", "")
WHOP_API_KEY = os.environ.get("WHOP_API_KEY", "")
WHOP_WEBHOOK_SECRET = os.environ.get("WHOP_WEBHOOK_SECRET", "")
WHOP_BUSINESS_ID = os.environ.get("WHOP_BUSINESS_ID", "")
WHOP_PRODUCT_QUICK = os.environ.get("WHOP_PRODUCT_QUICK", "")
WHOP_PRODUCT_HUMAN = os.environ.get("WHOP_PRODUCT_HUMAN", "")
# Not in the spec's list: the Whop plan (plan_...) a Quick Check checkout is created from (verified M4).
WHOP_PLAN_QUICK = os.environ.get("WHOP_PLAN_QUICK", "")
# Sandbox: https://sandbox-api.whop.com/api/v1
WHOP_API_BASE = os.environ.get("WHOP_API_BASE", "https://api.whop.com/api/v1").rstrip("/")
# Human Test is hidden while Terac costs more per tester than the tier's price.
HUMAN_TEST_ENABLED = os.environ.get("HUMAN_TEST_ENABLED", "0").strip() == "1"

# On Render, RENDER_EXTERNAL_URL is the service's public onrender.com URL (set by Render automatically).
BASE_URL = (os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")
IS_LOCAL = BASE_URL.startswith(("http://localhost", "http://127.0.0.1"))
PRICE_QUICK = float(os.environ.get("PRICE_QUICK", "5"))
PRICE_HUMAN = float(os.environ.get("PRICE_HUMAN", "20"))

TERAC_PARTICIPANTS = int(os.environ.get("TERAC_PARTICIPANTS", "5"))
TERAC_BUDGET_TOTAL = float(os.environ.get("TERAC_BUDGET_TOTAL", "100"))
TERAC_MAX_PER_ORDER = float(os.environ.get("TERAC_MAX_PER_ORDER", "25"))
TERAC_STALL_MIN = float(os.environ.get("TERAC_STALL_MIN", "20"))
# Not in the spec's list: the URL query parameter Terac uses to pass a participant id.
# The real name is unverified (spec 5.6 VERIFY) and gets set at M6.
TERAC_PARTICIPANT_PARAM = os.environ.get("TERAC_PARTICIPANT_PARAM", "participant_id")

MOCK_SOLARI = _flag("MOCK_SOLARI")
MOCK_EXA = _flag("MOCK_EXA")
MOCK_GEMINI = _flag("MOCK_GEMINI")
MOCK_TERAC = _flag("MOCK_TERAC")
MOCK_WHOP = _flag("MOCK_WHOP")
ANY_MOCK = MOCK_SOLARI or MOCK_EXA or MOCK_GEMINI or MOCK_TERAC or MOCK_WHOP

# Only used when MOCK_WHOP=1 and no secret is set, so fake_webhook.py can sign requests locally.
MOCK_WEBHOOK_SECRET = "mock-webhook-secret"

DB_PATH = os.environ.get("DB_PATH", str(ROOT / "testrunner.db"))
# Screenshots are served at /static/shots/. On Render, point this at the persistent disk.
SHOTS_DIR = Path(os.environ.get("SHOTS_DIR", str(ROOT / "app" / "static" / "shots")))
WORKER_ENABLED = _flag("WORKER_ENABLED")
WORKER_INTERVAL_SEC = float(os.environ.get("WORKER_INTERVAL_SEC", "1"))

PRICES = {"quick": PRICE_QUICK, "human": PRICE_HUMAN}
TIER_NAMES = {"quick": "Quick Check", "human": "Human Test"}
