# -*- coding: utf-8 -*-
"""end_session evaluation script: run cases through real chat/coder paths.

Usage:
  RUN_LIVE_LLM_TESTS=1 .venv/bin/python scripts/eval_end_session.py \\
    --model cc/claude-opus-5 \\
    --model cc/claude-sonnet-5 \\
    --model ds/deepseek-v4-pro \\
    --budget 2000000 \\
    --stop-at 19:15+06:00

Runs each case through the real agent (_run_chat for chat, run_loop for coder),
records every call's usage, stops when budget is exhausted or time runs out.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.agent import LocalAgent
from kernel.agent_state import AgentConfig
from kernel.executor import SafeExecutor
from kernel.llm_clients import create_llm_client
from kernel.tests.conftest import NullUI
from kernel.user_config import load_credentials

# Model route multipliers from the user's AnyModel account
ROUTE_MULTIPLIERS = {
    "cc/": 0.06,
    "ds/": 0.15,
}


class UsageCollectorUI(NullUI):
    """UI that accumulates token usage from print_usage calls."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    def print_usage(self, usage: dict):
        """Callback for _emit_usage; records each call's tokens."""
        self.calls.append(
            {
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_tokens": usage["input_tokens"] + usage["output_tokens"],
            }
        )


@dataclass
class CaseRun:
    """One case's outcome."""

    case_id: int
    category: str
    language: str
    role: str
    model: str
    warning_state: str
    expected: str
    status: str  # "completed" | "errored" | "skipped"
    error: str | None = None
    final: dict | None = None  # closed, warned_msg_id, closed_msg_id, reason if closed
    usage_calls: list[dict] = field(default_factory=list)  # each call's usage dict
    total_tokens: int = 0  # sum of (prompt+completion) across calls
    weighted_tokens: float = 0.0  # total_tokens × route multiplier


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", action="append", dest="models", required=True, help="Model id (repeatable)")
    parser.add_argument("--budget", type=int, required=True, help="Total token budget (weighted)")
    parser.add_argument("--stop-at", required=True, help="Stop new cases at this ISO timestamp (e.g. 19:15+06:00)")
    return parser.parse_args()


def load_cases(path: Path) -> list[dict]:
    """Load JSONL cases."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def route_multiplier(model: str) -> float:
    """Return the billing multiplier for this model's route."""
    for prefix, mult in ROUTE_MULTIPLIERS.items():
        if model.startswith(prefix):
            return mult
    return 1.0  # unknown route = no discount


def run_case(case: dict, case_id: int, model: str, tmp_path: Path) -> CaseRun:
    """Run one case through the real agent on the given model."""
    # Build agent with end_session_enabled and the chosen model
    credentials = load_credentials()
    config = AgentConfig(
        provider="anymodel",
        fast_model=model,
        smart_model=model,
        persist_logs=False,
        cache_enabled=False,
        api_request_interval=0.2,
        retry_base_delay=0.1,
        max_retries=2,
        end_session_enabled=True,
    )
    client = create_llm_client(config.provider, credentials, verbose=False)
    executor = SafeExecutor(tmp_path)
    ui = UsageCollectorUI()
    agent = LocalAgent(client=client, executor=executor, config=config, ui=ui)

    # Seed history: the case conversation minus the last message (that's the run input)
    conversation = case["conversation"]
    history = []
    for msg in conversation[:-1]:
        role = "user" if msg["from"] == "user" else "assistant"
        history.append({"role": role, "content": msg["text"]})
    if history:
        # Set system + history on _chat_history; the agent will refresh system on the next turn
        # The first message in history is the first user turn, system is added by the agent
        agent._chat_history = history

    # The flagged message is the last one
    user_input = conversation[-1]["text"]

    # Inject warning if the case says so
    if case["warning_state"] == "fresh":
        # Mark warned_msg_id: the turn before this one
        from kernel import end_session

        agent._message_ledger = {"last_id": len(conversation) - 2, "user_ids": list(range(1, len(conversation), 2))}
        agent._warned_msg_id = len(conversation) - 2
    elif case["warning_state"] == "stale":
        # warned_msg_id is more than WARNING_FRESH_MESSAGES turns ago
        from kernel import end_session

        agent._message_ledger = {"last_id": len(conversation) - 2, "user_ids": list(range(1, len(conversation), 2))}
        agent._warned_msg_id = max(1, len(conversation) - 2 - end_session.WARNING_FRESH_MESSAGES - 1)
    else:  # none
        pass

    # Run: chat vs coder
    role = case["role"]
    try:
        if role == "chat":
            agent.run(user_input, routed_role="chat")
        else:  # coder
            agent.run(user_input, routed_role="coder")
    except Exception as error:
        return CaseRun(
            case_id=case_id,
            category=case["category"],
            language=case["language"],
            role=role,
            model=model,
            warning_state=case["warning_state"],
            expected=case["expected"],
            status="errored",
            error=str(error)[:500],
        )

    # Harvest usage: UsageCollectorUI recorded every print_usage call
    usage_calls = ui.calls
    total = sum(call["total_tokens"] for call in usage_calls)

    # Check closure
    from kernel import end_session

    final = None
    if end_session.is_closed(agent):
        closure = agent._closure
        final = {
            "closed": True,
            "closed_at": closure.get("closed_at"),
            "closed_msg_id": closure.get("closed_msg_id"),
        }
        # Try to extract reason from the decision log
        log = getattr(agent, "_end_session_log", [])
        if log:
            last = log[-1]
            if last.get("verdict") == "confirm":
                final["reason"] = last.get("reason", "")

    multiplier = route_multiplier(model)
    return CaseRun(
        case_id=case_id,
        category=case["category"],
        language=case["language"],
        role=role,
        model=model,
        warning_state=case["warning_state"],
        expected=case["expected"],
        status="completed",
        final=final,
        usage_calls=usage_calls,
        total_tokens=total,
        weighted_tokens=total * multiplier,
    )


