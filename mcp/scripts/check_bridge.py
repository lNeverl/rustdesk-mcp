"""Local executable acceptance checks; no remote connection or real credentials."""
import argparse
import asyncio
import ctypes
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from rustdesk_mcp.bridge import Bridge
from rustdesk_mcp.errors import ToolError


async def launch(executable):
    process = await asyncio.create_subprocess_exec(str(executable), "--mcp-bridge",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    ready = json.loads(await asyncio.wait_for(process.stdout.readline(), 30))
    assert ready["type"] == "ready" and ready["protocol"]["major"] == 1
    return process, ready


async def check(executable):
    process, ready = await launch(executable)
    process.stdin.close()
    assert await asyncio.wait_for(process.wait(), 5) == 0
    print("Native ready / parent stdin EOF: PASS")

    process, ready = await launch(executable)
    reader, writer = await asyncio.open_connection("127.0.0.1", ready["port"])
    writer.write(b'{"jsonrpc":"2.0","id":1,"method":"hello","params":{"token":"wrong","major":1}}\n')
    await writer.drain()
    response = json.loads(await asyncio.wait_for(reader.readline(), 5))
    assert response["error"]["data"]["code"] == "authentication_failed"
    assert await asyncio.wait_for(process.wait(), 5) == 0
    writer.close()
    print("Invalid local token refused: PASS")

    bridge = Bridge(executable)
    await bridge.start()
    try:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "RustDesk2.toml"
            for content, code in (("unlock_pin = 'configured'\n", "official_application_locked"),
                                  ("[options]\n", "official_credential_unavailable")):
                path.write_text(content, "utf-8")
                try:
                    await bridge.rpc("connect", peer_id="123", config_source="official", official_config_dir=directory)
                    raise AssertionError("Expected clean refusal")
                except ToolError as exc:
                    assert exc.code == code, exc.code
                assert path.read_text("utf-8") == content
        print("Official PIN / unavailable credential / readonly files: PASS")
        bridge.writer.close()
        assert await asyncio.wait_for(bridge.process.wait(), 5) == 0
        print("Control socket EOF: PASS")
    finally:
        await bridge.close()

    child_code = """
import asyncio,sys
from pathlib import Path
from rustdesk_mcp.bridge import Bridge
async def main():
    bridge = Bridge(Path(sys.argv[1]))
    await bridge.start()
    print(bridge.process.pid, flush=True)
    await asyncio.Event().wait()
asyncio.run(main())
"""
    parent = await asyncio.create_subprocess_exec(sys.executable, "-c", child_code, str(executable),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        pid = int(await asyncio.wait_for(parent.stdout.readline(), 30))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        assert handle, "Unable to track owned bridge"
        try:
            parent.kill()
            await parent.wait()
            assert kernel.WaitForSingleObject(handle, 5000) == 0
        finally:
            kernel.CloseHandle(handle)
        print("Abrupt parent exit kills owned bridge: PASS")
    finally:
        if parent.returncode is None:
            parent.kill()
            await parent.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bridge-exe", type=Path, required=True)
    asyncio.run(check(parser.parse_args().bridge_exe.resolve()))
