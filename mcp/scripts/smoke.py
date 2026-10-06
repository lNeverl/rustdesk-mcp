"""Exercise the real MCP stdio transport and optionally an authorized remote peer."""
import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def result(reply):
    if reply.isError:
        raise RuntimeError("; ".join(item.text for item in reply.content if item.type == "text"))
    if reply.structuredContent is not None:
        return reply.structuredContent
    return json.loads(next(item.text for item in reply.content if item.type == "text"))


async def run(args):
    command = ["-m", "rustdesk_mcp.app", "--bridge-exe", str(args.bridge_exe.resolve())]
    if args.config:
        command.extend(["--config", str(args.config.resolve())])
    if args.data_dir:
        command.extend(["--data-dir", str(args.data_dir.resolve())])
    parameters = StdioServerParameters(command=sys.executable, args=command, env={"PYTHONUTF8": "1"})
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as client:
            await client.initialize()
            tools = (await client.list_tools()).tools
            assert {"connect","exec_command","read_file","upload_file","list_processes","exec_script","list_commands"}.issubset({t.name for t in tools})
            print(f"MCP handshake and {len(tools)} tools: PASS")
            if not args.peer_id:
                return
            connected = result(await client.call_tool("connect", {"peer_id": args.peer_id, "credential_ref": args.credential_ref,
                                                                   "config_source": args.config_source}))
            session_id = connected["session_id"]
            if args.config_source:
                assert connected["config_source"] == args.config_source
            print("Headless connect, authenticate, terminal and identity probe: PASS")
            print("Remote RustDesk version:", connected.get("peer", {}).get("version", "unknown"))
            try:
                info = result(await client.call_tool("get_system_info", {"session_id": session_id}))
                print(json.dumps({key: info.get(key) for key in ("os", "build", "shell_version", "administrator")}, ensure_ascii=False))
                params = {"session_id": session_id, "operation_id": "smoke:" + str(uuid.uuid4()),
                          "command": "diagnostic:unicode", "wait_timeout_s": 30}
                output = result(await client.call_tool("exec_command", params))
                assert output["state"] == "completed", output["state"]
                assert "中文输出验证 😀" in output["output"], "UTF-8 output mismatch"
                repeated = result(await client.call_tool("exec_command", params))
                assert repeated["command_id"] == output["command_id"]
                print("UTF-8 command output and stable operation retry: PASS")
                offset, pages = 0, []
                while True:
                    page = result(await client.call_tool("get_command_output", {"session_id": session_id,
                        "command_id": output["command_id"], "offset": offset, "max_chars": 7}))
                    pages.append(page["output"])
                    offset = page["next_offset"]
                    if not page["has_more"]:
                        break
                assert "".join(pages) == output["output"]
                cancelled = result(await client.call_tool("cancel_command", {"session_id": session_id,
                                                                            "command_id": output["command_id"]}))
                assert not cancelled["accepted"] and cancelled["state"] == "completed"
                blocked = await client.call_tool("exec_command", {"session_id": session_id,
                    "operation_id": "blocked:" + str(uuid.uuid4()), "command": "Get-Date"})
                assert blocked.isError and any("readonly_policy" in item.text for item in blocked.content if item.type == "text")
                print("Unicode pagination / completed-command cancellation / readonly policy: PASS")
                status = result(await client.call_tool("status", {"session_id": session_id}))
                assert status["state"] == "connected"
            finally:
                closed = result(await client.call_tool("disconnect", {"session_id": session_id}))
                print("Disconnect:", json.dumps(closed))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-exe", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--peer-id")
    parser.add_argument("--credential-ref")
    parser.add_argument("--config-source", choices=("profile", "official"))
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