def main():
    if os.environ.get("RUN_LIVE_LLM_TESTS") != "1":
        print("RUN_LIVE_LLM_TESTS must be 1", file=sys.stderr)
        return 1

    args = parse_args()
    cases_path = Path(__file__).parent.parent / "kernel" / "tests" / "data" / "end_session_cases.jsonl"
    cases = load_cases(cases_path)
    models = args.models
    budget = args.budget
    stop_time = datetime.fromisoformat(args.stop_at)

    print(f"Loaded {len(cases)} cases, {len(models)} models, budget {budget}, stop at {stop_time}")
    print(f"Models: {', '.join(models)}")
    print()

    tmp_path = Path("/tmp/eval_end_session")
    tmp_path.mkdir(exist_ok=True)

    runs: list[CaseRun] = []
    spent = 0.0

    for model_idx, model in enumerate(models):
        print(f"=== Model {model_idx + 1}/{len(models)}: {model} ===")
        for case_idx, case in enumerate(cases):
            now = datetime.now(timezone.utc)
            if now >= stop_time:
                print(f"Time limit reached at case {case_idx + 1}/{len(cases)}, stopping new cases")
                break
            if spent >= budget:
                print(f"Budget exhausted ({spent:.0f} >= {budget}), stopping")
                break

            case_id = case_idx + 1
            print(f"[{case_id}/{len(cases)}] {case['category']} / {case['role']} / {case['language']}", end=" ")
            sys.stdout.flush()

            run = run_case(case, case_id, model, tmp_path)
            runs.append(run)
            spent += run.weighted_tokens

            if run.status == "errored":
                print(f"ERROR: {run.error}")
            else:
                closed = run.final and run.final.get("closed")
                print(f"OK ({run.total_tokens} tok, {run.weighted_tokens:.0f} weighted, closed={closed})")
            sys.stdout.flush()

            # Progress every 30 cases
            if case_id % 30 == 0:
                print(f"  [{model}] {case_id}/{len(cases)} cases, {spent:.0f}/{budget} spent")

        # Model done or stopped
        completed_this_model = sum(1 for r in runs if r.model == model and r.status == "completed")
        errored_this_model = sum(1 for r in runs if r.model == model and r.status == "errored")
        print(f"Model {model} done: {completed_this_model} completed, {errored_this_model} errored\n")

    # Write results
    out_path = Path(__file__).parent.parent / "eval_end_session_results.jsonl"
    with out_path.open("w", encoding="utf-8") as handle:
        for run in runs:
            handle.write(json.dumps(asdict(run), ensure_ascii=False) + "\n")
    print(f"Written {out_path}")
    print(f"Total weighted tokens spent: {spent:.0f} / {budget}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
