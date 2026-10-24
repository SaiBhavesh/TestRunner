"""Simulate testers submitting the participant form for a Human Test order (mock mode only).

POSTs to /t/{token} like a real tester would. Give either a local ORDER_ID (token read from the local
database) or --token=TOKEN from the order page (works against any server, e.g. the deployed one).
Usage: python scripts/fake_responses.py ORDER_ID [--count 5] [--base-url http://localhost:8000]
       python scripts/fake_responses.py --token=TOKEN --base-url https://your-app.onrender.com
"""
import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

from app import config, db  # noqa: E402

SAMPLES = [
    ("yes", 7, "The sign-up button was hard to find at first.", "Nothing looked broken to me on this page.", ""),
    ("no", 3, "I could not find the sign-up button anywhere.", "The confirmation email step never loaded for me.", "Gave up."),
    ("yes", 6, "The sign-up button is below the fold on my phone.", "One image on the pricing page did not load.", ""),
    ("no", 4, "I was not sure if I had to confirm my email first.", "The page jumped around while it was loading.", ""),
    ("yes", 9, "Nothing major, the form labels were clear enough.", "Everything looked fine on my laptop screen.", "Nice site."),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("order_id", nargs="?")
    parser.add_argument("--token", help="study token from the order page; use --token=VALUE (it may start with '-')")
    parser.add_argument("--count", type=int, default=config.TERAC_PARTICIPANTS)
    parser.add_argument("--base-url", default=config.BASE_URL)
    args = parser.parse_args()

    if not config.MOCK_TERAC:
        sys.exit("fake_responses.py only works with MOCK_TERAC=1")
    if bool(args.order_id) == bool(args.token):
        sys.exit("Give either ORDER_ID or --token=TOKEN")

    if args.token:
        token = args.token
    else:
        order = db.get_order(args.order_id)
        if order is None or not order["study_token"]:
            sys.exit(f"No Human Test order {args.order_id} in {config.DB_PATH}")
        token = order["study_token"]

    for i in range(args.count):
        completed, rating, confusing, broken, comments = SAMPLES[i % len(SAMPLES)]
        pid = f"mock-participant-{secrets.token_hex(3)}"
        res = httpx.post(
            f"{args.base_url}/t/{token}",
            params={config.TERAC_PARTICIPANT_PARAM: pid},
            data={"completed": completed, "rating": str(rating), "confusing": confusing,
                  "broken": broken, "comments": comments},
        )
        print(f"response {i + 1}: HTTP {res.status_code}")
        res.raise_for_status()


if __name__ == "__main__":
    main()
