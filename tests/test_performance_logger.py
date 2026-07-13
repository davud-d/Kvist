from __future__ import annotations

import json

import performance_logger
from performance_logger import PerfLogger


def test_perf_logger_is_noop_when_disabled(tmp_path):
    logger = PerfLogger(tmp_path)
    logger.log_render(render_duration_ms=100)
    logger.flush()
    assert not (tmp_path / ".kvist").exists()


def test_perf_logger_writes_jsonl_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(performance_logger, "DEBUG_LOGGING", True)
    logger = PerfLogger(tmp_path)

    logger.log_api_request(
        step=1,
        model="m",
        request_start=1.0,
        total_duration=0.2,
        messages_count=2,
        input_tokens=3,
        estimated_tokens=4,
        prompt_chars=5,
        history_size_kb=1.2,
        ok=False,
        error="x" * 400,
    )
    logger.flush()

    path = tmp_path / ".kvist" / "logs" / "debug" / "logs.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["type"] == "api_request"
    assert len(record["error"]) == 300
