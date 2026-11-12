# -*- coding: utf-8 -*-
"""The native session program (kernel/session-rs) against the Python implementation.

``kvist-session`` and ``session_store.run_session_op`` serve the same requests on the
same files, and whichever is installed answers. These tests are what keeps them one
contract: every scenario runs through both on identical workspaces and must leave the
same answers and the same files, and generated text must be redacted the same way.

The comparisons need the built program (``cargo build --release`` in
``kernel/session-rs``) and are skipped without it; the hand-over tests at the end use
a shell-script stand-in, so they run wherever there is a shell (not on Windows).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import re
import stat
import string
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from kernel.config import user_config
from kernel.sandbox import redaction
from kernel.sandbox.redaction import redact_value, sanitize_display_text
from kernel.session import native
from kernel.session.durable_json import JsonStorageError
from kernel.session.session_store import run_session_op

ROOT = Path(__file__).parents[2]
SessionOp = Callable[..., dict[str, Any]]


@pytest.fixture
def binary(monkeypatch) -> str:
    monkeypatch.delenv(native.SESSION_BIN_ENV, raising=False)
    found = native.find_session_binary()
    if found is None:
        pytest.skip("kvist-session is not built (cargo build --release in kernel/session-rs)")
    return found


def python_op(workspace: Path, op: str, name: str | None = None, *, text: str | None = None, **cursors: int) -> dict:
    """One operation in Python, answered the way ``main._run_session_op`` prints it."""
    try:
        return run_session_op(workspace, op, name, text=text, **cursors)
    except (ValueError, JsonStorageError, OSError) as error:
        return {"ok": False, "error": sanitize_display_text(str(error).strip() or type(error).__name__)}


def native_op(binary: str) -> SessionOp:
    def run(workspace: Path, op: str, name: str | None = None, *, text: str | None = None, **cursors: int) -> dict:
        completed = subprocess.run(
            native.native_command(binary, workspace, op, name, **cursors),
            input=(text or "").encode("utf-8"),
            capture_output=True,
            timeout=60,
        )
        assert completed.stderr == b""
        payload = json.loads(completed.stdout.decode("utf-8"))
        assert completed.returncode == (0 if payload["ok"] else 1)
        return payload

    return run


# --- Comparing two runs -------------------------------------------------------

_AUTO_NAME = re.compile(r"chat-\d{8}-\d{6}(?:-\d+)?")


def _without_clock(value: Any) -> Any:
    """The two runs happen at different moments: their timestamps cannot be equal."""
    if isinstance(value, dict):
        return {key: "<time>" if key == "updated_at" else _without_clock(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_clock(item) for item in value]
    return value


def normalized(value: Any) -> Any:
    """``value`` with timestamps blanked and auto-allocated names numbered in order of appearance."""
    numbers: dict[str, int] = {}
    text = json.dumps(_without_clock(value), ensure_ascii=False, sort_keys=True)
    return json.loads(_AUTO_NAME.sub(lambda match: f"chat-#{numbers.setdefault(match.group(), len(numbers))}", text))


def tree(workspace: Path) -> dict[str, Any]:
    """Everything under ``.kvist``: each path with its mode, and each file with its content."""
    snapshot: dict[str, Any] = {}
    base = workspace / ".kvist"
    for path in sorted(base.rglob("*")) if base.exists() else []:
        info = path.lstat()
        entry: dict[str, Any] = {"mode": oct(stat.S_IMODE(info.st_mode))}
        if stat.S_ISREG(info.st_mode):
            raw = path.read_bytes()
            try:
                entry["json"] = json.loads(raw)
            except ValueError:
                entry["bytes"] = raw.decode("utf-8", errors="replace")
        snapshot[str(path.relative_to(base))] = entry
    return snapshot


def assert_written_like_python(workspace: Path) -> None:
    """A document the native program wrote is byte for byte what ``durable_json`` writes."""
    for path in (workspace / ".kvist").rglob("*.json*"):
        if not path.is_file() or path.name.endswith((".lock", ".jsonl")):
            continue
        text = path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except ValueError:
            continue
        assert text == json.dumps(data, ensure_ascii=False, indent=2) + "\n", path.name


def run_both(tmp_path: Path, binary: str, script: Callable[[Path, SessionOp], list], seed=None) -> list:
    """Run ``script`` through both implementations on identical workspaces; return the shared answers."""
    results = []
    for label, op in (("python", python_op), ("native", native_op(binary))):
        workspace = tmp_path / label / "work"
        workspace.mkdir(parents=True)
        if seed is not None:
            seed(workspace)
        answers = script(workspace, op)
        results.append((normalized(answers), normalized(tree(workspace))))
    assert_written_like_python(tmp_path / "native" / "work")
    (python_answers, python_tree), (native_answers, native_tree) = results
    for index, (expected, got) in enumerate(zip(python_answers, native_answers)):
        assert got == expected, f"answer {index} differs"
    assert native_tree == python_tree
    return python_answers


# --- A workspace with every kind of session -----------------------------------


def _write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _document(name: str, updated_at: Any, **fields: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "session": name,
        "updated_at": updated_at,
        "session_history": [],
        "chat_history": [],
        "response_language": None,
        "styles": {"agents": "Default", "chat": "Default"},
        "checkpoint": None,
        "title": None,
        "title_manual": False,
        **fields,
    }


def _event(seq: int, turn: int, **event: Any) -> str:
    record = {"seq": seq, "turn": turn, "ts": 1700000000 + seq / 8, "event": event}
    return json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"


def seed_workspace(workspace: Path) -> None:
    sessions = workspace / ".kvist" / "sessions"
    sessions.mkdir(parents=True)
    documents = [
        _document("titled", 50.0, title="  Named session  "),
        _document("goal-first", 40.5, session_history=[
            {"goal": "Rewrite the whole session storage layer in another language", "role": "coder", "result": "ok"},
        ]),
        _document("chat-only", 30, chat_history=[
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "   "},
            {"role": "user", "content": "what is\nthis repository about"},
        ]),
        _document("interrupted", 60.25, checkpoint={"goal": "resume me", "step": 7, "run_id": "r1"}),
        _document("closed-ru", 70.0, closed=True, response_language="ru", checkpoint={"goal": "over", "step": 2}),
        _document("closed-en", 20.0, closed=True, response_language="en"),
        _document("helper", 80.0, service={"kind": "helper", "parent": "titled"}, checkpoint={"goal": "help"}),
        _document("bool-time", True, title="Odd clock"),
        _document("secretive", 10.0, chat_history=[
            {"role": "user", "content": "deploy with API_KEY=abcd1234efgh and Authorization: Bearer abcdefgh1234"},
            {"role": "assistant", "content": "done", "password": "hunter2222", "note": "https://u:pw12345@host/x"},
        ]),
        _document("future", 90.0) | {"version": 2},
        _document("stranger", 91.0) | {"session": "someone-else"},
    ]
    for document in documents:
        name = "stranger" if document["session"] == "someone-else" else document["session"]
        _write_json(sessions / f"{name}.json", document)
    # The original flat layout: no version, the checkpoint is the document itself.
    _write_json(sessions / "legacy-flat.json", {"goal": "flat goal", "step": 3})
    (sessions / "damaged.json").write_text("{not json", encoding="utf-8")
    (sessions / "recovered.json").write_text("{torn", encoding="utf-8")
    _write_json(sessions / "recovered.json.bak", _document("recovered", 55.0, title="From the backup"))
    (sessions / "empty-file.json").write_bytes(b"")
    _write_json(sessions / "Bad Name.json", _document("bad", 1.0))
    _write_json(sessions / "x.events.json", {"not": "a session"})

    journal = [_event(1, 1, type="user", text="first"), _event(2, 1, type="final", text="answer one")]
    journal += ["not json at all\n", _event(4, 2, type="user", text="second"), "\n", _event(3, 1, type="turn_end")]
    journal += [_event(5, 2, type="step", step=1, command={"action": "shell", "args": ["ls", "-la"]})]
    journal += [_event(6, 2, type="final", text="ответ два"), '{"seq":7,"turn":2,"event":{"type":"torn']
    (sessions / "titled.events.jsonl").write_text("".join(journal), encoding="utf-8")
    (sessions / "closed-ru.events.jsonl").write_text(_event(1, 1, type="session_ended"), encoding="utf-8")
    (sessions / "ghost.events.jsonl").write_text(_event(1, 1, type="user", text="orphan"), encoding="utf-8")
    _write_json(workspace / ".kvist" / "active_session.json", {"session": "titled", "updated_at": 5.0})

    attachments = workspace / ".kvist" / "attachments"
    records = {}
    for index, (session, deleted) in enumerate(
        [("goal-first", False), ("goal-first", True), ("titled", False), ("interrupted", False)]
    ):
        attachment_id = f"att_{index:016x}"
        records[attachment_id] = {
            "id": attachment_id, "name": f"shot-{index}.png", "format": "PNG", "mime": "image/png", "size": 3,
            "width": 1, "height": 1, "frames": 1, "has_alpha": False, "origin": {"kind": "user_message"},
            "session": session, "created_at": 100.0 + index, "deleted": deleted, "extra": {},
        }
        if not deleted:
            (attachments / attachment_id).mkdir(parents=True)
            (attachments / attachment_id / "original.png").write_bytes(b"png")
    _write_json(attachments / "index.json", records)

    # The fallback that picks a session when the marker names none goes by file time.
    for order, path in enumerate(sorted(sessions.iterdir())):
        os.utime(path, (1_600_000_000 + order, 1_600_000_000 + order))


# --- Scenarios ----------------------------------------------------------------


def _in_a_new_second(op: SessionOp) -> SessionOp:
    """``op`` that waits for a new second before an operation that allocates a name.

    Auto names are timestamped to the second. The Python run is fast enough to reuse a name
    the native run (one process per operation) has already outlived, so the two would
    number their names differently by timing alone.
    """

    def run(workspace: Path, name: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        if name in ("new", "launch"):
            time.sleep(1.0 - time.time() % 1.0 + 0.01)
        return op(workspace, name, *args, **kwargs)

    return run


def test_a_fresh_workspace_goes_through_the_whole_lifecycle_identically(tmp_path, binary):
    def script(workspace: Path, op: SessionOp) -> list:
        op = _in_a_new_second(op)
        return [
            op(workspace, "status"),
            op(workspace, "list"),
            op(workspace, "create", "alpha"),
            op(workspace, "create", "alpha"),
            op(workspace, "create", "Bad Name"),
            op(workspace, "create"),
            op(workspace, "new"),
            op(workspace, "launch"),
            op(workspace, "switch", "alpha"),
            op(workspace, "switch", "ghost"),
            op(workspace, "delete", "alpha"),
            op(workspace, "history", "alpha"),
            op(workspace, "history", "default"),
            op(workspace, "reset"),
            op(workspace, "new"),
            op(workspace, "delete", "alpha"),
            op(workspace, "delete", "alpha"),
            op(workspace, "status"),
        ]

    answers = run_both(tmp_path, binary, script)

    assert answers[2]["active_session"] == "alpha"
    assert answers[3]["error"] == "Session 'alpha' already exists"
    assert answers[5]["error"] == "Session operation 'create' requires a session name"
    # The first auto-named session was never used, so the second launch dropped it.
    assert len(answers[6]["sessions"]) == len(answers[7]["sessions"]) == 2
    assert answers[6]["active_session"] != answers[7]["active_session"]
    assert answers[10]["error"] == "Cannot delete the active session 'alpha'"
    assert "alpha" not in answers[-1]["sessions"] and len(answers[-1]["sessions"]) == 2


def test_a_workspace_with_every_kind_of_session_is_read_identically(tmp_path, binary):
    def script(workspace: Path, op: SessionOp) -> list:
        return [
            op(workspace, "status"),
            op(workspace, "list"),
            op(workspace, "history", "titled"),
            op(workspace, "history", "titled", limit=2),
            op(workspace, "history", "titled", limit=2, before=4),
            op(workspace, "history", "titled", limit=2, after=1),
            op(workspace, "history", "titled", limit=0),
            op(workspace, "history", "titled", limit=5001),
            op(workspace, "history", "titled", before=0),
            op(workspace, "history", "titled", after=-1),
            op(workspace, "history", "titled", before=3, after=1),
            op(workspace, "history", "ghost"),
            op(workspace, "history", "closed-ru"),
            op(workspace, "history", "damaged"),
            op(workspace, "history", "future"),
            op(workspace, "history", "stranger"),
        ]

    answers = run_both(tmp_path, binary, script, seed_workspace)

    status = answers[0]
    assert status["active_session"] == "titled"
    assert status["resumable_session"] == "interrupted"
    assert [item["name"] for item in status["session_items"]][:3] == ["helper", "closed-ru", "interrupted"]
    assert len(status["warnings"]) == 3
    titles = {item["name"]: item["title"] for item in status["session_items"]}
    assert titles["titled"] == "Named session"
    assert titles["goal-first"] == "Rewrite the whole session storage layer"
    assert titles["chat-only"] == "what is this repository about"
    assert titles["recovered"] == "From the backup"
    secretive = next(item for item in status["session_items"] if item["name"] == "secretive")
    assert "abcd1234efgh" not in json.dumps(secretive) and "hunter2222" not in json.dumps(secretive)
    assert [event["seq"] for event in answers[2]["events"]] == [1, 2, 3, 4, 5, 6]
    assert answers[2]["skipped_lines"] == 2
    assert [event["seq"] for event in answers[4]["events"]] == [2, 3]
    assert answers[12]["closed_notice"] == "разговор окончен · Enter — новая сессия"


def test_a_workspace_with_every_kind_of_session_is_changed_identically(tmp_path, binary):
    def script(workspace: Path, op: SessionOp) -> list:
        return [
            op(workspace, "switch", "damaged"),
            op(workspace, "switch", "interrupted"),
            op(workspace, "delete", "goal-first"),
            op(workspace, "delete", "ghost"),
            op(workspace, "reset"),
            op(workspace, "switch", "closed-ru"),
            op(workspace, "reset"),
            op(workspace, "new"),
            op(workspace, "launch"),
            op(workspace, "delete", "helper"),
            op(workspace, "delete", "damaged"),
            op(workspace, "switch", "legacy-flat"),
            op(workspace, "status"),
        ]

    answers = run_both(tmp_path, binary, script, seed_workspace)

    assert "corrupt" in answers[0]["error"]
    assert answers[3]["error"] == "Unknown session: 'ghost'"
    assert answers[6]["error"] == "Session 'closed-ru' is closed and cannot be reset; start a new one."
    assert answers[-1]["checkpoint"] == {"goal": "flat goal", "step": 3, "progress": 0}
    for label in ("python", "native"):
        kvist = tmp_path / label / "work" / ".kvist"
        index = json.loads((kvist / "attachments" / "index.json").read_text(encoding="utf-8"))
        assert [record["deleted"] for record in index.values()] == [True, True, False, True]
        assert not (kvist / "attachments" / "att_0000000000000000").exists()
        assert (kvist / "attachments" / "att_0000000000000002").is_dir()
        assert not (kvist / "sessions" / "ghost.events.jsonl").exists()


@pytest.mark.parametrize("marker", ["{broken", {"session": "gone"}, {"session": 7}, []])
def test_a_marker_that_names_no_session_falls_back_identically(tmp_path, binary, marker):
    def seed(workspace: Path) -> None:
        seed_workspace(workspace)
        path = workspace / ".kvist" / "active_session.json"
        if isinstance(marker, str):
            path.write_text(marker, encoding="utf-8")
        else:
            _write_json(path, marker)

    answers = run_both(tmp_path, binary, lambda workspace, op: [op(workspace, "list")], seed)

    # The newest file that is a readable conversation, not the helper's service session.
    assert answers[0]["active_session"] == "titled"


def test_the_input_history_is_kept_identically(tmp_path, binary):
    def script(workspace: Path, op: SessionOp) -> list:
        answers = [op(workspace, "input-history")]
        for text in ["first", "first", "  \n\t", "x" * 20_001, "y" * 20_000, "вопрос\nв две строки", "first"]:
            answers.append(op(workspace, "input-history-add", text=text))
        answers.append(op(workspace, "input-history"))
        for number in range(120):
            op(workspace, "input-history-add", text=f"entry {number}")
        answers.append(op(workspace, "input-history"))
        return answers

    answers = run_both(tmp_path, binary, script)

    assert answers[-2]["entries"] == ["first", "y" * 20_000, "вопрос\nв две строки", "first"]
    assert answers[-1]["entries"] == [f"entry {number}" for number in range(20, 120)]


def test_a_missing_workspace_is_refused_identically(tmp_path, binary):
    missing = tmp_path / "nowhere"

    for op in ("status", "input-history"):
        assert native_op(binary)(missing, op) == python_op(missing, op)


def test_a_new_session_takes_the_saved_style_of_every_style_python_knows(tmp_path, binary, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config))
    styles = [*user_config._STYLE_DESCRIPTIONS, "No such style"]

    for number, style in enumerate(styles):
        _write_json(config / "config.json", {"styles": {"agents": style, "chat": "ignored"}})
        workspace = tmp_path / f"work-{number}"
        workspace.mkdir()
        native_op(binary)(workspace, "create", "styled")
        stored = json.loads((workspace / ".kvist" / "sessions" / "styled.json").read_text(encoding="utf-8"))

        expected = {family: user_config.get_saved_style(family) for family in ("agents", "chat")}
        assert stored["styles"] == expected, style


def test_the_equals_form_of_the_arguments_is_read_too(tmp_path, binary):
    native_op(binary)(tmp_path, "create", "alpha")

    completed = subprocess.run(
        [binary, f"--workspace={tmp_path}", "--session-op=history", "--session-name=alpha", "--history-limit=3"],
        capture_output=True, timeout=60,
    )

    assert json.loads(completed.stdout)["session"] == "alpha"


@pytest.mark.parametrize(
    "arguments",
    [["--session-op", "status"], ["--workspace", ".", "--session-op", "rename"], ["--workspace", ".", "--frontend", "x"]],
)
def test_a_request_the_program_cannot_serve_fails_as_json(binary, arguments):
    completed = subprocess.run([binary, *arguments], capture_output=True, timeout=60)

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["ok"] is False


# --- Redaction: generated text through both ------------------------------------

_WORDS = [
    "authorization", "Authorization", "AUTHORIZATION", "proxy-authorization", "cookie", "Cookie", "set-cookie",
    "bearer", "Bearer", "basic", "token", "Token", "password", "PASSWORD", "passwd", "secret", "api_key", "API_KEY",
    "apiKey", "api-key", "access_token", "client_secret", "private_key", "PRIVATE KEY", "private key", "credential",
    "auth", "key", "KEY", "auth_token", "x-api-key", "--token", "--password", "--api-key", "true", "false", "null",
    "None", "your_api_key", "example_value", "${TOKEN}", "{{secret}}", "<token>", "[REDACTED]", "os.environ",
    "config.token", "get_token()", "https://", "://", "user", "host.example.com", "12345678", "1234", "hunter22",
    "correcthorsebattery", "value_with_underscores", "ALongIdentifierWithoutUnderscore", "eyJ", "sk-", "sk-proj-",
    "sk_live_", "gsk_", "hf_", "key-", "AIza", "github_pat_", "ghp_", "glpat-", "xoxb-", "AKIA", "npm_", "pypi-",
    "pass_word", "monkey", "tokenize", "cookies", "secretary",
]
_SEPARATORS = [
    " ", " ", "=", "=", ":", ":", ": ", " = ", "\n", "\t", ",", ";", "&", "/", "@", "@", ".", "-", "_", "\"", "'",
    "{", "}", "[", "]", "(", ")", "<", ">", "$", "%", "\r\n", "#", "\\", "+", "~",
]
# Characters Python and Rust treat differently: case folds that reach ASCII, whitespace
# only Python counts, word characters only one of them counts, digits that are not ASCII.
_ODD = [
    "İ", "ı", "ſ", "K", "ß", "ﬁ", "ﬆ", "\x1c", "\x1f", "\x0b", "\x85", "\xa0",
    " ", " ", "　", "​", "́", "²", "‿", "١٢٣٤٥٦٧٨",
    "ж", "密码", "\U0001f600", "\x00", "\x1b", "\x7f", "‮", "﻿", "ς",
]
_ALNUM = string.ascii_letters + string.digits
_GAPS = ["", "", " ", " ", "\t", "\x1c", "\xa0", "\n"]


def _run(rng: random.Random, alphabet: str, around: int) -> str:
    return "".join(rng.choice(alphabet) for _ in range(max(0, around + rng.randint(-2, 6))))


def _cookie(rng: random.Random) -> str:
    pairs = [_run(rng, _ALNUM, 3) + "=" + _run(rng, _ALNUM + "%._-", 6) for _ in range(rng.randint(1, 3))]
    name = rng.choice(["Cookie", "cookie", "Set-Cookie", "xcookie", "cooKie", "cookıe"])
    return name + rng.choice(_GAPS) + ":" + rng.choice(_GAPS) + (rng.choice(_GAPS) + ";" + rng.choice(_GAPS)).join(pairs)


def _url(rng: random.Random) -> str:
    scheme = rng.choice(["https", "postgres", "a", "x+y.z-1", "1http", "ht tp"])
    at = rng.choice(["@", "@", "@", "", " @", "/@"])
    return f"{scheme}://{_run(rng, _ALNUM + '.', 4)}:{_run(rng, _ALNUM + ':!#', 6)}{at}{_run(rng, _ALNUM + '.', 5)}/x"


def _jwt(rng: random.Random) -> str:
    parts = [_run(rng, _ALNUM + "_-", 8) for _ in range(rng.choice([2, 3, 3, 4]))]
    return rng.choice(["", " ", "x", "-", ".", "ж"]) + "eyJ" + ".".join(parts)


def _header(rng: random.Random) -> str:
    name = rng.choice([
        "Authorization", "authorization", "Proxy-Authorization", "authorızation", "Authorİzatİon",
        "xAuthorization", "_authorization", "́authorization", "²authorization", "‿authorization",
        "Bearer", "bearer", "xbearer",
    ])
    how = rng.choice(["", "", "Basic", "Bearer", "Token", "basıc", "Digest"])
    spacing = (rng.choice(_GAPS) or " ") if how else ""
    credential = _run(rng, _ALNUM + "._~+/=-İſ", 8)
    return name + rng.choice(_GAPS) + rng.choice([":", "=", ""]) + rng.choice(_GAPS) + how + spacing + credential


def _private_key(rng: random.Random) -> str:
    begin = rng.choice([
        "-----BEGIN RSA PRIVATE KEY-----", "-----begin private key-----", "-----BEGİN PRIVATE KEY-----",
        "-----BEGIN PRIVATE KEY-----", "----BEGIN PRIVATE KEY-----",
    ])
    end = rng.choice(["-----END RSA PRIVATE KEY-----", "-----end private key-----", "-----END PRıVATE KEY-----", ""])
    return begin + "\n" + _run(rng, _ALNUM + "+/=\n", 30) + "\n" + end


def _assignment(rng: random.Random) -> str:
    key = rng.choice([
        "password", "api_key", "token", "secret", "client-secret", "ACCESS_TOKEN", "passſword", "passwd",
        "auth.token", "--token", "--api-key", "-password",
    ])
    quote = rng.choice(["", "", "\"", "'"])
    close = quote if rng.random() < 0.85 else rng.choice(["", "\"", "'"])
    value = _run(rng, _ALNUM + "_-.١é", 8)
    return (
        rng.choice(["", " ", "export ", "\"", "x"]) + key + rng.choice(["", "", "\"", "'"]) + rng.choice(_GAPS)
        + rng.choice(["=", ":", "", " "]) + rng.choice(_GAPS) + quote + value + close
    )


def _known_token(rng: random.Random) -> str:
    prefix = rng.choice([
        "sk-", "sk-proj-", "sk_live_", "rk_test_", "gsk_", "hf_", "key-", "AIza", "github_pat_", "ghp_", "glpat-",
        "xoxb-", "AKIA", "ASIA", "npm_", "pypi-", "sk_prod_", "ghx_",
    ])
    alphabet = rng.choice([_ALNUM, _ALNUM + "_-", string.ascii_uppercase + string.digits])
    before, after = rng.choice(["", " ", "x", "-", "_", "."]), rng.choice(["", " ", "_", "-", ".", "x", "\n"])
    # Each kind of token has its own least length (10, 16 or 20): one short of it, on it, one past it.
    length = rng.choice([9, 10, 11, 15, 16, 17, 19, 20, 21, 30])
    return before + prefix + "".join(rng.choice(alphabet) for _ in range(length)) + after


_TEMPLATES = [_cookie, _url, _jwt, _header, _private_key, _assignment, _known_token]


def _piece(rng: random.Random) -> str:
    kind = rng.random()
    if kind < 0.3:
        made = rng.choice(_TEMPLATES)(rng)
        at = rng.randrange(len(made) + 1)
        # Half of the credentials get a stray character somewhere: the edges are where rules differ.
        return made if rng.random() < 0.5 else made[:at] + rng.choice(_SEPARATORS + _ODD) + made[at:]
    if kind < 0.6:
        return rng.choice(_WORDS)
    if kind < 0.82:
        return rng.choice(_SEPARATORS)
    if kind < 0.94:
        return _run(rng, rng.choice([_ALNUM, _ALNUM + "_-", _ALNUM + "._~+/=-"]), rng.choice([2, 8, 16, 20]))
    return rng.choice(_ODD)


def _text(rng: random.Random) -> str:
    return "".join(_piece(rng) for _ in range(rng.randint(1, 10)))


def _value(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.random()
    if depth < 3 and kind < 0.12:
        return {_text(rng): _value(rng, depth + 1) for _ in range(rng.randint(0, 4))}
    if depth < 3 and kind < 0.2:
        return [_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if kind < 0.25:
        return rng.choice([None, True, False, 0, 1.5, -3, 10**30, 1e300])
    return _text(rng)


def assert_redacted_like_python(workspace: Path, binary: str, entries: list[dict[str, Any]]) -> None:
    """Read ``entries`` back through the native program: a session's history is redacted on the way."""
    document = {"version": 1, "session": "fuzz", "updated_at": 1.0, "session_history": entries, "chat_history": []}
    path = workspace / ".kvist" / "sessions" / "fuzz.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    expected = redact_value(document)
    assert expected != document

    payload = native_op(binary)(workspace, "status")

    for theirs, ours, original in zip(expected["session_history"], payload["session_items"][0]["session_history"], entries):
        assert ours == theirs, json.dumps(original)
    # A document that still held a secret is rewritten without it, as Python rewrites it.
    assert json.loads(path.read_text(encoding="utf-8")) == expected


