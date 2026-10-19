"""Minimal stdio MCP server for natural-language scenario authoring."""

from __future__ import annotations

import json
import sys
from typing import Any, Dict, Optional, TextIO

from monkeylm.scenario import parse_scenario_text, scenario_to_dict

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "monkeylm-scenario", "version": "0.1.0"}
TOOLS = [
    {
        "name": "create_test_scenario",
        "description": (
            "Convert a plain-English test scenario into a structured MonkeyLM "
            "scenario with personas, flows, edge cases, and security focus."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Natural-language scenario to convert.",
                },
                "name": {
                    "type": "string",
                    "description": "Optional scenario display name.",
                },
            },
            "required": ["text"],
        },
    }
]


def _result(request_id: Any, result: Dict[str, Any]) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_response(text: str, is_error: bool = False) -> Dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def handle_request(request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC request; return None for notifications."""
    if not isinstance(request, dict):
        return _error(None, -32600, "Invalid Request")
    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str):
        return _error(request_id, -32600, "Invalid Request")
    if "id" not in request:
        return None

    if method == "initialize":
        return _result(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name != "create_test_scenario":
            return _error(request_id, -32602, f"Unknown tool: {name}")
        if not isinstance(arguments, dict):
            return _error(request_id, -32602, "Tool arguments must be an object")
        text = str(arguments.get("text") or "").strip()
        if not text:
            return _error(request_id, -32602, "Missing required argument: text")
        name_value = str(arguments.get("name") or "").strip()
        try:
            strategy = parse_scenario_text(text, name=name_value)
            payload = json.dumps(scenario_to_dict(strategy), indent=2, ensure_ascii=False)
        except ValueError as exc:
            return _result(request_id, _tool_response(str(exc), is_error=True))
        return _result(request_id, _tool_response(payload))

    return _error(request_id, -32601, f"Method not found: {method}")


def serve(stdin: TextIO, stdout: TextIO) -> None:
    for raw_line in stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _error(None, -32700, f"Parse error: {exc.msg}")
        else:
            response = handle_request(request)
        if response is not None:
            stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            stdout.flush()


def main() -> None:
    serve(sys.stdin, sys.stdout)


if __name__ == "__main__":
    main()
