# -*- coding: utf-8 -*-
"""end_session evaluation: run dataset cases through the real chat and action-loop paths.

Usage:
  RUN_LIVE_LLM_TESTS=1 .venv/bin/python scripts/eval_end_session.py \\
    --model deepseek:deepseek-v4-pro \\
    --out-dir workspace/_runs/end_session_eval \\
    --budget 2000000 \\
    --stop-at 2026-10-01T19:15:00+02:00

A model is ``provider:model`` (``deepseek:deepseek-v4-pro``, ``anymodel:ds/deepseek-v4-pro``).
``main.build_agent`` runs once per model (the product path, with the saved settings and
``end_session`` offered) and fails if the provider resolves a different model than requested.
Each case then gets a fresh workspace and a new agent on that client, seeded with the case's
earlier turns, and the flagged message is sent. Spend is the provider-reported usage of every
request, times the route multiplier. Results are appended to ``<out-dir>/<model>.jsonl``
after every case; a rerun skips completed cases and retries errored ones. Only gateway
outages are retried inside a run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import end_session  # noqa: E402
from kernel.agent.agent import LocalAgent  # noqa: E402
from kernel.agent.agent_state import AgentConfig  # noqa: E402
from kernel.sandbox.executor import SafeExecutor  # noqa: E402
from kernel.main import build_agent  # noqa: E402
from kernel.context.system_context import ensure_workspace_instructions  # noqa: E402
from kernel.tests.conftest import NullUI  # noqa: E402

CASES_PATH = ROOT / "kernel" / "tests" / "data" / "end_session_cases.jsonl"

# Billing multipliers of the user's AnyModel account, keyed by "provider:route-prefix".
# A direct provider is billed by raw usage, so it has no entry.
ROUTE_MULTIPLIERS = {
    "anymodel:cc/": 0.06,
    "anymodel:ds/": 0.15,
    "anymodel:am/": 0.0,  # free models: nothing is billed, raw usage is still recorded
}

# A coder case that wanders into real work must not eat the whole budget.
CODER_MAX_STEPS = 25

# Consecutive errored cases that mean the provider is down or out of balance, not that cases are bad.
MAX_CONSECUTIVE_ERRORS = 3

# A 502/503/504 or a timeout from the gateway usually clears within a minute. Anything else
# (401/402, a missing usage report, a bug in the case) repeats identically, so it is not retried.
OUTAGE_RETRIES = 3
OUTAGE_PAUSE_SECONDS = 30
OUTAGE_ERROR_MARKERS = ("Error code: 502", "Error code: 503", "Error code: 504", "Timeout", "ConnectionError")

# A verdict the model reached by calling end_session; "refused" is a gate rejection of a bad call.
FLAGGING_VERDICTS = ("warned", "opened", "decline", "confirm")


class UsageTap:
    """Wraps an OpenAI-style client and records the provider-reported usage of every request."""

    def __init__(self, inner: Any, sink: list[dict[str, Any]]):
        self._inner = inner
        self.chat = _TappedChat(inner.chat, sink)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _TappedChat:
    def __init__(self, inner: Any, sink: list[dict[str, Any]]):
        self.completions = _TappedCompletions(inner.completions, sink)


class _TappedCompletions:
    def __init__(self, inner: Any, sink: list[dict[str, Any]]):
        self._inner = inner
        self._sink = sink

    def create(self, **kwargs: Any) -> Any:
        try:
            result = self._inner.create(**kwargs)
        except Exception as error:
            self._record_failure(error)
            raise
        if kwargs.get("stream"):
            return self._tap_stream(result)
        self._record(getattr(result, "usage", None))
        return result

    def _tap_stream(self, stream: Any):
        usage = None
        try:
            for chunk in stream:
                # Some gateways repeat cumulative usage in every chunk, so only the last one counts.
                usage = getattr(chunk, "usage", None) or usage
                yield chunk
        except Exception as error:
            self._record_failure(error)
            raise
        finally:
            # agent_model closes an abandoned stream; the close must reach the provider's connection.
            close = getattr(stream, "close", None)
            if close is not None:
                close()
            self._record(usage)

    def _record_failure(self, error: Exception) -> None:
        # Kvist turns a provider failure into a reply text, so the case would otherwise look completed.
        self._sink.append({"error": f"{type(error).__name__}: {error}"[:300]})

    def _record(self, usage: Any) -> None:
        if usage is None:
            return
        self._sink.append(
            {
                "input_tokens": int(usage.prompt_tokens or 0),
                "output_tokens": int(usage.completion_tokens or 0),
            }
        )


class EstimateUI(NullUI):
    """Collects Kvist's own tiktoken-based usage events, kept only as a cross-check of the tap."""

    def __init__(self) -> None:
        super().__init__()
        self.estimated_tokens = 0

    def print_usage(self, usage: dict) -> None:
        self.estimated_tokens += usage["input_tokens"] + usage["output_tokens"]


