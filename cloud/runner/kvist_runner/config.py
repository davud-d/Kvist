# -*- coding: utf-8 -*-
"""The runner's config file (runner.json), written by the provider when it creates the machine."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


class RunnerConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerConfig:
    machine_id: int
    gateway_url: str
    token: str = field(repr=False)
    root: Path = Path(".")
    kvist_command: tuple[str, ...] = ("kvist",)
    kvist_env: dict[str, str] = field(default_factory=dict)
    heartbeat_seconds: float = 15.0
    # Where projects and the Kvist home live. By default under ``root``; on a customer VM both are the
    # user's home directory, so a project is just ``~/<name>``.
    work_path: Path | None = None
    home_path: Path | None = None

    @property
    def work_dir(self) -> Path:
        return self.work_path or self.root / "work"

    @property
    def home_dir(self) -> Path:
        return self.home_path or self.root / "home"


def load_config(path: Path) -> RunnerConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RunnerConfigError(f"no runner config at {path}") from error
    except json.JSONDecodeError as error:
        raise RunnerConfigError(f"runner config {path} is not JSON") from error
    for name in ("machine_id", "gateway_url", "token"):
        if not raw.get(name):
            raise RunnerConfigError(f"runner config lacks {name}")
    return RunnerConfig(
        machine_id=int(raw["machine_id"]),
        gateway_url=str(raw["gateway_url"]),
        token=str(raw["token"]),
        root=Path(raw.get("root") or path.parent),
        kvist_command=tuple(raw.get("kvist_command") or ("kvist",)),
        kvist_env={str(k): str(v) for k, v in (raw.get("kvist_env") or {}).items()},
        heartbeat_seconds=float(raw.get("heartbeat_seconds") or 15.0),
        work_path=Path(raw["work_dir"]) if raw.get("work_dir") else None,
        home_path=Path(raw["home_dir"]) if raw.get("home_dir") else None,
    )
