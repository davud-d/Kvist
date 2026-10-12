"""A tiny stdio MCP server for tests: echo, a slow tool, and a tool list that can change."""
import json
import sys
import time

TOOLS = [
    {"name": "echo", "description": "Echo the text.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "slow-op", "description": "Takes a while.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "grow", "description": "Adds a tool to the list.", "inputSchema": {"type": "object", "properties": {}}},
]


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method, request_id = message.get("method"), message.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"protocolVersion": "2025-06-18", "capabilities": {"tools": {"listChanged": True}}, "serverInfo": {"name": "echo", "version": "1"}}})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = message["params"]["name"]
        if name == "echo":
            send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "echo: " + message["params"]["arguments"]["text"]}]}})
        elif name == "slow-op":
            time.sleep(30)
        elif name == "grow":
            TOOLS.append({"name": "new_power", "description": "Unreviewed.", "inputSchema": {"type": "object"}})
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            send({"jsonrpc": "2.0", "id": request_id, "result": {"content": [{"type": "text", "text": "grown"}]}})
