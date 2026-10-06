import asyncio
import json
import subprocess
from pathlib import Path

from .errors import ToolError
from .windows import Job

MAX_FRAME = 2 * 1024 * 1024


class Bridge:
    def __init__(self, executable: Path):
        self.executable = executable
        self.events = asyncio.Queue(maxsize=256)
        self.pending = {}
        self.counter = 0
        self.process = None
        self.writer = None
        self.job = None
        self.tasks = []
        self.failure = None
        self.build_info = {}

    async def start(self):
        if not self.executable.is_file():
            raise ToolError("bridge_executable_missing")
        try:
            self.process = await asyncio.create_subprocess_exec(
                str(self.executable), "--mcp-bridge", stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
                limit=MAX_FRAME,
            )
            self.job = Job(self.process.pid)
            self.tasks.append(asyncio.create_task(self._drain_stderr()))
            async with asyncio.timeout(30):
                ready = json.loads(await self.process.stdout.readline())
            if ready.get("type") != "ready" or ready.get("protocol", {}).get("major") != 1:
                raise ToolError("bridge_protocol_mismatch")
            if not 1 <= ready["port"] <= 65535 or "terminal" not in ready.get("capabilities", []):
                raise ToolError("bridge_protocol_mismatch")
            self.build_info = {key: ready.get(key) for key in ("protocol", "bridge_version", "rustdesk_version", "source_revision", "instance_id", "capabilities")}
            self.reader, self.writer = await asyncio.open_connection("127.0.0.1", ready["port"], limit=MAX_FRAME)
            self.tasks.append(asyncio.create_task(self._read()))
            await self.rpc("hello", token=ready["token"], major=1)
        except Exception:
            await self.close()
            raise ToolError("bridge_start_failed") from None

    async def _drain_stderr(self):
        while await self.process.stderr.read(8192):
            pass

    async def _read(self):
        try:
            while True:
                frame = await self.reader.readline()
                if not frame or len(frame) > MAX_FRAME:
                    raise ToolError("stream_lost")
                message = json.loads(frame)
                if message.get("jsonrpc") != "2.0":
                    raise ToolError("stream_lost")
                if "id" in message:
                    future = self.pending.pop(message["id"], None)
                    if future and not future.done():
                        if "error" in message:
                            future.set_exception(ToolError(message["error"].get("data", {}).get("code", "bridge_error")))
                        else:
                            future.set_result(message["result"])
                elif message.get("method") == "event":
                    await self.events.put(message["params"])
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.failure = "stream_lost"
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(ToolError("stream_lost"))
            self.pending.clear()
            # Loss invalidates every queued event; never resume parsing a partial command.
            while not self.events.empty():
                self.events.get_nowait()
            self.events.put_nowait({"type": "stream_lost", "data": {"stage":"bridge_reader","reason":type(exc).__name__}})
            if self.writer:
                self.writer.close()

    async def rpc(self, method: str, **params):
        if self.failure or not self.writer or self.writer.is_closing():
            raise ToolError("stream_lost")
        self.counter += 1
        request_id = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        frame = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}).encode() + b"\n"
        if len(frame) > MAX_FRAME:
            self.pending.pop(request_id)
            raise ToolError("frame_too_large")
        try:
            self.writer.write(frame)
            await self.writer.drain()
            return await asyncio.wait_for(future, 10)
        except (OSError, TimeoutError):
            raise ToolError("submission_unknown") from None
        finally:
            self.pending.pop(request_id, None)

    async def close(self):
        if self.writer:
            self.writer.close()
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                self.process.kill()
                await self.process.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.job:
            self.job.close()
