# tests/test_role_router_rules.py

from kernel.role_router import _match_direct_rule


def test_systemd_status_routes_to_devops():
    result = _match_direct_rule("проверь статус systemd сервиса nginx")

    assert result is not None
    role, label, pattern = result

    assert role == "devops"


def test_git_status_still_routes_to_coder():
    result = _match_direct_rule("проверь git status")

    assert result is not None
    role, label, pattern = result

    assert role == "coder"
