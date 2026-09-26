import sys
import os
from pathlib import Path
from agent import AgentConfig, LocalAgent
from executor import SafeExecutor
from main import build_agent
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
