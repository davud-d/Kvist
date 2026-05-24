# -*- coding: utf-8 -*-
"""Non-blocking render throttle for streaming chunk output.

Limits screen updates to at most ``max_fps`` times per second by
buffering incoming text chunks and flushing only when the minimum
interval has elapsed.  Uses ``time.time()`` — **never** ``time.sleep()``.

Integration point
-----------------
In ``agent_model.call_model_streaming``, wrap the per-chunk verbose
print with a ``ThrottledRenderer``::

    from render_throttle import ThrottledRenderer

    renderer = ThrottledRenderer(max_fps=10)
    for chunk in stream:
        ...
        if delta and delta.content:
            renderer.feed(delta.content)
            rendered = renderer.maybe_render()
            if rendered is not None and self.config.verbose:
                print(f"  [streaming] {rendered[:120]}...", flush=True)
    # after loop
    leftover = renderer.flush()
    if leftover is not None and self.config.verbose:
        print(f"  [streaming] {leftover[:120]}", flush=True)
"""
from __future__ import annotations

import time
from typing import Callable


class ThrottledRenderer:
    """Accumulate text chunks and emit combined output at a capped rate.

    Parameters
    ----------
    max_fps:
        Maximum renders per second (default **10** → interval 0.1 s).
    render_fn:
        Optional callback invoked with the buffered text on every
        allowed render tick.  If *None*, ``maybe_render`` simply returns
        the buffered string so the caller can render it.

    Thread-safety
    -------------
    **Not** thread-safe by design — intended for a single synchronous
    generator loop that processes streaming chunks sequentially.
    """

    __slots__ = ("_interval", "_buffer", "_last_render", "_render_fn")

    def __init__(
        self,
        max_fps: int = 10,
        render_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._interval: float = 1.0 / max_fps
        self._buffer: list[str] = []
        self._last_render: float = 0.0
        self._render_fn = render_fn

    # ── public API ────────────────────────────────────────────────

    def feed(self, chunk: str) -> None:
        """Append a chunk to the internal buffer.  O(1) amortised."""
        self._buffer.append(chunk)

    def maybe_render(self) -> str | None:
        """Flush the buffer **only** if ≥ interval seconds have passed.

        Returns the concatenated buffer text when a render happens,
        or *None* if the interval has not elapsed yet.
        """
        now = time.time()
        if now - self._last_render < self._interval:
            return None

        return self._do_flush(now)

    def flush(self) -> str | None:
        """Force-render whatever remains in the buffer (end-of-stream).

        Returns the remaining text, or *None* if the buffer is empty.
        """
        if not self._buffer:
            return None

        return self._do_flush(time.time())

    # ── internals ─────────────────────────────────────────────────

    def _do_flush(self, now: float) -> str | None:
        if not self._buffer:
            return None

        text = "".join(self._buffer)
        self._buffer.clear()
        self._last_render = now

        if self._render_fn is not None:
            self._render_fn(text)

        return text
