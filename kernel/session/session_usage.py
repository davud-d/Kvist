# -*- coding: utf-8 -*-
"""Token, cache and cost totals of a whole session, and of a session with its children.

``UsageTotals`` is the running sum of every model call a session made (the ``usage`` event of
docs/UI_API.md section 7, summed over the session instead of one turn). The session document
keeps it as ``usage_totals``; ``--session-op status``/``list`` report it as ``usage`` and add
``usage_total``, the session's own totals plus those of every session below it through
``service.parent_session``. ``kernel/session-rs/src/usage.rs`` does the same sums: the two must
give the same numbers, so the order of the additions below is part of the contract.

Unknown stays unknown. A call that did not report its cache counts adds nothing to them and
leaves them unset until some call does; ``cost_usd`` stays unset until some call had a price.
A call without a price makes the cost a lower bound, which ``cost_approx`` says.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from typing import Any, Mapping

# One lock for all totals: the critical sections are a few additions, and a model call
# can be reported from the agent's thread, a title thread or a skill's thread.
_LOCK = threading.Lock()


@dataclass(slots=True)
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    calls: int = 0
    cost_usd: float | None = None
    cost_approx: bool = False

    def add_call(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int | None,
        cache_write_tokens: int | None,
        cost_usd: float | None,
        cost_approx: bool,
    ) -> None:
        with _LOCK:
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.cache_read_tokens = _sum_known(self.cache_read_tokens, cache_read_tokens)
            self.cache_write_tokens = _sum_known(self.cache_write_tokens, cache_write_tokens)
            self.calls += 1
            self.cost_usd = _sum_known(self.cost_usd, cost_usd)
            # No price at all is the same lower bound as a call priced from an estimate.
            self.cost_approx = self.cost_approx or cost_approx or cost_usd is None

    def to_dict(self) -> dict[str, Any]:
        with _LOCK:
            fields: dict[str, Any] = {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens}
            if self.cache_read_tokens is not None:
                fields["cache_read_tokens"] = self.cache_read_tokens
            if self.cache_write_tokens is not None:
                fields["cache_write_tokens"] = self.cache_write_tokens
            fields["calls"] = self.calls
            if self.cost_usd is not None:
                fields["cost_usd"] = self.cost_usd
            fields["cost_approx"] = self.cost_approx
            return fields

    @classmethod
    def from_document(cls, value: object) -> UsageTotals | None:
        """The totals a session document holds, or None when it holds none.

        Fields of a damaged document read as unknown (cache, cost) or zero, never as an error:
        a session list must not fail over one hand-edited number.
        """
        if not isinstance(value, dict):
            return None
        return cls(
            input_tokens=_count(value.get("input_tokens")) or 0,
            output_tokens=_count(value.get("output_tokens")) or 0,
            cache_read_tokens=_count(value.get("cache_read_tokens")),
            cache_write_tokens=_count(value.get("cache_write_tokens")),
            calls=_count(value.get("calls")) or 0,
            cost_usd=_cost(value.get("cost_usd")),
            cost_approx=value.get("cost_approx") is True,
        )


def _sum_known(left: Any, right: Any) -> Any:
    """``left + right`` where None means "not reported": it is skipped, and only None + None is None."""
    if left is None:
        return right
    if right is None:
        return left
    return left + right


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _cost(value: object) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def merged(left: UsageTotals, right: UsageTotals) -> UsageTotals:
    return UsageTotals(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        cache_read_tokens=_sum_known(left.cache_read_tokens, right.cache_read_tokens),
        cache_write_tokens=_sum_known(left.cache_write_tokens, right.cache_write_tokens),
        calls=left.calls + right.calls,
        cost_usd=_sum_known(left.cost_usd, right.cost_usd),
        cost_approx=left.cost_approx or right.cost_approx,
    )


def family_totals(documents: Mapping[str, Mapping[str, Any]]) -> dict[str, UsageTotals]:
    """Each session's own totals plus those of every session below it, for sessions with any.

    A session belongs below another when its ``service.parent_session`` names it, at any depth.
    The sum runs over the session first, then its descendants depth first, children in name
    order, each session once (a loop in the parent links cannot hang it). Floating point
    addition is not associative, so this order is what makes the native program agree to the bit.
    """
    own: dict[str, UsageTotals] = {}
    children: dict[str, list[str]] = {}
    for name in sorted(documents):
        document = documents[name]
        totals = UsageTotals.from_document(document.get("usage_totals"))
        if totals is not None:
            own[name] = totals
        service = document.get("service")
        parent = service.get("parent_session") if isinstance(service, dict) else None
        if isinstance(parent, str):
            children.setdefault(parent, []).append(name)
    if not own:
        return {}
    family: dict[str, UsageTotals] = {}
    for name in documents:
        total = _sum_below(name, own, children)
        if total is not None:
            family[name] = total
    return family


def _sum_below(root: str, own: dict[str, UsageTotals], children: dict[str, list[str]]) -> UsageTotals | None:
    total: UsageTotals | None = None
    seen: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        if name in own:
            total = own[name] if total is None else merged(total, own[name])
        pending.extend(reversed(children.get(name, ())))
    return total