@dataclass(slots=True)
class CaseRun:
    index: int
    category: str
    language: str
    role: str
    model: str
    warning_state: str
    expected: str
    status: str  # "completed" | "errored"
    closed: bool = False
    flagged: bool = False  # end_session reached a warning, a review or a decision (not a gate refusal)
    decision_log: list[dict[str, Any]] = field(default_factory=list)
    reply: str = ""
    error: str = ""
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_tokens: int = 0
    weighted_tokens: float = 0.0
    earlier_errors: list[str] = field(default_factory=list)  # errors of the attempts that were retried


def route_multiplier(model: str) -> float:
    for prefix, multiplier in ROUTE_MULTIPLIERS.items():
        if model.startswith(prefix):
            return multiplier
    return 1.0


def load_cases(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def interleaved(cases: list[dict]) -> list[tuple[int, dict]]:
    """Cases as (1-based index, case), round-robin across categories so a cut run still covers all of them."""
    by_category: dict[str, list[tuple[int, dict]]] = defaultdict(list)
    for index, case in enumerate(cases, start=1):
        by_category[case["category"]].append((index, case))
    rounds = max(len(items) for items in by_category.values())
    return [items[n] for n in range(rounds) for items in by_category.values() if n < len(items)]


def seed_conversation(agent: Any, case: dict) -> str:
    """Load the case's earlier turns into the agent as a finished conversation; return the flagged message.

    Message numbers follow the runtime's ledger (each person's message and each reply gets one), so
    the flagged message becomes number len(earlier) + 1 and a fresh warning follows the first one.
    """
    *earlier, flagged = case["conversation"]
    agent.start_fresh_session()
    chat_history: list[dict[str, str]] = [{"role": "system", "content": ""}]
    session_history: list[dict[str, Any]] = []
    user_ids: list[int] = []
    last_id = 0
    for turn, reply in zip(earlier[::2], earlier[1::2]):
        user_ids.append(last_id + 1)
        session_history.append(
            {
                "goal": turn["text"],
                "role": case["role"],
                "result": reply["text"],
                "msg_id": last_id + 1,
                "reply_id": last_id + 2,
            }
        )
        chat_history += [
            {"role": "user", "content": turn["text"]},
            {"role": "assistant", "content": reply["text"]},
        ]
        last_id += 2
    agent._session_history = session_history
    if case["role"] == "chat":
        agent._chat_history = chat_history
    agent._message_ledger = {"last_id": last_id, "user_ids": user_ids}
    if case["warning_state"] == "fresh":
        agent._warned_msg_id = user_ids[-1]
    agent.save_session()
    return flagged["text"]


def build_model_setup(spec: str) -> tuple[Any, AgentConfig, list[str]]:
    """Build the provider client and config of ``spec`` once; every case reuses them.

    Goes through ``build_agent`` for the saved settings, credentials and the live catalog check,
    which replaces a model missing from the catalog with a fallback. The eval must measure the
    requested model, so a differing result is an error, and runtime fallbacks are switched off.
    """
    provider, _, model = spec.partition(":")
    with tempfile.TemporaryDirectory(prefix="eval_end_session_setup_") as workspace:
        agent, config, warnings = build_agent(
            SafeExecutor(Path(workspace)),
            max_steps=CODER_MAX_STEPS,
            persist_logs=False,
            verbose=False,
            provider_override=provider,
            models_override={"fast": model, "smart": model},
            ui=NullUI(),
            end_session=True,
        )
    if (config.fast_model, config.smart_model) != (model, model):
        raise RuntimeError(
            f"{spec}: the provider resolved fast={config.fast_model!r} smart={config.smart_model!r} "
            f"instead of {model!r}; warnings: {warnings}"
        )
    return agent.client, replace(config, runtime_model_fallbacks_enabled=False), warnings


def open_agent(workspace: Path, client: Any, config: AgentConfig, ui: EstimateUI) -> LocalAgent:
    executor = SafeExecutor(workspace)
    ensure_workspace_instructions(workspace)
    return LocalAgent(client=client, executor=executor, config=replace(config), ui=ui)


def run_case(index: int, case: dict, spec: str, client: Any, config: AgentConfig) -> CaseRun:
    base = CaseRun(
        index=index,
        category=case["category"],
        language=case["language"],
        role=case["role"],
        model=spec,
        warning_state=case["warning_state"],
        expected=case["expected"],
        status="errored",
    )
    ui = EstimateUI()
    tapped: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="eval_end_session_") as workspace:
        try:
            agent = open_agent(Path(workspace), UsageTap(client, tapped), config, ui)
            flagged_text = seed_conversation(agent, case)
            base.reply = agent.run(flagged_text, routed_role=case["role"])
            base.decision_log = [
                {key: entry.get(key) for key in ("stage", "verdict", "reason")} for entry in agent._end_session_log
            ]
            base.closed = end_session.is_closed(agent)
        except Exception as error:  # noqa: BLE001 - one bad case is recorded, the rest of the run goes on
            base.error = f"{type(error).__name__}: {error}"[:500]
    answered = [call for call in tapped if "error" not in call]
    if not base.error and tapped and "error" in tapped[-1]:
        base.error = "last model request failed: " + tapped[-1]["error"]
    if not base.error and not answered:
        base.error = "no model request returned usage"
    base.status = "errored" if base.error else "completed"
    base.reply = base.reply[:600]
    base.flagged = any(entry["verdict"] in FLAGGING_VERDICTS for entry in base.decision_log)
    base.requests = len(answered)
    base.input_tokens = sum(call["input_tokens"] for call in answered)
    base.output_tokens = sum(call["output_tokens"] for call in answered)
    base.estimated_tokens = ui.estimated_tokens
    base.weighted_tokens = (base.input_tokens + base.output_tokens) * route_multiplier(spec)
    return base


