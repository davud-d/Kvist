import sys
import os
from pathlib import Path

# Run as `python scripts/run_task.py`, the repository root is not on the path.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kernel.agent.agent import AgentConfig, LocalAgent
from kernel.sandbox.executor import SafeExecutor
from kernel.main import build_agent
import asyncio

def run():
    workspace = Path("/tmp/kvist_test")
    executor = SafeExecutor(workspace=workspace)
    agent, config, warnings = build_agent(
        executor=executor,
        max_steps=150,
        persist_logs=True,
        verbose=True,
        language_hint="ru"
    )
    prompt = sys.argv[1]
    agent.run(prompt)

if __name__ == "__main__":
    run()
