# -*- coding: utf-8 -*-
"""The cloud for the phone app's browser tests: a real server, a real runner on this computer, the scripted Kvist.

Only the outside world is faked: Stripe is the contract fake from backend/tests, and any other host (the model
provider the key is checked against) answers like an OpenAI-compatible API. Nothing leaves this computer.

    python server.py --port 8742 --data /tmp/x     prints "READY <url>" when it accepts connections
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
import uvicorn
from cryptography.fernet import Fernet

HERE = Path(__file__).resolve().parent
CLOUD = HERE.parents[1]
sys.path[:0] = [str(CLOUD / "backend"), str(CLOUD / "backend" / "tests"), str(CLOUD / "runner")]

from kvist_cloud.config import load_settings  # noqa: E402
from kvist_cloud.main import create_app  # noqa: E402
from stripe_fake import PUBLISHABLE, SECRET, WEBHOOK_SECRET, StripeFake  # noqa: E402

FAKE_KVIST = CLOUD / "runner" / "tests" / "fake_kvist.py"


def handler(stripe: StripeFake):
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.stripe.com":
            return stripe(request)
        # The model provider the key is checked against.
        return httpx.Response(200, json={"data": [{"id": "fake-model"}]})

    return answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8742)
    parser.add_argument("--data", required=True)
    parser.add_argument("--no-wallet", action="store_true")
    args = parser.parse_args()
    url = f"http://127.0.0.1:{args.port}"
    env = {
        "KVIST_CLOUD_ENV_FILE": str(Path(args.data) / "no.env"),  # never the real cloud/.env
        "KVIST_CLOUD_DATA_DIR": args.data,
        "KVIST_CLOUD_SESSION_SECRET": secrets.token_urlsafe(40),
        "KVIST_CLOUD_MASTER_KEY": Fernet.generate_key().decode(),
        "PUBLIC_URL": url,
        "AUTH_DEV_LOGIN": "true",
        "ALLOW_LOCAL_ENDPOINTS": "true",
        # The runner starts in a clean environment: the path to its package goes in the command itself.
        "RUNNER_COMMAND": json.dumps(["/usr/bin/env", f"PYTHONPATH={CLOUD / 'runner'}", sys.executable, "-m", "kvist_runner"]),
        "KVIST_COMMAND": json.dumps([sys.executable, str(FAKE_KVIST)]),
        "HEARTBEAT_SECONDS": "0.5",
        "HEARTBEAT_MAX_GAP_SECONDS": "5",
        "FRONTEND_DIST": str(CLOUD / "frontend" / "dist"),
    }
    if not args.no_wallet:
        env |= {"WALLET_ENABLED": "true", "STRIPE_SECRET_KEY": SECRET, "STRIPE_PUBLISHABLE_KEY": PUBLISHABLE,
                "STRIPE_WEBHOOK_SECRET": WEBHOOK_SECRET, "APPLE_PAY_MERCHANT_ID": "merchant.site.kvist.cloud"}
    stripe = StripeFake()
    app = create_app(load_settings(env), http_transport=httpx.MockTransport(handler(stripe)))
    # What the tests may look at: the charges Stripe was asked for, and how a card should behave.
    @app.get("/__test/charges")
    def charges() -> dict:
        return {"charges": [dict(parse_qsl(r.content.decode())) for r in stripe.paid()]}

    @app.post("/__test/card/{outcome}")
    def card(outcome: str) -> dict:
        stripe.cards["tok_goodGooglePayToken01"] = outcome
        stripe.cards["tok_appleMadeToken0001"] = outcome
        return {"ok": True}

    config = uvicorn.Config(app, host="127.0.0.1", port=args.port, log_level="warning")
    server = uvicorn.Server(config)
    original = server.startup

    async def startup(sockets=None):
        await original(sockets)
        print(f"READY {url}", flush=True)

    server.startup = startup
    server.run()


if __name__ == "__main__":
    main()
