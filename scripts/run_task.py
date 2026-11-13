# -*- coding: utf-8 -*-
"""Run one task headless: the prompt goes in, the final answer comes out on stdout.

    python scripts/run_task.py [--workspace DIR] [--provider P --model M] "<prompt>"
    python scripts/run_task.py --container --workspace /app --provider inception --model mercury-3 "<task>"

``--container`` is for a benchmark task's container (Terminal-Bench through Harbor,
see scripts/bench/): the container is the sandbox, so commands run unwrapped and every
question is answered yes. It refuses to start anywhere else.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Run as `python scripts/run_task.py`, the repository root is not on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prompt", help="The task, as the person would type it.")
    parser.add_argument("--workspace", default=".", help="Directory the agent works in (default: current).")
    parser.add_argument("--container", action="store_true", help="Run inside a throwaway container without approvals.")
    parser.add_argument("--provider", help="Provider id (default: the saved one).")
    parser.add_argument("--model", help="Model for every step; needs --provider.")
    parser.add_argument("--max-steps", type=int, default=150, help="Step budget; headless it is a hard stop.")
    args = parser.parse_args()
    if args.model and not args.provider:
        parser.error("--model needs --provider")
    return args


def _executor(workspace: Path, container: bool):
    from kernel.sandbox.executor import SafeExecutor

    if not container:
        return SafeExecutor(workspace=workspace)
    from kernel.sandbox.approvals import ContainerApprovalGate

    # The terminal reads it while the executor is built, so it is set before that.
    os.environ["AGENT_SANDBOX_MODE"] = "container"
    return SafeExecutor(workspace=workspace, approvals=ContainerApprovalGate())


def main() -> int:
    args = _parse_args()
    from kernel.main import build_agent
    from kernel.sandbox.sandbox import running_in_container

    if args.container and not running_in_container():
        print("--container refused: this process is not inside a Docker or Podman container.", file=sys.stderr)
        return 2
    executor = _executor(Path(args.workspace).resolve(), args.container)
    models = {"fast": args.model, "smart": args.model} if args.model else None
    agent, _config, warnings = build_agent(
        executor=executor,
        max_steps=args.max_steps,
        persist_logs=True,
        verbose=True,
        provider_override=args.provider,
        models_override=models,
    )
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(agent.run(args.prompt))
    return 0


if __name__ == "__main__":
    sys.exit(main())
