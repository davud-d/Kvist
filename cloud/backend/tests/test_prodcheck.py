# -*- coding: utf-8 -*-
"""The production configuration check: a good file passes, each dangerous setting is named, no value is printed."""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from kvist_cloud import prodcheck
from kvist_cloud.config import load_settings

GOOD = {
    "KVIST_CLOUD_SESSION_SECRET": "s" * 48,
    "KVIST_CLOUD_MASTER_KEY": Fernet.generate_key().decode(),
    "PUBLIC_URL": "https://cloud.example.org",
    "DATABASE_URL": "postgresql+psycopg://kvist:pw@db.internal/kvist",
    "GITHUB_CLIENT_ID": "id",
    "GITHUB_CLIENT_SECRET": "very-secret-github-value",
    "ADMIN_GITHUB_LOGINS": "owner",
    "MACHINE_PROVIDER": "sprites",
}


def check(**changes: str) -> list[str]:
    return prodcheck.problems(load_settings({**GOOD, **changes}))


def test_a_good_configuration_has_no_problems():
    assert check() == []


@pytest.mark.parametrize(
    ("change", "mention"),
    [
        ({"PUBLIC_URL": "http://cloud.example.org"}, "PUBLIC_URL"),
        ({"MACHINE_PROVIDER": "local"}, "MACHINE_PROVIDER"),
        ({"DATABASE_URL": "sqlite:////tmp/x.db"}, "DATABASE_URL"),
        ({"GITHUB_CLIENT_SECRET": ""}, "GITHUB_CLIENT_ID"),
        ({"ADMIN_GITHUB_LOGINS": ""}, "ADMIN_GITHUB_LOGINS"),
    ],
)
def test_each_dangerous_setting_is_named(change, mention):
    assert any(mention in line for line in check(**change))


def test_a_malformed_master_key_is_reported():
    assert any("Fernet" in line for line in check(KVIST_CLOUD_MASTER_KEY="not-a-key"))


def test_main_prints_reasons_never_values(tmp_path, capsys):
    env = tmp_path / "cloud.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in {**GOOD, "PUBLIC_URL": "http://x.example"}.items()))
    assert prodcheck.main(["--env-file", str(env)]) == 1
    captured = capsys.readouterr()
    assert "PUBLIC_URL" in captured.err
    for secret in (GOOD["KVIST_CLOUD_MASTER_KEY"], GOOD["GITHUB_CLIENT_SECRET"], GOOD["KVIST_CLOUD_SESSION_SECRET"]):
        assert secret not in captured.out + captured.err


def test_main_ok(tmp_path, capsys):
    env = tmp_path / "cloud.env"
    env.write_text("".join(f"{k}={v}\n" for k, v in GOOD.items()))
    assert prodcheck.main(["--env-file", str(env)]) == 0
    assert "ok:" in capsys.readouterr().out