@pytest.mark.parametrize("seed", range(6))
def test_generated_text_is_redacted_exactly_as_python_redacts_it(tmp_path, binary, seed):
    """Any change to ``redaction.py`` that the native port does not follow shows up here."""
    rng = random.Random(seed)

    assert_redacted_like_python(tmp_path, binary, [{"goal": _text(rng), "extra": _value(rng)} for _ in range(500)])


def _threshold_cases() -> list[str]:
    """Every credential form one character short of its least length, on it and past it.

    Random text rarely lands exactly on a rule's limit, and an off-by-one there is the
    likeliest way for the two implementations to part.
    """
    cases = []
    prefixes = [*redaction._KNOWN_TOKEN_PREFIXES, "sk-proj-", "sk-or-v1-", "sk_live_", "sk_test_", "rk_live_", "rk_test_"]
    for prefix, length, fill in itertools.product(prefixes, range(8, 23), ("a", "A", "7", "a_", "a-")):
        token = prefix + (fill * length)[:length]
        cases += [before + token + after for before in ("", " ", "x", "-") for after in ("", " ", "_", "-", ".", "x")]
    for first, second, third in itertools.product((7, 8, 9), repeat=3):
        cases += [before + f"eyJ{'a' * first}.{'b' * second}.{'c' * third}" for before in ("", " ", "x", "_")]
    headers = ("Authorization: ", "Authorization: Bearer ", "authorization=Token ", "x bearer ", "Proxy-Authorization:Basic ")
    cases += [header + "a" * length + after for header in headers for length in range(6, 11) for after in ("", " rest")]
    values = ("a", "a1", "a_", "a.", "1", "A")
    for key, operator, quote, length, fill in itertools.product(
        ("password", "API_KEY", "--token", "auth.token"), ("=", ":", ": ", " = ", " "), ("", "\"", "'"),
        (3, 4, 5, 7, 8, 9, 15, 16, 17), values,
    ):
        cases.append(f"{key}{operator}{quote}{(fill * length)[:length]}{quote}")
    for user, password in itertools.product(("", "u"), ("", "p", "p:q")):
        cases += [f"{scheme}://{user}:{password}@host/" for scheme in ("https", "a", "1a", "a+b.c-d")]
    return cases


