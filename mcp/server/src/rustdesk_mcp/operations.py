import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ToolError
from .framing import Parser, wrapper


@dataclass
class Operation:
    row: dict
    execute: object
    review: dict
    digest: str
    done: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    output: str = ""
    metadata: dict = field(default_factory=dict)
    started: float = field(default_factory=time.monotonic)
    on_finish: object = None


class Operations:
    """One approved operation owns the session's terminal and file channel."""
    def __init__(self, engine):
        self.engine = engine
        self.active = None
        self.frame = None
        self.frame_done = None
        self.frame_result = None
        self.frame_output = ""
        self.file_ready = asyncio.Event()
        self.file_secured = False
        self.file_error = None
        self.file_job = None
        self.file_done = None
        self.file_result = None
        self.counter = 0

    def _persist(self):
        operation = self.active
        if operation:
            self.engine.journal.update(operation.row["command_id"], operation.row["state"],
                                       operation.output, operation.metadata)

    async def submit(self, session_id, operation_id, kind, params, execute, *, readonly=False,
                     review=None, wait_timeout_s=10, execution_timeout_s=300, on_finish=None):
        engine = self.engine
        if not 1 <= len(operation_id) <= 128 or not 0 <= wait_timeout_s <= 30 or not 1 <= execution_timeout_s <= 3600:
            raise ToolError("invalid_params")
        request = {"session_id":session_id,"kind":kind,"params":params,"execution_timeout_s":execution_timeout_s}
        async with engine.lock:
            existing, _, _ = engine.journal.lookup(operation_id, request)
            if existing:
                return await engine.get_command_output(session_id, existing["command_id"], wait_timeout_s=0)
            engine._require(session_id)
            if self.active or engine.active:
                raise ToolError("busy")
            if not readonly and not engine.config.allow_writes:
                raise ToolError("readonly_policy")
            row = engine.journal.create(operation_id, request, "submitting" if readonly else "pending_approval")
            binding = {"request":request,"review":review or params,"peer_id":engine.peer_id,
                       "identity":engine.snapshot.get("system_info",{}).get("identity"),
                       "sid":engine.snapshot.get("system_info",{}).get("sid")}
            digest = hashlib.sha256(json.dumps(binding,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            self.active = Operation(row, execute, binding, digest)
            self.active.on_finish = on_finish
            self.active.metadata = {"operation_kind":kind,"review":binding,"approval_digest":digest}
            self._persist()
            if readonly:
                self.start()
        return await engine.get_command_output(session_id,row["command_id"],wait_timeout_s=wait_timeout_s)

    def start(self):
        operation = self.active
        self.engine.journal.audit("operation_submitting", {"command_id":operation.row["command_id"],
            "session_id":self.engine.session_id,"kind":operation.metadata["operation_kind"],"digest":operation.digest})
        operation.row["state"] = "running"
        self._persist()
        operation.task = asyncio.create_task(self._run(operation))

    async def _run(self, operation):
        try:
            timeout = json.loads(operation.row["request"])["execution_timeout_s"]
            async with asyncio.timeout(timeout):
                data = await operation.execute(self)
            self.finish("completed",{"data":data})
        except asyncio.CancelledError:
            if self.active is operation:
                self.finish("unknown",{"reason":"cancelled_without_confirmation","termination_confirmed":False})
        except TimeoutError:
            await self.cancel("execution_timeout")
        except ToolError as exc:
            self.finish("unknown" if exc.code in {"stream_lost","submission_unknown","session_closed"} else "failed",
                        {"error":{"code":exc.code},"termination_confirmed":False})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            self.finish("failed",{"error":{"code":"operation_failed","type":type(exc).__name__}})

    def finish(self, state, metadata):
        operation = self.active
        if not operation:
            return
        operation.row["state"] = state
        operation.metadata.update(metadata,elapsed_ms=round((time.monotonic()-operation.started)*1000))
        if "data" in metadata:
            operation.output = json.dumps(metadata["data"],ensure_ascii=False,separators=(",",":"))
            if len(operation.output.encode()) > 8*1024*1024:
                operation.output=operation.output.encode()[:8*1024*1024].decode('utf-8',errors='ignore')
                operation.metadata['output_truncated']=True
            operation.metadata.pop("data",None)
            operation.metadata["result_encoding"] = "json"
        try:
            if operation.on_finish:
                try:
                    operation.on_finish()
                except OSError:
                    operation.metadata['local_cleanup_pending'] = True
            self._persist()
        finally:
            operation.done.set()
            self.active = None

    async def decide(self, command_id, digest, approve):
        operation = self.active
        if not operation or operation.row["command_id"] != command_id or operation.row["state"] != "pending_approval" or not secrets.compare_digest(digest,operation.digest):
            raise ToolError("approval_conflict")
        self.engine.journal.audit("approved" if approve else "denied",{"command_id":command_id,"digest":digest,"source":"local_web"})
        if approve:
            if not self.engine.config.allow_writes:
                raise ToolError("readonly_policy")
            self.start()
        else:
            self.finish("cancelled",{"reason":"approval_denied","not_submitted":True})
        return {"decided":True}

    async def powershell(self, script):
        if self.frame_done and not self.frame_done.is_set():
            raise ToolError("busy")
        nonce = secrets.token_hex(24)
        self.frame = Parser(nonce)
        self.frame_done = asyncio.Event()
        self.frame_output = ""
        self.frame_result = None
        await self.engine.bridge.rpc("terminal.write",session_id=self.engine.session_id,data=wrapper(script,nonce))
        await self.frame_done.wait()
        result = self.frame_result
        if not result:
            raise ToolError("stream_lost")
        if not result["powershell_success"] or result["native_exit_code"]:
            raise ToolError("remote_script_failed")
        return self.frame_output

    async def json_script(self, script):
        try:
            data = json.loads((await self.powershell(script)).strip())
        except ValueError:
            raise ToolError("invalid_remote_result") from None
        if isinstance(data,dict) and data.get("ok") is False:
            raise ToolError(data.get("error",{}).get("code","remote_error"))
        return data.get("data",data) if isinstance(data,dict) else data

    def feed_terminal(self,data):
        if not self.active or not self.frame or self.frame.finished:
            return
        try:
            output, metadata = self.frame.feed(base64.b64decode(data["data"],validate=True))
            if len((self.frame_output+output).encode()) > 8*1024*1024:
                raise ToolError("output_limit")
            self.frame_output += output
            self.active.output = self.frame_output
            if metadata:
                self.frame_result = metadata
                self.engine.snapshot.update(cwd=metadata.get("cwd"),shell_version=metadata.get("shell_version"))
                self.frame_done.set()
        except (ToolError,ValueError,KeyError,TypeError):
            self.abort("stream_lost")

    def feed_file(self,kind,data):
        if kind == "transport":
            self.file_secured = data.get("secured") is True
        elif kind == "authenticated":
            self.file_ready.set()
        elif kind == "challenge" and data.get("kind") not in {"success","info"}:
            self.file_error = "file_authentication_failed"
            self.file_ready.set()
            if self.file_done:
                self.file_done.set()
        elif kind in {'session_closed','terminal.error'}:
            self.file_error='file_channel_closed'
            self.file_ready.set()
            if self.file_done:self.file_done.set()
        if data.get("job_id") != self.file_job:
            return
        if kind == "files.progress" and self.active:
            self.active.metadata["transfer"] = data
        elif kind in {"files.done","files.error","files.overwrite_required"}:
            self.file_result = {"kind":kind,**data}
            if self.file_done:
                self.file_done.set()

    async def transfer(self,direction,local_path,remote_path):
        if not self.file_ready.is_set():
            self.file_error = None
            await self.engine.bridge.rpc("files.connect",session_id=self.engine.session_id)
            await asyncio.wait_for(self.file_ready.wait(),30)
        if self.file_error or not self.file_secured:
            raise ToolError(self.file_error or "insecure_file_channel")
        self.counter += 1
        self.file_job = self.counter
        self.file_done = asyncio.Event()
        self.file_result = None
        await self.engine.bridge.rpc("files."+direction,session_id=self.engine.session_id,
            job_id=self.file_job,local_path=str(local_path),remote_path=remote_path)
        await self.file_done.wait()
        result = self.file_result
        if not result or result["kind"] != "files.done":
            raise ToolError("file_overwrite_conflict" if result and result["kind"]=="files.overwrite_required" else "file_transfer_failed")
        return result

    def abort(self,reason):
        operation = self.active
        if operation:
            self.engine.state='failed'
            if operation.task and operation.task is not asyncio.current_task():
                operation.task.cancel()
            self.finish("unknown",{"reason":reason,"termination_confirmed":False})
            if not self.engine.closed.is_set():
                asyncio.create_task(self._close_failed_terminal())
        if self.frame_done:
            self.frame_done.set()
        if self.file_done:
            self.file_done.set()

    async def _close_failed_terminal(self):
        try:
            await self.engine.bridge.rpc('terminal.close',session_id=self.engine.session_id)
        except ToolError:
            self.engine.remote_error='stream_lost'

    async def cancel(self,reason):
        operation = self.active
        if not operation:
            return
        if operation.row["state"] == "pending_approval":
            self.finish("cancelled",{"reason":reason,"not_submitted":True,"termination_confirmed":False})
            return
        operation.row["state"] = "cancelling"
        try:
            self._persist()
        except ToolError:
            operation.metadata['audit_storage_unavailable']=True
        try:
            if self.file_job:
                await self.engine.bridge.rpc("files.cancel",session_id=self.engine.session_id,job_id=self.file_job)
                await self.engine.bridge.rpc("files.disconnect",session_id=self.engine.session_id)
            await self.engine.bridge.rpc("terminal.close",session_id=self.engine.session_id)
            await asyncio.wait_for(self.engine.closed.wait(),3)
            confirmed = True
        except (ToolError,TimeoutError):
            confirmed = False
        if operation.task and operation.task is not asyncio.current_task():
            operation.task.cancel()
        self.finish("cancelled" if confirmed else "unknown",{"reason":reason,"termination_confirmed":confirmed,
                    "transfer_cancel_requested":self.file_job is not None,"detached_children_may_remain":True})
        self.engine.state = "closed" if confirmed else "failed"

    def snapshot(self):
        operation = self.active
        if not operation:
            return None
        return {"command_id":operation.row["command_id"],"state":operation.row["state"],"digest":operation.digest,
                "request":operation.review["request"],"review":operation.review,"output_tail":operation.output[-8192:],
                "transfer":operation.metadata.get("transfer"),"elapsed_ms":round((time.monotonic()-operation.started)*1000)}


def script(resource,params):
    content = (Path(__file__).parent / "scripts" / resource).read_text("utf-8")
    encoded = base64.b64encode(json.dumps(params,ensure_ascii=False,separators=(",",":")).encode()).decode()
    return "& { param($rd)\n"+content+"\n} ([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+encoded+"')) | ConvertFrom-Json)"