def is_outage(error: str) -> bool:
    return any(marker in error for marker in OUTAGE_ERROR_MARKERS)


def add_attempt(retry: CaseRun, earlier: CaseRun) -> None:
    """Fold a failed earlier attempt into the retry, so spend and the cross-check cover every attempt."""
    retry.requests += earlier.requests
    retry.input_tokens += earlier.input_tokens
    retry.output_tokens += earlier.output_tokens
    retry.estimated_tokens += earlier.estimated_tokens
    retry.weighted_tokens += earlier.weighted_tokens
    retry.earlier_errors = [*earlier.earlier_errors, earlier.error]


def run_case_with_retries(index: int, case: dict, spec: str, client: Any, config: AgentConfig, stop_time: datetime) -> CaseRun:
    """Rerun a case whose gateway is briefly unavailable; any other error repeats identically and is final."""
    run = run_case(index, case, spec, client, config)
    for attempt in range(1, OUTAGE_RETRIES + 1):
        if run.status == "completed" or not is_outage(run.error) or datetime.now(stop_time.tzinfo) >= stop_time:
            break
        time.sleep(OUTAGE_PAUSE_SECONDS * attempt)
        retry = run_case(index, case, spec, client, config)
        add_attempt(retry, run)
        run = retry
    return run


def results_path(out_dir: Path, spec: str) -> Path:
    return out_dir / (re.sub(r"[^A-Za-z0-9._-]+", "_", spec) + ".jsonl")


def load_runs(path: Path) -> list[dict]:
    """Every saved run; a line torn by a killed process costs only itself, and is reported."""
    if not path.exists():
        return []
    runs = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            runs.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"{path.name}:{number}: unreadable line skipped", file=sys.stderr)
    return runs


def append_run(path: Path, run: CaseRun) -> None:
    """Append one run as a line, first ending a torn last line so the new one stays parseable."""
    with path.open("a+b") as handle:
        if handle.tell() > 0:
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write((json.dumps(asdict(run), ensure_ascii=False) + "\n").encode("utf-8"))


def latest_runs(path: Path) -> list[dict]:
    """One run per case, the last attempt, so a rerun replaces an errored line."""
    return list({run["index"]: run for run in load_runs(path)}.values())


