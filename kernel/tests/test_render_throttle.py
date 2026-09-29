from __future__ import annotations

from kernel.render_throttle import ThrottledRenderer


def test_throttled_renderer_buffers_until_interval(monkeypatch):
    times = iter([0.0, 0.05, 0.20])
    monkeypatch.setattr("kernel.render_throttle.time.time", lambda: next(times))
    rendered: list[str] = []
    renderer = ThrottledRenderer(max_fps=10, render_fn=rendered.append)

    renderer.feed("a")
    assert renderer.maybe_render() is None
    renderer.feed("b")
    assert renderer.maybe_render() is None
    assert renderer.maybe_render() == "ab"
    assert rendered == ["ab"]


def test_throttled_renderer_flushes_remaining(monkeypatch):
    monkeypatch.setattr("kernel.render_throttle.time.time", lambda: 10.0)
    renderer = ThrottledRenderer()
    renderer.feed("tail")
    assert renderer.flush() == "tail"
    assert renderer.flush() is None
