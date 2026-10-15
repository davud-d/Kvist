"""Core services behind skills and the agent: memory, storage, attachments, questions, resources, network, viewer."""
from __future__ import annotations

import http.server
import io
import os
import socket
import struct
import threading
import urllib.error
import urllib.request

import pytest
from PIL import Image

from kernel.attachments import AttachmentGone, AttachmentStore
from kernel.plugins.http_client import HttpPolicyError, request
from kernel.plugins.net_broker import Denied, NetPolicy, TaskService, TaskServices, decide
from kernel.plugins.screen import ScreenViewer
from kernel.plugins.storage import SkillStorage, StorageError
from kernel.resources import ManagedProcess, Owner, ResourceManager
from kernel.tests.conftest import NullUI
from kernel.workspace_memory import NoteError, WorkspaceMemory


# --- memory ------------------------------------------------------------------


def test_notes_never_cross_workspaces(tmp_path):
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    WorkspaceMemory(first).add("The tests need a local postgres", kind="observation", author="agent")

    assert WorkspaceMemory(second).search("postgres") == []
    assert WorkspaceMemory(first).search("postgres")[0][1].text == "The tests need a local postgres"


def test_concurrent_note_writes_lose_nothing(tmp_path):
    memory = WorkspaceMemory(tmp_path)
    errors: list[Exception] = []

    def add(index: int) -> None:
        try:
            WorkspaceMemory(tmp_path).add(f"fact number {index}", kind="observation", author="agent")
        except Exception as error:  # noqa: BLE001 - collected for the assertion
            errors.append(error)

    threads = [threading.Thread(target=add, args=(index,)) for index in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(memory.list()) == 20


def test_only_the_person_makes_or_rewrites_a_decision_and_outdated_notes_keep_their_reason(tmp_path):
    memory = WorkspaceMemory(tmp_path)
    with pytest.raises(NoteError, match="only the person"):
        memory.add("Use tabs", kind="decision", author="agent")
    decision = memory.add("Use tabs", kind="decision", author="person")
    with pytest.raises(NoteError, match="only by the person"):
        memory.update(decision.id, author="agent", text="Use spaces")

    outdated = memory.update(decision.id, author="agent", status="outdated", reason="the team moved to spaces")

    assert outdated.status == "outdated" and outdated.outdated_reason == "the team moved to spaces"
    assert memory.list() == []
    assert memory.list(include_outdated=True)[0].text == "Use tabs"


def test_secrets_are_redacted_before_a_note_is_stored(tmp_path):
    note = WorkspaceMemory(tmp_path).add("The key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789",
                                         kind="observation", author="agent")
    assert "abcdefghijklmnopqrstuvwxyz" not in note.text


# --- skill storage -----------------------------------------------------------


def test_storage_versions_detect_a_lost_update_and_scopes_stay_apart(tmp_path):
    storage = SkillStorage(tmp_path, "browser", "1.0.0")
    first = storage.set("workspace", "workspace", "cookies", {"n": 1}, expected_version=None)
    storage.set("workspace", "workspace", "cookies", {"n": 2}, expected_version=first["version"])

    with pytest.raises(StorageError) as conflict:
        storage.set("workspace", "workspace", "cookies", {"n": 3}, expected_version=first["version"])
    assert conflict.value.code == "conflict"
    assert storage.get("task", "run-1", "cookies") == {"value": None, "version": 0}


def test_an_older_version_refuses_state_a_newer_one_wrote(tmp_path):
    SkillStorage(tmp_path, "browser", "2.0.0").set("workspace", "workspace", "k", 1, None)

    with pytest.raises(StorageError) as refused:
        SkillStorage(tmp_path, "browser", "1.9.0").get("workspace", "workspace", "k")
    assert refused.value.code == "newer_state"


# --- attachments -------------------------------------------------------------


def _png_with_alpha(width=64, height=40) -> bytes:
    image = Image.new("RGBA", (width, height), (255, 0, 0, 0))
    image.putpixel((1, 1), (0, 0, 255, 255))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_the_original_is_kept_beside_the_model_copy_and_a_crop_is_a_new_attachment(tmp_path):
    store = AttachmentStore(tmp_path)
    data = _png_with_alpha()
    original = store.add_bytes(data, "logo.png", origin={"kind": "user_message"}, session="s1")

    assert original.format == "PNG" and original.has_alpha
    assert store.original_path(original.id).read_bytes() == data
    assert store.model_path(original.id).read_bytes()[:3] == b"\xff\xd8\xff"  # the JPEG copy for models
    cropped = store.crop(original.id, [0, 0, 32, 20], session="s1")

    assert cropped.id != original.id and (cropped.width, cropped.height) == (32, 20)
    assert cropped.origin == {"kind": "derived", "parent": original.id, "operation": "crop [0, 0, 32, 20]"}
    assert store.original_path(original.id).read_bytes() == data


def test_a_deleted_session_leaves_references_that_say_deleted(tmp_path):
    store = AttachmentStore(tmp_path)
    attachment = store.add_bytes(_png_with_alpha(), "x.png", origin={"kind": "user_message"}, session="s1")
    store.delete_session("s1")

    with pytest.raises(AttachmentGone, match="was deleted"):
        store.get(attachment.id)


def test_broken_and_oversized_images_are_refused_with_the_reason(tmp_path):
    store = AttachmentStore(tmp_path)
    with pytest.raises(Exception, match="not a readable image"):
        store.add_bytes(b"not an image", "x.png", origin={"kind": "user_message"}, session="s1")
    with pytest.raises(Exception, match="between 1 byte"):
        store.add_bytes(b"x" * (6 * 1024 * 1024), "x.png", origin={"kind": "user_message"}, session="s1")


def test_a_user_image_becomes_an_attachment_and_its_bytes_never_reach_the_goal(agent_factory, tmp_path):
    import base64
    import json

    agent = agent_factory(max_steps=2)
    agent.client.content = "A red square."
    agent._call_action_model = lambda prompt: "It is a red square."
    url = "data:image/png;base64," + base64.b64encode(_png_with_alpha()).decode("ascii")
    goal = json.dumps({"type": "kvist-image-goal", "text": "what is this", "images": [{"name": "a.png", "data_url": url}]})

    agent.run(goal, routed_role="reader")

    stored = AttachmentStore(agent.executor.workspace).list(agent.current_session)
    assert len(stored) == 1 and stored[0].has_alpha
    assert stored[0].id in agent._state.user_goal
    assert "base64" not in agent._state.user_goal


def test_vision_unavailable_is_an_error_not_an_analysis(agent_factory, tmp_path):
    from kernel.plugins.host import ServiceError
    from kernel.plugins.services import AttachedSkill, SkillCall
    from kernel.tests.skill_fixtures import install, write_skill
    from kernel.plugins.registry import SkillRegistry

    agent = agent_factory(provider="freeflow")  # its wrapper accepts no images
    registry = SkillRegistry(tmp_path / "skills")
    install(registry, write_skill(tmp_path / "src", capabilities=["images.analyze"]))
    entry = registry.entry("echo")
    grant = agent.skills.grants.put("echo", entry.digest, agent.executor.workspace, {"images.analyze": {}})
    attachment = AttachmentStore(agent.executor.workspace).add_bytes(
        _png_with_alpha(), "x.png", origin={"kind": "user_message"}, session=agent.current_session)
    call = SkillCall(AttachedSkill(entry, grant, 0.0), entry.manifest.tools[0], "c1", "r1", agent.current_session)

    with pytest.raises(ServiceError) as error:
        agent.skills.services.handle(call, "images.analyze", {"ids": [attachment.id], "question": "what?"})
    assert error.value.code == "unavailable"


# --- questions ---------------------------------------------------------------


class QuestionUI(NullUI):
    def __init__(self, choice=None, text=None):
        self.choice, self.text = choice, text

    def ask_choice(self, title, options):
        return self.choice

    def prompt_text(self, title, current_value="", help_text=""):
        return self.text


@pytest.mark.parametrize(
    ("interactive", "choice", "text", "status"),
    [
        (True, "blue", None, "answered"),
        (True, "__kvist_skip__", None, "skipped"),
        (True, None, None, "cancelled"),
        (True, "__kvist_other__", "", "skipped"),
        (True, "__kvist_other__", "green", "answered"),
        (False, "blue", None, "no_interactive_user"),
    ],
)
def test_ask_user_tells_an_answer_a_skip_a_cancel_and_nobody_apart(agent_factory, interactive, choice, text, status):
    from kernel.agent_tools import run_agent_tool
    from kernel.protocol import Action, AgentCommand

    agent = agent_factory(interactive=interactive)
    object.__setattr__(agent._ui, "_ui", QuestionUI(choice, text))
    agent._state.response_language = "en"
    command = AgentCommand("inspect", Action("ask_user", {"question": "Which colour?", "options": ["blue", "red"],
                                                          "allow_text": True}))

    result = run_agent_tool(agent, command)

    assert result["data"]["status"] == status
    if status == "answered":
        assert agent._state.task_updates[-1].endswith(result["data"]["answer"])


# --- resources ---------------------------------------------------------------


def test_cleanup_stops_a_tree_and_reports_it(tmp_path, trusted_python_path):
    manager = ResourceManager(tmp_path, runtime_root=tmp_path / "rt")
    (tmp_path / "rt").mkdir()
    process = ManagedProcess([str(trusted_python_path), "-c", "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)"],
                             label="server", env={"PATH": "/usr/bin:/bin"})
    resource = manager.spawn(process, Owner(run_id="r1"))
    pid = resource.process.pid

    report = manager.cleanup(Owner(run_id="r1"), "task_finished")

    assert report.ok and report.outcomes[0].label == "server"
    assert not os.path.exists(f"/proc/{pid}")
    assert "server" in report.summary("en")


def test_recovery_never_kills_a_process_whose_pid_was_reused(tmp_path):
    from kernel.durable_json import write_json_object

    registry = tmp_path / ".kvist" / "resources.json"
    registry.parent.mkdir()
    write_json_object(registry, {"resources": [{
        "id": "proc_x", "kind": "process", "label": "old server", "core_pid": 999_999_999, "core_start": 1,
        "pid": os.getpid(), "start_time": 1, "cgroup": "",
    }]}, boundary=tmp_path)

    report = ResourceManager(tmp_path).recover()

    assert report.outcomes[0].stopped  # forgotten, not killed: this test process is still alive
    assert os.path.exists(f"/proc/{os.getpid()}")


def test_a_cleanup_that_cannot_be_confirmed_is_reported_as_such(tmp_path):
    manager = ResourceManager(tmp_path)
    manager.register("logical", "stuck browser", Owner(run_id="r1"), closer=lambda: "did not answer")

    report = manager.cleanup(Owner(run_id="r1"), "task_finished")

    assert not report.ok
    assert "could not confirm" in report.summary("en") and "stuck browser" in report.summary("en")


# --- network policy ----------------------------------------------------------


def _resolver(mapping):
    return lambda name, port: mapping.get(name, [])


@pytest.mark.parametrize(
    ("host", "port", "domains", "resolved", "reason"),
    [
        ("10.0.0.5", 80, ["*"], {}, "private or local"),
        ("169.254.169.254", 80, ["*"], {}, "private or local"),
        ("rebind.example", 80, ["rebind.example"], {"rebind.example": ["127.0.0.1"]}, "non-public"),
        ("other.example", 443, ["docs.example"], {}, "not among"),
        ("localhost", 3000, ["*"], {}, "no server of this task"),
    ],
)
def test_the_broker_refuses_local_private_and_unlisted_destinations(host, port, domains, resolved, reason):
    with pytest.raises(Denied, match=reason):
        decide(host, port, NetPolicy(allow_services=True, domains=domains), TaskServices(), _resolver(resolved))


def test_the_broker_routes_a_task_server_and_a_listed_public_host():
    services = TaskServices()
    services.add(TaskService(port=3000, label="dev", unix_path="/run/x/service.sock"))
    policy = NetPolicy(allow_services=True, domains=["*.example.org"])

    assert decide("localhost", 3000, policy, services).target == "/run/x/service.sock"
    route = decide("docs.example.org", 443, policy, services, _resolver({"docs.example.org": ["93.184.216.34"]}))
    assert route.target == "93.184.216.34"


class _Redirect(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server naming
        self.send_response(302)
        self.send_header("Location", "http://10.1.2.3/secret")
        self.end_headers()

    def log_message(self, *args):
        return


def test_a_redirect_cannot_carry_a_request_to_a_refused_destination():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    services = TaskServices()
    services.add(TaskService(port=4321, label="dev", tcp_port=server.server_address[1]))
    try:
        with pytest.raises(HttpPolicyError, match="private or local"):
            request(method="GET", url="http://localhost:4321/", headers={}, body=None, json_body=None,
                    timeout=5, domains=[], services=services)
    finally:
        server.shutdown()


# --- screen viewer -----------------------------------------------------------


def _xwd(path, width=8, height=4):
    header = struct.pack(">25I", 100, 7, 2, 24, width, height, 0, 0, 32, 0, 32, 32, width * 4, 4,
                         0xFF0000, 0xFF00, 0xFF, 8, 256, 0, width, height, 0, 0, 0)
    path.write_bytes(header + b"\x10\x20\x30\x00" * width * height)


def test_the_viewer_needs_its_token_and_its_own_host_name(tmp_path):
    _xwd(tmp_path / "Xvfb_screen0")
    viewer = ScreenViewer(tmp_path, title="Test")
    viewer.start()
    try:
        frame = urllib.request.urlopen(viewer.frame_url, timeout=5)
        assert frame.headers["Content-Type"] == "image/jpeg" and frame.headers["Cache-Control"] == "no-store"
        with pytest.raises(urllib.error.HTTPError) as anonymous:
            urllib.request.urlopen(f"{viewer.base}/frame.jpg", timeout=5)
        assert anonymous.value.code == 401
        rebound = urllib.request.Request(viewer.frame_url, headers={"Host": f"evil.example:{viewer.port}"})
        with pytest.raises(urllib.error.HTTPError) as foreign:
            urllib.request.urlopen(rebound, timeout=5)
        assert foreign.value.code == 403
        assert viewer.base.startswith("http://127.0.0.1:")
    finally:
        viewer.close()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", viewer.port), timeout=1)


# --- connections ---------------------------------------------------------------


def test_a_skill_reaches_only_connections_granted_by_name_and_never_sees_their_secrets(agent_factory, tmp_path, monkeypatch):
    from kernel.plugins.connections import save_connection
    from kernel.plugins.host import ServiceError
    from kernel.plugins.registry import SkillRegistry
    from kernel.plugins.services import AttachedSkill, SkillCall
    from kernel.tests.skill_fixtures import install, write_skill

    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path / "config"))
    save_connection("crm", {"base_url": "https://crm.example.com/api", "headers": {"Authorization": "Bearer s3cret"},
                            "methods": ["GET"]})
    save_connection("billing", {"base_url": "https://billing.example.com/", "headers": {"X-Key": "k"}})
    agent = agent_factory()
    registry = SkillRegistry(tmp_path / "skills")
    install(registry, write_skill(tmp_path / "src", capabilities=[{"name": "connections.use", "scope": {"connections": ["crm"]}}]))
    entry = registry.entry("echo")
    grant = agent.skills.grants.put("echo", entry.digest, agent.executor.workspace,
                                    entry.manifest.required_capabilities())
    call = SkillCall(AttachedSkill(entry, grant, 0.0), entry.manifest.tools[0], "c1", "r1", agent.current_session)

    listed = agent.skills.services.handle(call, "connections.list", {})

    assert [item["name"] for item in listed] == ["crm"]
    assert "s3cret" not in str(listed)
    with pytest.raises(ServiceError, match="may not use a connection named 'billing'"):
        agent.skills.services.handle(call, "connections.request", {"connection": "billing", "method": "GET", "path": "/"})
    with pytest.raises(ServiceError, match="reaches only https://crm.example.com"):
        agent.skills.services.handle(call, "connections.request",
                                     {"connection": "crm", "method": "GET", "path": "https://evil.example.org/x"})
    with pytest.raises(ServiceError, match="read-only"):
        agent.skills.services.handle(call, "connections.request", {"connection": "crm", "method": "POST", "path": "/x"})


def test_runtime_directory_is_private_unique_and_thread_safe(tmp_path, monkeypatch):
    import stat
    from concurrent.futures import ThreadPoolExecutor
    import kernel.resources as resources

    monkeypatch.setattr(resources.tempfile, "tempdir", str(tmp_path))
    manager = ResourceManager(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        roots = list(pool.map(lambda _: manager.runtime_dir(), range(32)))
    assert len(set(roots)) == 1
    assert stat.S_IMODE(roots[0].stat().st_mode) == 0o700
    other = ResourceManager(tmp_path).runtime_dir()
    assert other != roots[0]


def test_runtime_directory_ignores_precreated_xdg_symlink(tmp_path, monkeypatch):
    import kernel.resources as resources

    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    fake_runtime = tmp_path / "runtime"
    fake_runtime.symlink_to(victim, target_is_directory=True)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(fake_runtime))
    monkeypatch.setattr(resources.tempfile, "tempdir", str(tmp_path))
    root = ResourceManager(tmp_path).runtime_dir()
    assert root.parent == tmp_path
    assert not list(victim.iterdir())