def test_every_credential_form_is_redacted_identically_at_its_least_length(tmp_path, binary):
    assert_redacted_like_python(tmp_path, binary, [{"goal": case} for case in _threshold_cases()])


@pytest.mark.parametrize("seed", range(3))
def test_error_texts_are_sanitized_exactly_as_python_sanitizes_them(tmp_path, binary, seed):
    rng = random.Random(1000 + seed)
    names = [name for name in (_text(rng) for _ in range(150)) if name and not name.startswith("-") and "\x00" not in name]

    for name in names:
        assert native_op(binary)(tmp_path, "switch", name) == python_op(tmp_path, "switch", name), repr(name)


# --- Handing a request over ---------------------------------------------------


def _stand_in(tmp_path: Path, exit_code: int) -> Path:
    """A program that answers with its own arguments and stdin, where kvist-session would be."""
    if os.name == "nt":
        pytest.skip("the stand-in is a shell script")
    program = tmp_path / "stand-in"
    program.write_text(
        "#!/bin/sh\n"
        'printf \'{"ok": true, "argv": "%s", "stdin": "%s"}\\n\' "$*" "$(cat)"\n'
        f"exit {exit_code}\n",
        encoding="utf-8",
    )
    program.chmod(0o755)
    return program


def _core(tmp_path: Path, *arguments: str, session_bin: str, stdin: str = "") -> subprocess.CompletedProcess:
    environment = {**os.environ, native.SESSION_BIN_ENV: session_bin}
    return subprocess.run(
        [sys.executable, str(ROOT / "kernel" / "__main__.py"), *arguments],
        cwd=ROOT, env=environment, input=stdin, capture_output=True, text=True, timeout=60,
    )


