# -*- coding: utf-8 -*-
"""API bodies accept only explicitly declared fields."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
