from __future__ import annotations

import pytest

from executor import ExecutorError, SafeExecutor
from terminal import TerminalError


def test_executor_file_lifecycle_and_search(tmp_path):
    executor = SafeExecutor(tmp_path)

    written = executor._write_file("src/app.py", "print('hello')\nneedle\n")
    assert written.ok is True
    assert written.data["bytes_written"] > 0

    executor._append_file("src/app.py", "tail\n")
    read = executor._read_file("src/app.py")
    assert "needle" in read.data["content"]

    replaced = executor._replace_in_file("src/app.py", "needle", "changed")
    assert replaced.data["occurrences_replaced"] == 1
    assert "changed" in executor._read_file("src/app.py").data["content"]

    listing = executor._list_dir("src")
    assert listing.data["items"] == [{"name": "app.py", "type": "file"}]
    assert "app.py" in executor._list_tree(".", max_depth=3).data["tree"]
    assert executor._find_files("*.py").data["matches"] == ["src/app.py"]
    assert executor._search_code("changed").data["results"][0]["path"] == "src/app.py"


def test_executor_blocks_workspace_escape_env_and_internal_paths(tmp_path):
    executor = SafeExecutor(tmp_path)

    with pytest.raises(ExecutorError, match="outside workspace"):
        executor._read_file("../outside.txt")
    with pytest.raises(ExecutorError, match="env file"):
        executor._read_file(".env")
    with pytest.raises(ExecutorError, match="internal agent directory"):
        executor._list_dir(".devagent")
    with pytest.raises(ExecutorError, match="env file"):
        executor._run_shell(["cat", ".env"])


def test_executor_replace_reports_missing_fragment(tmp_path):
    executor = SafeExecutor(tmp_path)
    executor._write_file("file.txt", "abc")

    with pytest.raises(ExecutorError, match="fragment not found"):
        executor._replace_in_file("file.txt", "missing", "x")


def test_executor_git_fallback_sanitizes_internal_paths(tmp_path, monkeypatch):
    executor = SafeExecutor(tmp_path)

    def fail(command, **kwargs):
        raise TerminalError(".devagent/logs/state")

    monkeypatch.setattr(executor._terminal, "run", fail)
    result = executor._git_status()

    assert result.ok is True
    assert result.data["fallback_used"] is True
    assert ".devagent" not in result.data["stderr"]


def test_executor_normalizes_and_filters_search_results(tmp_path):
    executor = SafeExecutor(tmp_path)
    results = executor._normalize_search_results(
        [
            {"title": " Python Docs ", "url": "HTTPS://docs.python.org/3/#x", "snippet": " a  b "},
            {"title": "Bad", "url": "https://evil.example/", "snippet": "skip"},
        ],
        domain_filter=["python.org"],
        limit=10,
    )

    assert results == [
        {
            "title": "Python Docs",
            "url": "https://docs.python.org/3/",
            "snippet": "a b",
        }
    ]


def test_executor_fetch_url_uses_mocked_http_and_extracts_html(tmp_path, monkeypatch):
    executor = SafeExecutor(tmp_path)

    def fake_http(**kwargs):
        return (
            200,
            {"Content-Type": "text/html; charset=utf-8"},
            b"<html><title>Docs</title><body><h1>Hello</h1><p>World</p></body></html>",
        )

    monkeypatch.setattr(executor, "_http_request", fake_http)
    result = executor._fetch_url("https://docs.python.org/3/", max_chars=1000)

    assert result.ok is True
    assert result.data["title"] == "Docs"
    assert "Hello" in result.data["content"]
    assert result.data["truncated"] is False


def test_executor_fetch_url_blocks_untrusted_and_binary_urls(tmp_path):
    executor = SafeExecutor(tmp_path)

    with pytest.raises(ExecutorError, match="trusted domains"):
        executor._fetch_url("https://example.invalid/")
    with pytest.raises(ExecutorError, match="binary or archive"):
        executor._fetch_url("https://docs.python.org/file.zip")