def test_the_program_is_found_where_the_environment_names_it(tmp_path, monkeypatch):
    program = _stand_in(tmp_path, 0)

    monkeypatch.setenv(native.SESSION_BIN_ENV, str(program))
    assert native.find_session_binary() == str(program)
    # Naming a file that is not there switches the program off instead of falling back to another.
    monkeypatch.setenv(native.SESSION_BIN_ENV, str(tmp_path / "absent"))
    assert native.find_session_binary() is None
    program.chmod(0o644)
    monkeypatch.setenv(native.SESSION_BIN_ENV, str(program))
    assert native.find_session_binary() is None


def test_the_command_line_carries_only_what_was_asked(tmp_path):
    assert native.native_command("/bin/kvist-session", tmp_path, "status") == [
        "/bin/kvist-session", "--workspace", str(tmp_path), "--session-op", "status",
    ]
    assert native.native_command("/b", "~", "history", "chat-1", limit=5, before=9, after=0)[2:] == [
        os.path.expanduser("~"), "--session-op", "history", "--session-name", "chat-1",
        "--history-limit", "5", "--history-before", "9", "--history-after", "0",
    ]


def test_the_program_is_handed_exactly_the_operations_python_serves():
    from kernel import main
    from kernel.session.session_store import SESSION_OPS

    parser_choices = next(action.choices for action in main._build_cli_parser()._actions if action.dest == "session_op")

    assert native.NATIVE_OPS == set(SESSION_OPS) == set(parser_choices)


