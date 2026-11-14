# -*- coding: utf-8 -*-
"""The libvirt provider (talking to the VM helper over a socket) and the helper's own refusals."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from kvist_cloud.providers import MachineSpec, ProviderError, ProviderState, make_provider
from kvist_cloud.providers.libvirt import LibvirtProvider, machine_id_of

HELPER = Path(__file__).resolve().parents[1] / "kvist_cloud" / "scripts" / "vm_ctl.py"
TOKEN = "runner-token-value-0123456789abcdef"


def load_helper():
    spec = importlib.util.spec_from_file_location("vm_ctl_under_test", HELPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def helper():
    return load_helper()


# -- the provider, against a stand-in helper on a unix socket ----------------------------------------


@pytest.fixture
async def door():
    """A socket that records the requests and answers with whatever ``door.answer`` holds."""
    directory = tempfile.mkdtemp(prefix="kv")
    path = Path(directory) / "s.sock"
    seen: list[dict] = []

    class Door:
        answer: dict | bytes = {"ok": True, "state": "running"}
        requests = seen
        socket = path

    async def serve(reader, writer):
        seen.append(json.loads(await reader.readline()))
        reply = Door.answer if isinstance(Door.answer, bytes) else json.dumps(Door.answer).encode() + b"\n"
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_unix_server(serve, path=str(path))
    yield Door
    server.close()
    await server.wait_closed()


def spec(machine_id: int = 7) -> MachineSpec:
    return MachineSpec(machine_id, 2, 4, 20, "wss://cloud.example.org/gateway/ws", TOKEN)


async def test_create_sends_the_whole_spec_and_returns_the_domain_name(door):
    provider = LibvirtProvider(door.socket)
    assert await provider.create(spec()) == "kvist-7"
    assert door.requests == [{"op": "create", "machine_id": 7, "vcpu": 2, "memory_gb": 4, "disk_gb": 20,
                              "gateway_url": "wss://cloud.example.org/gateway/ws", "token": TOKEN}]


async def test_wake_sleep_delete_and_status_name_the_machine(door):
    provider = LibvirtProvider(door.socket)
    await provider.wake("kvist-7")
    await provider.sleep("kvist-7")
    await provider.delete("kvist-7")
    assert [r["op"] for r in door.requests] == ["start", "stop", "delete"]
    assert all(r["machine_id"] == 7 for r in door.requests)
    for answer, expected in (("running", ProviderState.RUNNING), ("stopped", ProviderState.STOPPED),
                             ("missing", ProviderState.MISSING)):
        door.answer = {"ok": True, "state": answer}
        assert await provider.status("kvist-7") == expected


async def test_a_refusal_becomes_a_provider_error_that_never_carries_the_token(door):
    door.answer = {"ok": False, "error": "setup_vm.sh failed (1): Not enough free memory on the host"}
    with pytest.raises(ProviderError) as error:
        await LibvirtProvider(door.socket).create(spec())
    assert "Not enough free memory" in str(error.value) and TOKEN not in str(error.value)


@pytest.mark.parametrize("garbage", [b"", b"not json\n", b"[]\n"])
async def test_an_unreadable_answer_is_an_error_not_a_success(door, garbage):
    door.answer = garbage
    with pytest.raises(ProviderError):
        await LibvirtProvider(door.socket).wake("kvist-7")


async def test_a_missing_helper_is_a_provider_error(tmp_path):
    with pytest.raises(ProviderError, match="cannot reach the VM helper"):
        await LibvirtProvider(tmp_path / "nothing.sock").wake("kvist-7")


@pytest.mark.parametrize("ref", ["m7", "kvist-", "kvist-7; rm -rf /", "kvist--1", "../kvist-7", "kvist-7/../x", ""])
def test_only_our_own_machine_names_are_accepted(ref):
    with pytest.raises(ProviderError):
        machine_id_of(ref)


def test_make_provider_knows_libvirt_and_no_longer_the_stub(env):
    from kvist_cloud.config import load_settings

    settings = load_settings({**env, "MACHINE_PROVIDER": "libvirt"})
    assert make_provider(settings).name == "libvirt"
    with pytest.raises(ProviderError, match="unknown MACHINE_PROVIDER"):
        make_provider(load_settings({**env, "MACHINE_PROVIDER": "sprites"}))


# -- the helper: what it refuses, and the only files it touches ---------------------------------------


def good_create(**changes) -> dict:
    request = {"op": "create", "machine_id": 7, "vcpu": 2, "memory_gb": 4, "disk_gb": 20,
               "gateway_url": "wss://cloud.example.org/gateway/ws", "token": TOKEN}
    return {**request, **changes}


@pytest.mark.parametrize("line", ["", "not json", "[]", "null", '{"op": "rm"}', '{"op": "state"}'])
def test_the_helper_refuses_what_is_not_a_known_request(helper, line):
    assert helper.handle(line)["ok"] is False


@pytest.mark.parametrize("machine_id", [0, -1, True, 1.5, "7", "7; reboot", None, [7]])
def test_the_helper_refuses_a_machine_id_that_is_not_a_positive_integer(helper, machine_id, monkeypatch):
    monkeypatch.setattr(helper, "virsh", lambda *a, **k: pytest.fail("virsh must not be reached"))
    for op in ("start", "stop", "delete", "state"):
        assert helper.handle(json.dumps({"op": op, "machine_id": machine_id}))["ok"] is False


@pytest.mark.parametrize("changes", [
    {"gateway_url": "ws://cloud.example.org/gateway/ws"}, {"gateway_url": "https://x"}, {"gateway_url": 7},
    {"token": "short"}, {"token": "t" * 300}, {"token": "token with\nnewline-0123456789"}, {"token": 5},
    {"vcpu": 5}, {"memory_gb": 9}, {"disk_gb": 151}, {"vcpu": 0}, {"disk_gb": "20"},
])
def test_the_helper_refuses_a_create_that_would_exceed_or_weaken_anything(helper, changes, monkeypatch):
    monkeypatch.setattr(helper, "state_of", lambda domain: "missing")
    monkeypatch.setattr(helper.subprocess, "run", lambda *a, **k: pytest.fail("nothing may be started"))
    assert helper.handle(json.dumps(good_create(**changes)))["ok"] is False


def test_the_token_goes_to_setup_vm_in_the_environment_never_in_argv(helper, monkeypatch):
    monkeypatch.setattr(helper, "state_of", lambda domain: "missing")
    seen = {}

    def run(args, **kwargs):
        seen["args"], seen["env"] = args, kwargs["env"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(helper.subprocess, "run", run)
    assert helper.handle(json.dumps(good_create())) == {"ok": True, "state": "running"}
    assert TOKEN not in " ".join(seen["args"]) and seen["env"]["KVIST_MACHINE_TOKEN"] == TOKEN
    assert seen["args"][1:] == ["en", "7", "2", "4", "20", "wss://cloud.example.org/gateway/ws"]
    assert seen["args"][0].endswith("setup_vm.sh")


def test_an_existing_machine_is_never_recreated_over(helper, monkeypatch):
    monkeypatch.setattr(helper, "state_of", lambda domain: "stopped")
    monkeypatch.setattr(helper.subprocess, "run", lambda *a, **k: pytest.fail("must not run setup_vm.sh"))
    assert "already exists" in helper.handle(json.dumps(good_create()))["error"]


def test_a_failed_setup_reports_why_without_the_token(helper, monkeypatch):
    monkeypatch.setattr(helper, "state_of", lambda domain: "missing")
    failed = lambda a, **k: subprocess.CompletedProcess(a, 1, "", "No wheels in /x")  # noqa: E731
    monkeypatch.setattr(helper.subprocess, "run", failed)
    answer = helper.handle(json.dumps(good_create()))
    assert answer["ok"] is False and "No wheels" in answer["error"] and TOKEN not in answer["error"]


def test_delete_removes_that_machines_disk_and_seed_and_nothing_else(helper, monkeypatch, tmp_path):
    for name in ("kvist-7.qcow2", "kvist-7-seed.iso", "kvist-70.qcow2", "kvist-70-seed.iso", "debian-12-base.qcow2"):
        (tmp_path / name).write_text("x")
    monkeypatch.setattr(helper, "IMAGES_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(helper, "state_of", lambda domain: "stopped")
    monkeypatch.setattr(helper, "virsh", lambda *a, **k: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    assert helper.handle('{"op": "delete", "machine_id": 7}') == {"ok": True, "state": "missing"}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["debian-12-base.qcow2", "kvist-70-seed.iso", "kvist-70.qcow2"]
    assert all("kvist-7" in call for call in calls if call[0] in ("destroy", "undefine"))


def test_stop_asks_politely_then_pulls_the_plug(helper, monkeypatch):
    monkeypatch.setattr(helper, "SHUTDOWN_WAIT_SECONDS", 0)
    monkeypatch.setattr(helper, "state_of", lambda domain: "running")
    calls = []
    monkeypatch.setattr(helper, "virsh",
                        lambda *a, **k: calls.append(a[0]) or subprocess.CompletedProcess(a, 0, "", ""))
    assert helper.handle('{"op": "stop", "machine_id": 7}') == {"ok": True, "state": "stopped"}
    assert calls == ["shutdown", "destroy"]


def test_state_tells_missing_from_stopped_from_running(helper, monkeypatch):
    listing = {"names": ["kvist-7", "kvist-8"], "state": "shut off"}

    def virsh(*args, check=True):
        out = "\n".join(listing["names"]) if args[0] == "list" else listing["state"]
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(helper, "virsh", virsh)
    assert helper.handle('{"op": "state", "machine_id": 9}')["state"] == "missing"
    assert helper.handle('{"op": "state", "machine_id": 7}')["state"] == "stopped"
    listing["state"] = "running"
    assert helper.handle('{"op": "state", "machine_id": 7}')["state"] == "running"