def summarize(runs: list[dict]) -> str:
    """Per-category table: false closures (expected decline, closed) and misses (expected close, not closed)."""
    rows: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for run in runs:
        if run["status"] != "completed":
            rows[run["category"]]["errored"] += 1
            continue
        row = rows[run["category"]]
        row["n"] += 1
        row["flagged"] += run["flagged"]
        if run["expected"] == "decline":
            row["decline_n"] += 1
            row["false_close"] += run["closed"]
        else:
            row["close_n"] += 1
            row["miss"] += not run["closed"]
    lines = ["category            n  flagged  false_close/decline_n  miss/close_n  errored"]
    for category, row in sorted(rows.items()):
        lines.append(
            f"{category:<18}{row['n']:>3}{row['flagged']:>9}"
            f"{row['false_close']:>12}/{row['decline_n']:<9}{row['miss']:>8}/{row['close_n']:<4}{row['errored']:>6}"
        )
    return "\n".join(lines)


def main() -> int:
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        print("RUN_LIVE_LLM_TESTS must be 1", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", dest="models", required=True, help="provider:model (repeatable)")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--budget", type=int, required=True, help="weighted tokens across all models")
    parser.add_argument("--stop-at", required=True, help="no new case after this ISO timestamp with offset")
    parser.add_argument("--only", help="comma-separated case indexes (1-based), for a dry run")
    args = parser.parse_args()

    stop_time = datetime.fromisoformat(args.stop_at)
    if stop_time.tzinfo is None:
        parser.error("--stop-at needs a UTC offset, e.g. 2026-10-01T19:15:00+02:00")
    only = {int(item) for item in args.only.split(",")} if args.only else None
    args.out_dir.mkdir(parents=True, exist_ok=True)
    ordered = [(index, case) for index, case in interleaved(load_cases(CASES_PATH)) if only is None or index in only]

    spent = 0.0
    setup_failed = False
    for spec in args.models:
        try:
            client, config, warnings = build_model_setup(spec)
        except RuntimeError as error:
            print(f"=== {spec}: setup failed, model skipped: {error}", file=sys.stderr, flush=True)
            setup_failed = True
            continue
        for warning in warnings:
            print(f"=== {spec}: {warning}", flush=True)
        spent = run_model(spec, client, config, ordered, args.out_dir, args.budget, stop_time, spent)
    print(f"weighted tokens spent: {spent:.0f} / {args.budget}")
    return 1 if setup_failed else 0


def stop_reason(spent: float, budget: int, consecutive_errors: int, stop_time: datetime) -> str | None:
    if datetime.now(stop_time.tzinfo) >= stop_time:
        return "stop time reached, no new cases"
    if spent >= budget:
        return f"budget reached ({spent:.0f} >= {budget})"
    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
        return f"{consecutive_errors} errored cases in a row"
    return None


def run_model(
    spec: str,
    client: Any,
    config: AgentConfig,
    ordered: list[tuple[int, dict]],
    out_dir: Path,
    budget: int,
    stop_time: datetime,
    spent: float,
) -> float:
    """Run the model's pending cases, appending each result; return the total spend so far."""
    path = results_path(out_dir, spec)
    done = {run["index"] for run in latest_runs(path) if run["status"] == "completed"}
    pending = [(index, case) for index, case in ordered if index not in done]
    spent += sum(run["weighted_tokens"] for run in load_runs(path))
    print(f"=== {spec}: {len(pending)} cases to run, spent so far {spent:.0f}/{budget} ===", flush=True)
    consecutive_errors = 0
    for index, case in pending:
        reason = stop_reason(spent, budget, consecutive_errors, stop_time)
        if reason:
            print(f"{reason}, stopping {spec}", flush=True)
            break
        run = run_case_with_retries(index, case, spec, client, config, stop_time)
        append_run(path, run)
        spent += run.weighted_tokens
        consecutive_errors = consecutive_errors + 1 if run.status == "errored" else 0
        print(
            f"[{index:>3}] {run.category:<16}{run.role:<6}{run.language} {run.status:<9}"
            f"expected={run.expected:<8}closed={run.closed!s:<5}flagged={run.flagged!s:<5}"
            f"req={run.requests} in={run.input_tokens} out={run.output_tokens} {run.error}",
            flush=True,
        )
    print(summarize(latest_runs(path)), flush=True)
    return spent


if __name__ == "__main__":
    sys.exit(main())