@pytest.mark.parametrize(
    ("arguments", "plain"),
    [
        (["--workspace", "/w", "--session-op", "status"], True),
        (["--session-op", "history", "--workspace", "/w", "--session-name", "a", "--history-before", "12"], True),
        (["--workspace", "/w", "--session-op", "history", "--history-after", "-1"], True),
        # Whatever the parser reads differently, or the program does not read at all, goes through the parser.
        (["--workspace=/w", "--session-op", "status"], False),
        (["--workspace", "/w", "--session-op"], False),
        (["--workspace", "/w", "--session-op", "rename"], False),
        (["--workspace", "/w", "--session-op", "history", "--history-limit", "five"], False),
        (["--workspace", "/w", "--session-op", "history", "--history-limit", "٥"], False),
        (["--workspace", "/w", "--session-op", "switch", "--session-name", "--remote"], False),
        (["--workspace", "~/w", "--session-op", "status"], False),
        (["--session-op", "status"], False),
        (["--remote", "host", "--remote-workspace", "/srv", "--workspace", "/w", "--session-op", "list"], False),
        (["--workspace", "/w", "--session-op", "status", "--frontend", "stdio"], False),
    ],
)
def test_only_a_plain_request_skips_the_parser(arguments, plain):
    assert native._is_plain_request(arguments) is plain


