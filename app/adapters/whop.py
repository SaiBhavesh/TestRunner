"""Whop checkout + webhooks (spec 5.5).

Mock mode: the "checkout" is the order status page, and webhooks are JSON bodies signed with
HMAC-SHA256 in an X-Mock-Signature header. This is OUR mock format, not Whop's.

Real mode (verified against docs.whop.com, 2026-10-24):
- Checkout: POST {WHOP_API_BASE}/checkout_configurations with plan_id, mode "payment", metadata and
  redirect_url; the response carries id (ch_...) and purchase_url. Auth: "Authorization: Bearer <key>".
- Webhook: event "payment.succeeded", Standard Webhooks signature, checked with whop_sdk's unwrap().
  data is the payment object; we don't trust its fields and re-read the payment from the API.
- Payment: GET /payments/{id}: status "paid", metadata (copied from the checkout), usd_total.
- Server-side check: GET /payments?account_id=biz_...&checkout_configuration_ids=ch_..., for when the
  webhook doesn't arrive.
"""
import hashlib
import hmac
import json

import httpx

from app import config
from app.log import log

HTTP_TIMEOUT_SEC = 15


def create_checkout(order_id: str, tier: str) -> dict:
    """Return {"url": where to send the customer, "checkout_id": Whop ch_... id or None in mock mode}."""
    if config.MOCK_WHOP:
        return {"url": f"{config.BASE_URL}/order/{order_id}", "checkout_id": None}
    if tier != "quick":
        raise ValueError(f"No Whop plan configured for tier '{tier}'")
    data = _api("POST", "/checkout_configurations", json={
        "plan_id": config.WHOP_PLAN_QUICK,
        "mode": "payment",
        "metadata": {"order_id": order_id},
        "redirect_url": f"{config.BASE_URL}/order/{order_id}",
    })
    url = data.get("purchase_url") or ""
    if not url.startswith("https://"):
        # The docs show both full URLs and bare paths; don't guess a host.
        raise RuntimeError(f"Whop returned an unexpected purchase_url: {url!r}")
    log.info("Whop checkout %s created for order %s", data["id"], order_id)
    return {"url": url, "checkout_id": data["id"]}


def _api(method: str, path: str, **kwargs) -> dict:
    res = httpx.request(method, f"{config.WHOP_API_BASE}{path}", timeout=HTTP_TIMEOUT_SEC,
                        headers={"Authorization": f"Bearer {config.WHOP_API_KEY}"}, **kwargs)
    if res.status_code >= 400:
        raise RuntimeError(f"Whop {method} {path} returned HTTP {res.status_code}: {res.text[:300]}")
    return res.json()


def _payment_info(p: dict) -> dict:
    return {
        "order_id": str((p.get("metadata") or {}).get("order_id") or ""),
        "payment_id": p["id"],
        "amount_usd": float(p.get("usd_total") or 0),
        "paid": p.get("status") == "paid",
    }


def get_payment(payment_id: str) -> dict:
    return _payment_info(_api("GET", f"/payments/{payment_id}"))


def find_paid_payment(checkout_id: str) -> dict | None:
    """The paid payment made through this checkout, or None. Used when the webhook hasn't arrived."""
    data = _api("GET", "/payments", params={"account_id": config.WHOP_BUSINESS_ID,
                                             "checkout_configuration_ids": [checkout_id], "first": 10})
    for p in data["data"]:
        info = _payment_info(p)
        if info["paid"]:
            return info
    return None


def _mock_secret() -> bytes:
    return (config.WHOP_WEBHOOK_SECRET or config.MOCK_WEBHOOK_SECRET).encode()


def sign_mock(body: bytes) -> str:
    return hmac.new(_mock_secret(), body, hashlib.sha256).hexdigest()


def parse_webhook(body: bytes, headers) -> dict | None:
    """Verify the signature. Returns {order_id, payment_id, amount_usd} for a payment, None for other events.

    Raises ValueError if the signature is invalid. In real mode the payment is re-read from the Whop API.
    """
    if config.MOCK_WHOP:
        given = headers.get("x-mock-signature", "")
        if not hmac.compare_digest(given, sign_mock(body)):
            raise ValueError("bad webhook signature")
        data = json.loads(body)
        return {
            "order_id": str(data["order_id"]),
            "payment_id": str(data["payment_id"]),
            "amount_usd": float(data["amount_usd"]),
        }

    from whop_sdk.lib.verify_webhook import WebhookVerificationError, unwrap
    try:
        event = unwrap(body, headers=dict(headers), key=config.WHOP_WEBHOOK_SECRET)
    except WebhookVerificationError as e:
        raise ValueError(f"bad webhook signature ({e})")
    log.info("Whop webhook %s received", event.get("type"))
    if event.get("type") != "payment.succeeded":
        return None
    info = get_payment(event["data"]["id"])
    if not info["paid"]:
        raise ValueError(f"payment {info['payment_id']} is not paid according to the Whop API")
    return info
