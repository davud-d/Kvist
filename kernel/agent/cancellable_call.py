# -*- coding: utf-8 -*-
"""Blocking provider calls that give up the moment a cancellation is requested.

A stop from a frontend thread only sets ``_cancel_event``. A thread parked in a
socket read or waiting for a silent model never looks at it, so the stop used
to land at the next stream chunk or the next step. These helpers run the
blocking part on a daemon thread and keep the loop's own thread polling the
event, so the stop takes effect within ``_POLL_SECONDS``.
"""
from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

from kernel.agent.agent_state import RunCancelled

T = TypeVar("T")

_POLL_SECONDS = 0.05


def _close_quietly(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - an abandoned HTTP stream; nothing waits on its result
        return


def call_cancellable(cancel: threading.Event, call: Callable[[], T]) -> T:
    """Run ``call`` on a daemon thread; raise ``RunCancelled`` once ``cancel`` is set.

    An abandoned call is left to finish on its own thread and its result is
    closed (a stream would otherwise keep its connection open).
    """
    outcome: dict[str, Any] = {}
    done = threading.Event()
    abandoned = threading.Event()
    lock = threading.Lock()

    def target() -> None:
        try:
            value = call()
        except BaseException as error:  # re-raised on the caller's thread
            outcome["error"] = error
        else:
            outcome["value"] = value
            with lock:
                if abandoned.is_set():
                    _close_quietly(value)
        finally:
            done.set()

    threading.Thread(target=target, daemon=True, name="kvist-model-call").start()
    while not done.wait(_POLL_SECONDS):
        if cancel.is_set():
            with lock:
                abandoned.set()
                if "value" in outcome:
                    _close_quietly(outcome["value"])
            raise RunCancelled
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"]


def iterate_cancellable(cancel: threading.Event, stream: Iterable[T]) -> Iterator[T]:
    """Yield the chunks of ``stream``, raising ``RunCancelled`` as soon as ``cancel`` is set.

    The stream is read on a daemon thread. On cancellation the stream is
    closed from the caller's thread, which makes a blocked read fail there.
    """
    chunks: queue.Queue[tuple[str, Any]] = queue.Queue()
    stop = threading.Event()

    def pump() -> None:
        try:
            source = iter(stream)
            while not (stop.is_set() or cancel.is_set()):
                try:
                    chunk = next(source)
                except StopIteration:
                    break
                chunks.put(("chunk", chunk))
        except BaseException as error:  # re-raised on the consumer's thread
            chunks.put(("error", error))
            return
        chunks.put(("end", None))

    threading.Thread(target=pump, daemon=True, name="kvist-model-stream").start()
    try:
        while True:
            try:
                kind, payload = chunks.get(timeout=_POLL_SECONDS)
            except queue.Empty:
                if cancel.is_set():
                    raise RunCancelled from None
                continue
            if kind == "end":
                if cancel.is_set():
                    raise RunCancelled
                return
            if kind == "error":
                raise payload
            yield payload
    finally:
        stop.set()
        if cancel.is_set():
            _close_quietly(stream)
