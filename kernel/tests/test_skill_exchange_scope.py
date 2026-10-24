# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

from kernel.plugins.host import ServiceError
from kernel.plugins.registry import SkillRegistry
from kernel.plugins.services import AttachedSkill, SkillCall
from kernel.tests.skill_fixtures import ECHO_TOOL, install, write_skill

exchange_is_posix_only = pytest.mark.skipif(
    os.name != "posix",
    reason="the skill file exchange exists only beside the Linux runner and is opened with O_DIRECTORY/O_NOFOLLOW, "
    "which Windows lacks")


@pytest.fixture
def make_call(agent_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path / "config"))

    def build(capabilities, *, readonly=True):
        agent = agent_factory()
        registry = SkillRegistry(tmp_path / "skills")
        install(registry, write_skill(tmp_path / "src", capabilities=capabilities,
                                      tools=[{**ECHO_TOOL, "readonly": readonly}]))
        entry = registry.entry("echo")
        grant = agent.skills.grants.put("echo", entry.digest, agent.executor.workspace,
                                        entry.manifest.required_capabilities())
        skill = AttachedSkill(entry, grant, 0.0)
        exchange = tmp_path / "runner" / "exchange"
        exchange.mkdir(parents=True)
        skill.host = SimpleNamespace(exchange_dir=exchange)
        call = SkillCall(skill, entry.manifest.tools[0], "c1", "r1", agent.current_session)
        return agent, call, exchange

    return build


@exchange_is_posix_only
def test_a_handed_file_lands_as_a_new_file_of_the_inbox(make_call, tmp_path):
    agent, call, exchange = make_call(["files.read"])
    (tmp_path / "public.txt").write_text("hello", encoding="utf-8")

    result = agent.skills.services.handle(call, "files.read_bytes", {"path": "public.txt"})

    name = result["path"].rsplit("/", 1)[1]
    assert result["path"] == f"/kvist/exchange/in/{name}"
    assert (exchange / "in" / name).read_text(encoding="utf-8") == "hello"


@exchange_is_posix_only
def test_the_core_refuses_to_hand_a_file_through_a_symlinked_inbox(make_call, tmp_path, tmp_path_factory):
    agent, call, exchange = make_call(["files.read"])
    (tmp_path / "public.txt").write_text("hello", encoding="utf-8")
    outside = tmp_path_factory.mktemp("outside")
    os.symlink(outside, exchange / "in")

    with pytest.raises(ServiceError, match="not a directory") as error:
        agent.skills.services.handle(call, "files.read_bytes", {"path": "public.txt"})

    assert error.value.code == "denied"
    assert list(outside.iterdir()) == []


def _save(agent, call, path):
    return agent.skills.services.handle(call, "artifacts.save", {"path": path})


@exchange_is_posix_only
def test_a_skill_file_is_taken_when_it_is_a_regular_file_and_its_copy_is_removed(make_call):
    agent, call, exchange = make_call(["artifacts"])
    (exchange / "out").mkdir()
    (exchange / "out" / "report.txt").write_text("result", encoding="utf-8")

    artifact = _save(agent, call, "/kvist/exchange/out/report.txt")

    assert artifact["name"] == "report.txt"
    assert list((exchange.parent / "received").iterdir()) == []


@pytest.mark.parametrize("layout", [pytest.param("leaf-symlink", marks=exchange_is_posix_only),
                                    pytest.param("directory-symlink", marks=exchange_is_posix_only), "dot-dot"])
def test_a_skill_file_reached_through_a_symlink_or_dot_dot_is_refused(make_call, tmp_path_factory, layout):
    agent, call, exchange = make_call(["artifacts"])
    outside = tmp_path_factory.mktemp("outside")
    (outside / "report.txt").write_text("not the skill's", encoding="utf-8")
    if layout == "leaf-symlink":
        (exchange / "out").mkdir()
        os.symlink(outside / "report.txt", exchange / "out" / "report.txt")
        path = "/kvist/exchange/out/report.txt"
    elif layout == "directory-symlink":
        os.symlink(outside, exchange / "out")
        path = "/kvist/exchange/out/report.txt"
    else:
        path = "/kvist/exchange/../../report.txt"

    with pytest.raises(ServiceError, match="not a file of the exchange directory") as error:
        _save(agent, call, path)

    assert error.value.code == "denied"


@exchange_is_posix_only
def test_a_fifo_in_the_exchange_is_refused_without_waiting_for_a_writer(make_call):
    agent, call, exchange = make_call(["artifacts"])
    os.mkfifo(exchange / "pipe")
    errors: list[ServiceError] = []

    def save() -> None:
        try:
            _save(agent, call, "/kvist/exchange/pipe")
        except ServiceError as error:
            errors.append(error)

    thread = threading.Thread(target=save, daemon=True)
    thread.start()
    thread.join(2.0)

    assert not thread.is_alive()
    assert [error.code for error in errors] == ["bad_request"]


@pytest.fixture
def scoped_tree(tmp_path):
    (tmp_path / "allowed").mkdir()
    (tmp_path / "allowed" / "a.txt").write_text("in scope", encoding="utf-8")
    (tmp_path / "private.txt").write_text("out of scope", encoding="utf-8")
    os.symlink("../private.txt", tmp_path / "allowed" / "link.txt")
    return tmp_path


_READ_SCOPE = [{"name": "files.read", "scope": {"paths": ["allowed/**"]}}]


@pytest.mark.parametrize("method,params", [
    ("files.read", {"path": "allowed/../private.txt"}),
    ("files.read", {"path": "allowed/link.txt"}),
    ("files.read_many", {"paths": ["allowed/a.txt", "allowed/../private.txt"]}),
    ("files.read_bytes", {"path": "allowed/../private.txt"}),
    ("files.search", {"query": "scope", "path": "."}),
    ("files.search", {"query": "scope"}),
    ("files.find", {"pattern": "*.txt"}),
    ("files.list", {"path": "."}),
    ("files.tree", {"path": "allowed/.."}),
])
def test_a_path_scope_is_checked_on_the_canonical_location_by_every_file_method(make_call, scoped_tree, method, params):
    agent, call, _exchange = make_call(_READ_SCOPE)

    with pytest.raises(ServiceError, match="outside the paths") as error:
        agent.skills.services.handle(call, method, params)

    assert error.value.code == "denied"


def test_a_directory_inside_the_scope_can_be_read_listed_and_searched(make_call, scoped_tree):
    agent, call, _exchange = make_call(_READ_SCOPE)
    services = agent.skills.services

    assert services.handle(call, "files.read", {"path": "allowed/a.txt"})["content"] == "in scope"
    services.handle(call, "files.list", {"path": "allowed"})
    services.handle(call, "files.tree", {"path": "./allowed"})
    hits = services.handle(call, "files.search", {"query": "scope", "path": "allowed"})

    assert "out of scope" not in str(hits)
    assert [hit["path"] for hit in hits["results"]] == ["allowed/a.txt"]


def test_a_write_scope_is_checked_on_the_canonical_location(make_call, tmp_path):
    agent, call, _exchange = make_call([{"name": "files.write", "scope": {"paths": ["out/**"]}}], readonly=False)

    with pytest.raises(ServiceError, match="outside the paths"):
        agent.skills.services.handle(call, "files.write", {"path": "out/../escape.txt", "content": "x"})

    assert not (tmp_path / "escape.txt").exists()
