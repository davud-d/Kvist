from __future__ import annotations

import json
import stat

from tests.conftest import requires_posix

import performance_logger
from performance_logger import PerfLogger


def test_perf_logger_is_noop_when_disabled(tmp_path):
    logger = PerfLogger(tmp_path)
    logger.log_render(render_duration_ms=100)
    logger.flush()
    assert not (tmp_path / ".kvist").exists()


@requires_posix
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
        error="Authorization: Bearer synthetic-token-" + "x" * 400,
    )
    logger.flush()

    path = tmp_path / ".kvist" / "logs" / "debug" / "logs.jsonl"
    record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert record["type"] == "api_request"
    assert "synthetic-token" not in record["error"]
    assert "[REDACTED]" in record["error"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_perf_logger_refuses_symlinked_internal_directory(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "outside"
    workspace.mkdir()
    outside.mkdir()
    (workspace / ".kvist").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(performance_logger, "DEBUG_LOGGING", True)
    logger = PerfLogger(workspace)

    logger.log_api_request(
        step=1,
        model="fixture",
        request_start=1.0,
        total_duration=0.1,
        messages_count=1,
        input_tokens=1,
        estimated_tokens=1,
        prompt_chars=1,
        history_size_kb=0.1,
        error="synthetic",
    )
    logger.flush()

    assert list(outside.iterdir()) == []
