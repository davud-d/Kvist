from __future__ import annotations

from kernel.protocol.protocol import Action, AgentCommand


def test_protocol_dataclasses_store_action_payload():
    command = AgentCommand(
        plan_step="inspect",
        action=Action(type="read_file", args={"path": "main.py"}),
        text="Читаю main.py",
        call_id="call_1",
        batch_id="batch_1",
    )

    assert command.action.type == "read_file"
    assert command.action.args == {"path": "main.py"}
    assert (command.text, command.call_id, command.batch_id) == ("Читаю main.py", "call_1", "batch_1")
    assert not hasattr(command, "thought") and not hasattr(command, "user_message")