def test_an_interface_is_told_the_program_only_for_a_core_on_this_machine(tmp_path, monkeypatch):
    from argparse import Namespace

    from kernel import main

    program = _stand_in(tmp_path, 0)
    monkeypatch.setenv(native.SESSION_BIN_ENV, str(program))
    # A value inherited from the shell must never reach an interface of a core on a server.
    monkeypatch.setenv(native.SESSION_CMD_ENV, json.dumps(["/somewhere/else"]))

    local = main._frontend_environment(Namespace(remote=None), ["python", "core"], KVIST_SWITCH_FILE="/s")
    remote = main._frontend_environment(Namespace(remote="me@box"), ["python", "core", "--remote", "me@box"])
    monkeypatch.setenv(native.SESSION_BIN_ENV, str(tmp_path / "absent"))
    without = main._frontend_environment(Namespace(remote=None), ["python", "core"])

    assert json.loads(local[native.SESSION_CMD_ENV]) == [str(program)]
    assert json.loads(local["KVIST_CORE_CMD"]) == ["python", "core"] and local["KVIST_SWITCH_FILE"] == "/s"
    assert native.SESSION_CMD_ENV not in remote and native.SESSION_CMD_ENV not in without


@pytest.mark.slow
@pytest.mark.parametrize("flags", [["--remote", "me@box", "--remote-workspace", "/srv/app"]])
def test_a_session_operation_for_a_server_runs_on_the_server(tmp_path, flags):
    """It used to be served on the local stand-in folder, before main() could forward it."""
    ssh = tmp_path / "ssh"
    ssh.write_text('#!/bin/sh\necho \'{"ok": true, "from": "server"}\'\n', encoding="utf-8")
    ssh.chmod(0o755)
    workspace = tmp_path / "local-folder"
    workspace.mkdir()
    environment = {**os.environ, "KVIST_SSH": str(ssh), native.SESSION_BIN_ENV: str(_stand_in(tmp_path, 0))}

    completed = subprocess.run(
        [sys.executable, str(ROOT / "kernel" / "__main__.py"), *flags, "--workspace", str(workspace), "--session-op", "list"],
        cwd=ROOT, env=environment, capture_output=True, text=True, timeout=60,
    )

    assert json.loads(completed.stdout) == {"ok": True, "from": "server"}
    assert not (workspace / ".kvist").exists()


