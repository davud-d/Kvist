# -*- coding: utf-8 -*-
"""Kvist as a Harbor agent, for Terminal-Bench and the other Harbor datasets.

    PYTHONPATH=scripts/bench harbor run -d terminal-bench@2.0 \\
        -a kvist_harbor:KvistAgent -m inception/mercury-3 -n 4

The model is ``<kvist provider id>/<model id>``; the provider's key comes from the
host environment under Kvist's own name for it (``INCEPTION_API_KEY`` for
``inception``). Kvist is installed from this checkout's HEAD commit, never from the
working tree, so a run always names the exact code it measured. It gets its own
Python (uv, under /opt/kvist) and leaves the task's Python and HOME alone.
"""
from __future__ import annotations

import re
import shlex
import subprocess
import tempfile
from pathlib import Path

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

REPO = Path(__file__).resolve().parents[2]
# What the core needs to run headless: it imports the classic terminal and the Zed
# frontend by module name (kernel/__init__.py); the other frontends stay out.
_ARCHIVE_PATHS = ("kernel", "frontends/tui/classic", "frontends/zed", "scripts/run_task.py", "requirements.txt")
_HOME = "/opt/kvist"
_PYTHON = f"{_HOME}/venv/bin/python"
_LOG = "/logs/agent/kvist.txt"
_TOKENS = re.compile(r"^tokens: in=(\d+) out=(\d+)", re.MULTILINE)


def _git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True, capture_output=True, text=True).stdout.strip()


class KvistAgent(BaseInstalledAgent):
    """Installs Kvist's core into the task container and runs the task headless."""

    @staticmethod
    def name() -> str:
        return "kvist"

    def version(self) -> str | None:
        return _git("rev-parse", "--short=12", "HEAD")

    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("curl", "bash"))
        with tempfile.TemporaryDirectory(prefix="kvist-harbor-") as temp:
            archive = Path(temp) / "kvist.tar.gz"
            subprocess.run(
                ["git", "-C", str(REPO), "archive", "--format=tar.gz", "-o", str(archive), "HEAD", *_ARCHIVE_PATHS],
                check=True,
            )
            await self.exec_as_root(environment, command=f"mkdir -p {_HOME}/src")
            await environment.upload_file(archive, f"{_HOME}/kvist.tar.gz")
        await self.exec_as_root(
            environment,
            command=(
                f"tar -xzf {_HOME}/kvist.tar.gz -C {_HOME}/src && "
                f"curl -LsSf https://astral.sh/uv/install.sh "
                f"| env UV_INSTALL_DIR={_HOME}/bin INSTALLER_NO_MODIFY_PATH=1 sh && "
                f"export UV_PYTHON_INSTALL_DIR={_HOME}/python UV_CACHE_DIR={_HOME}/cache && "
                f"{_HOME}/bin/uv venv -q --python 3.13 {_HOME}/venv && "
                f"{_HOME}/bin/uv pip install -q --python {_PYTHON} -r {_HOME}/src/requirements.txt && "
                f"chmod -R a+rX {_HOME}"
            ),
        )

    @with_prompt_template
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        provider, model = self._provider_and_model()
        prefix = provider.replace("-", "_").upper()
        env = {
            # Kvist's config and skills live here, not in the task's HOME; /tmp because
            # the task's user is not always root.
            "KVIST_CONFIG_DIR": "/tmp/kvist-config",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        # Kvist reads a manual-tier provider's key and endpoint from these two names.
        for name in (f"{prefix}_API_KEY", f"{prefix}_BASE_URL"):
            value = self._get_env(name)
            if value:
                env[name] = value
        task = (
            f"{_PYTHON} {_HOME}/src/scripts/run_task.py --container --workspace \"$PWD\" "
            f"--provider {shlex.quote(provider)} --model {shlex.quote(model)} {shlex.quote(instruction)}"
        )
        # Kvist's own state (.kvist/: session, change journal, logs) is not part of the
        # task's answer: it goes to the agent logs and leaves the workspace before the tests.
        await self.exec_as_agent(
            environment,
            command=(
                f"status=0; {task} 2>&1 | tee {_LOG} || status=$?; "
                "mkdir -p /logs/agent/kvist-state; "
                'if [ -d .kvist ]; then cp -a .kvist/. /logs/agent/kvist-state/ && rm -rf .kvist; fi; '
                "exit $status"
            ),
            env=env,
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        log = self.logs_dir / "kvist.txt"
        if not log.is_file():
            return
        counts = _TOKENS.findall(log.read_text(encoding="utf-8", errors="replace"))
        context.n_input_tokens = sum(int(found) for found, _ in counts)
        context.n_output_tokens = sum(int(found) for _, found in counts)

    def _provider_and_model(self) -> tuple[str, str]:
        provider, separator, model = (self.model_name or "").partition("/")
        if not separator or not provider or not model:
            raise ValueError("Kvist needs -m <kvist provider id>/<model id>, e.g. inception/mercury-3")
        return provider, model
