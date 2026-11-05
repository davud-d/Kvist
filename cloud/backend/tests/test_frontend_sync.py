# -*- coding: utf-8 -*-
"""The prerendered pricing page reads frontend/src/plans.json; it must say what plans.py says."""
from __future__ import annotations

import json

from kvist_cloud import plans
from kvist_cloud.config import CLOUD_ROOT


def test_pricing_page_plans_match_the_server():
    shown = json.loads((CLOUD_ROOT / "frontend" / "src" / "plans.json").read_text(encoding="utf-8"))
    assert shown["plans"] == plans.public_catalog()
    assert shown["hour_pack"] == {"hours": plans.HOUR_PACK_HOURS, "price_usd": plans.HOUR_PACK_PRICE_USD}