@pytest.mark.slow
def test_the_parser_s_side_hands_the_operation_over_too(tmp_path):
    completed = _core(
        tmp_path, f"--workspace={tmp_path}", "--session-op=history", "--session-name", "chat-1", "--history-after=0",
        session_bin=str(_stand_in(tmp_path, 0)),
    )

    assert json.loads(completed.stdout)["argv"] == (
        f"--workspace {tmp_path} --session-op history --session-name chat-1 --history-after 0"
    )


@pytest.mark.slow
def test_the_core_hands_a_session_operation_to_the_program(tmp_path):
    program = _stand_in(tmp_path, 1)

    completed = _core(
        tmp_path, "--workspace", str(tmp_path), "--session-op", "history", "--session-name", "chat-1",
        "--history-limit", "7", session_bin=str(program),
    )

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["argv"] == (
        f"--workspace {tmp_path} --session-op history --session-name chat-1 --history-limit 7"
    )

    added = _core(
        tmp_path, "--workspace", str(tmp_path), "--session-op", "input-history-add",
        session_bin=str(_stand_in(tmp_path, 0)), stdin="typed text",
    )
    assert json.loads(added.stdout)["stdin"] == "typed text"


@pytest.mark.slow
def test_the_core_serves_the_operation_itself_when_the_program_is_missing_or_broken(tmp_path):
    if os.name == "nt":
        pytest.skip("a file of bytes is not refused as a program the way it is on Unix")
    broken = tmp_path / "broken"
    broken.write_bytes(b"\x7fELF not a program")
    broken.chmod(0o755)

    for session_bin in (str(tmp_path / "absent"), str(broken)):
        completed = _core(tmp_path, "--workspace", str(tmp_path), "--session-op", "list", session_bin=session_bin)

        assert completed.returncode == 0
        assert json.loads(completed.stdout)["active_session"] == "default"


@pytest.mark.slow
def test_the_core_and_the_program_answer_the_same_through_the_command_line(tmp_path, binary):
    def round_trip(session_bin: str, workspace: Path) -> list:
        workspace.mkdir()
        answers = []
        for arguments in (["create", "--session-name", "cli"], ["status"], ["switch", "--session-name", "ghost"]):
            completed = _core(
                tmp_path, "--workspace", str(workspace), "--session-op", *arguments, session_bin=session_bin
            )
            answers.append([completed.returncode, json.loads(completed.stdout.strip().splitlines()[-1])])
        return normalized(answers)

    assert round_trip(binary, tmp_path / "native") == round_trip(str(tmp_path / "absent"), tmp_path / "python")
