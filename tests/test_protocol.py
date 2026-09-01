from __future__ import annotations

from protocol import Action, AgentCommand


def test_protocol_dataclasses_store_action_payload():
    command = AgentCommand(
        thought="think",
        plan_step="inspect",
        user_message="message",
        action=Action(type="read_file", args={"path": "main.py"}),
    )

    assert command.action.type == "read_file"
    assert command.action.args == {"path": "main.py"}
