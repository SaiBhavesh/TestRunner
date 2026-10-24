"""Whop checkout + webhooks (spec 5.5).

Mock mode: the "checkout" is the order status page, and webhooks are JSON bodies signed with
HMAC-SHA256 in an X-Mock-Signature header. This is OUR mock format, not Whop's real one (M4 VERIFY).
"""
import hashlib
import hmac
import json

from app import config


def checkout_url(order_id: str, tier: str) -> str:
    if config.MOCK_WHOP:
        return f"{config.BASE_URL}/order/{order_id}"
    raise NotImplementedError("Real Whop adapter is built in Milestone 4 (spec 5.5 VERIFY). Set MOCK_WHOP=1.")


def _mock_secret() -> bytes:
    return (config.WHOP_WEBHOOK_SECRET or config.MOCK_WEBHOOK_SECRET).encode()


def sign_mock(body: bytes) -> str:
    return hmac.new(_mock_secret(), body, hashlib.sha256).hexdigest()


def parse_webhook(body: bytes, headers) -> dict:
    """Verify the signature and return {order_id, payment_id, amount_usd}. Raises ValueError if invalid."""
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
    raise NotImplementedError("Real Whop adapter is built in Milestone 4 (spec 5.5 VERIFY). Set MOCK_WHOP=1.")
