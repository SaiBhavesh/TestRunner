"""Simulate Whop confirming payment for an order (mock mode only).

Usage: python scripts/fake_webhook.py ORDER_ID [--base-url http://localhost:8000]
"""
import argparse
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import config  # noqa: E402
from app.adapters import whop  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order_id")
    parser.add_argument("--base-url", default=config.BASE_URL)
    args = parser.parse_args()

    if not config.MOCK_WHOP:
        sys.exit("fake_webhook.py only works with MOCK_WHOP=1")

    status = httpx.get(f"{args.base_url}/api/orders/{args.order_id}")
    status.raise_for_status()
    tier = status.json()["tier"]

    body = json.dumps({
        "order_id": args.order_id,
        "payment_id": "mock_pay_" + secrets.token_hex(8),
        "amount_usd": config.PRICES[tier],
    }).encode()
    res = httpx.post(f"{args.base_url}/webhooks/whop", content=body,
                     headers={"Content-Type": "application/json", "X-Mock-Signature": whop.sign_mock(body)})
    print(res.status_code, res.text)
    res.raise_for_status()


if __name__ == "__main__":
    main()
