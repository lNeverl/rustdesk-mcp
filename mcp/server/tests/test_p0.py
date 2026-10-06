import asyncio
import base64
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from rustdesk_mcp.config import Config
from rustdesk_mcp.engine import Engine
from rustdesk_mcp.errors import ToolError
from rustdesk_mcp.framing import Parser
from rustdesk_mcp.journal import Journal


class FramingTest(unittest.TestCase):
    def test_fragmented_unicode_input_echo_and_result(self):
        parser = Parser("example")
        metadata = {"powershell_success": True, "native_exit_code": 7, "cwd": "C:\\测试"}
        expected = "中文😀" * 100 + "\n"
        frame = "echo of encoded payload\x1b[0m\r\n__RDMCP_example_BEGIN__\r\n"
        sequence=0
        for kind, payload in (("DATA", expected.encode()), ("META", json.dumps(metadata).encode())):
            encoded = base64.b64encode(payload).decode()
            for at in range(0,len(encoded),64):
                frame+=f"\x1b[0m__RDMCP_example_{kind}__:{sequence}:{encoded[at:at+64]}\r\n"
                sequence+=1
        frame = (frame + "__RDMCP_example_END__\r\nPS>").encode()
        output, result = "", None
        for byte in frame:
            text, completed = parser.feed(bytes([byte]))
            output += text
            result = completed or result
        self.assertEqual(output, expected)
        self.assertEqual(result, metadata)

    def test_missing_record_is_stream_loss(self):
        with self.assertRaisesRegex(ToolError,'stream_lost'):
            Parser('test').feed(b'__RDMCP_test_BEGIN__\n__RDMCP_test_DATA__:1:dGVzdA==\n')


class ConfigTest(unittest.TestCase):
    def test_sources_and_password_override(self):
        config = Config(Path("bridge.exe"), Path("data"), profiles={
            "local": {"peer_id": "123", "server": "custom", "server_key": "custom-key", "password_dpapi": "protected"}
        }, config_source="official")
        self.assertEqual(config.credentials("1 23", None, None, False),
                         {"peer_id": "123", "elevate": False, "config_source": "official"})
        with patch("rustdesk_mcp.config.unprotect", return_value="secret"):
            official = config.credentials("123", "local", None, False)
            self.assertEqual(official["password"], "secret")
            self.assertNotIn("server", official)
            custom = config.credentials("123", "local", None, False, "profile")
            self.assertEqual(custom["server"], "custom")
            with self.assertRaisesRegex(ToolError, "credential_unavailable"):
                config.credentials("456", "local", None, False, "profile")


class EngineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.engine = Engine(Config(Path("missing.exe"), Path(self.temp.name) / "data", allow_writes=True))
        self.engine.session_id = "session"
        self.engine.peer_id = "123"
        self.engine.state = "connected"
        self.engine.bridge = SimpleNamespace(rpc=AsyncMock(return_value={"submitted": True}), failure=None, close=AsyncMock())

    async def asyncTearDown(self):
        self.engine.state = "disconnected"
        await self.engine.close()
        self.temp.cleanup()

    async def test_approval_and_retry_never_repeat_submission(self):
        initial = await self.engine.exec_command("session", "operation", "Write-Output 'test'", wait_timeout_s=0)
        self.assertEqual(initial["state"], "pending_approval")
        self.assertFalse(self.engine.bridge.rpc.called)
        pending = self.engine.web_snapshot()["command"]
        with self.assertRaisesRegex(ToolError, "approval_conflict"):
            await self.engine.decide("session", pending["command_id"], "wrong", True)
        await self.engine.decide("session", pending["command_id"], pending["digest"], True)
        again = await self.engine.exec_command("session", "operation", "Write-Output 'test'", wait_timeout_s=0)
        self.assertEqual(again["command_id"], initial["command_id"])
        self.assertEqual(again["state"], "running")
        self.assertEqual(self.engine.bridge.rpc.await_count, 1)
        with self.assertRaisesRegex(ToolError, "operation_conflict"):
            await self.engine.exec_command("session", "operation", "Remove-Item x", wait_timeout_s=0)
        with self.assertRaisesRegex(ToolError, "busy"):
            await self.engine.exec_command("session", "different", "diagnostic:disk", wait_timeout_s=0)
        self.engine.state = "disconnected"
        await self.engine.close()
        self.engine = Engine(Config(Path("missing.exe"), Path(self.temp.name) / "data"))
        recovered = await self.engine.get_command_output("session", initial["command_id"])
        self.assertEqual(recovered["state"], "unknown")

    async def test_readonly_blocks_free_powershell(self):
        self.engine.config.allow_writes = False
        with self.assertRaisesRegex(ToolError, "readonly_policy"):
            await self.engine.exec_command("session", "operation", "Get-Process", wait_timeout_s=0)
        accepted = await self.engine.exec_command("session", "diagnostic", "diagnostic:processes", wait_timeout_s=0)
        self.assertEqual(accepted["state"], "running")
        cancelled = await self.engine.cancel_command("session", accepted["command_id"])
        self.assertEqual(cancelled["state"], "unknown")
        self.assertFalse(cancelled["termination_confirmed"])


class ProtocolTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_handshake_and_tools(self):
        import sys

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        params = StdioServerParameters(command=sys.executable, args=["-m", "rustdesk_mcp.app", "--bridge-exe", "missing.exe", "--data-dir", temporary.name], env={"PYTHONUTF8": "1"})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                self.assertTrue({"connect", "disconnect", "status", "exec_command", "get_command_output", "cancel_command", "get_system_info", "read_file", "write_file", "list_processes", "list_sessions", "list_commands"}.issubset({tool.name for tool in tools}))
                self.assertTrue(all(tool.inputSchema and tool.outputSchema for tool in tools))
                invalid = await client.call_tool("status", {"session_id": "missing"})
                self.assertTrue(invalid.isError)


if __name__ == "__main__":
    unittest.main()
