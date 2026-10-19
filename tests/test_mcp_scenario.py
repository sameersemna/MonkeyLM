from __future__ import annotations

import io
import json
import unittest

from monkeylm.mcp_scenario import PROTOCOL_VERSION, handle_request, serve


class McpScenarioServerTests(unittest.TestCase):
    def test_initialize_advertises_tools(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})

        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertIn("tools", response["result"]["capabilities"])

    def test_tools_list_exposes_scenario_tool(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})

        tools = response["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "create_test_scenario")
        self.assertEqual(tools[0]["inputSchema"]["required"], ["text"])

    def test_tools_call_returns_structured_scenario(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "create_test_scenario",
                    "arguments": {
                        "text": (
                            "As an admin, create a user, then edit and delete it. "
                            "Try empty fields and XSS."
                        ),
                        "name": "user admin",
                    },
                },
            }
        )

        self.assertFalse(response["result"]["isError"])
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["name"], "user admin")
        self.assertEqual(payload["personas"][0]["name"], "admin")
        self.assertGreaterEqual(len(payload["flows"]), 2)
        self.assertTrue(payload["edge_cases"])
        self.assertTrue(payload["security_focus"])

    def test_tools_call_rejects_missing_text(self) -> None:
        response = handle_request(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "create_test_scenario", "arguments": {}},
            }
        )

        self.assertEqual(response["error"]["code"], -32602)

    def test_unknown_method_is_an_error(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "id": 5, "method": "unknown"})
        self.assertEqual(response["error"]["code"], -32601)

    def test_notifications_get_no_response(self) -> None:
        response = handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertIsNone(response)

    def test_serve_processes_json_lines_and_parse_errors(self) -> None:
        stdin = io.StringIO(
            '{"jsonrpc": "2.0", "id": 1, "method": "initialize"}\n'
            "{not valid json}\n"
            '{"jsonrpc": "2.0", "method": "notifications/initialized"}\n'
        )
        stdout = io.StringIO()
        serve(stdin, stdout)

        lines = [json.loads(line) for line in stdout.getvalue().splitlines()]
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["result"]["protocolVersion"], PROTOCOL_VERSION)
        self.assertEqual(lines[1]["error"]["code"], -32700)


if __name__ == "__main__":
    unittest.main()
