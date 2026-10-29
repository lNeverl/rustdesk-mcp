import asyncio
import base64
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field

from .bridge import Bridge
from .config import Config, private_directory
from .errors import ToolError
from .framing import DIAGNOSTICS, Parser, SYSTEM_INFO, wrapper
from .journal import Journal
from .operations import Operations

OUTPUT_LIMIT = 8 * 1024 * 1024

log = logging.getLogger(__name__)


@dataclass
class Running:
    row: dict
    script: str
    nonce: str = field(default_factory=lambda: secrets.token_hex(24))
    done: asyncio.Event = field(default_factory=asyncio.Event)
    output: str = ""
    output_bytes: int = 0
    metadata: dict = field(default_factory=dict)
    timer: asyncio.Task | None = None

    def __post_init__(self):
        self.parser = Parser(self.nonce)


class Engine:
    def __init__(self, config: Config, journal=None):
        self.config = config
        private_directory(config.data_dir)
        self.owns_journal = journal is None
        self.journal = journal or Journal(config.data_dir / "operations.sqlite3",config.storage_budget_bytes)
        self.operations = Operations(self)
        self.bridge = None
        self.event_task = None
        self.lock = asyncio.Lock()
        self.session_id = None
        self.peer_id = None
        self.state = "disconnected"
        self.active: Running | None = None
        self.authenticated = asyncio.Event()
        self.opened = asyncio.Event()
        self.closed = asyncio.Event()
        self.snapshot = {}
        self.remote_error = None

    def _require(self, session_id: str, *, connecting=False):
        if session_id != self.session_id or self.state not in ({"connected", "connecting"} if connecting else {"connected"}):
            raise ToolError("session_closed")

    async def _ensure_bridge(self):
        if self.bridge and not self.bridge.failure:
            return
        if self.bridge:
            await self.bridge.close()
        if self.event_task:
            self.event_task.cancel()
            await asyncio.gather(self.event_task, return_exceptions=True)
        self.bridge = Bridge(self.config.bridge_exe)
        await self.bridge.start()

    async def connect(self, peer_id, credential_ref=None, admin_credential_ref=None, elevate=False, config_source=None, *, owned_session_id=None, elevation_approved=False):
        if elevate and not elevation_approved:
            raise ToolError("elevation_approval_required")
        async with self.lock:
            if (self.state in {"connecting", "connected"} and not (self.state=='connecting' and owned_session_id==self.session_id)) or self.active:
                raise ToolError("busy")
            credentials = self.config.credentials(peer_id, credential_ref, admin_credential_ref, elevate, config_source)
            if owned_session_id:
                credentials["session_id"] = owned_session_id
            self.state='connecting'
            if self.event_task:
                self.event_task.cancel()
                await asyncio.gather(self.event_task, return_exceptions=True)
                self.event_task = None
            await self._ensure_bridge()
            if self.session_id:
                try:
                    await self.bridge.rpc("disconnect", session_id=self.session_id)
                except ToolError as exc:
                    if exc.code != "session_closed":
                        await self.bridge.close()
                        self.bridge = None
                        await self._ensure_bridge()
            self.authenticated.clear()
            self.opened.clear()
            self.closed.clear()
            self.remote_error = None
            self.snapshot = {"permissions": {}, "elevation_requested": elevate,
                             "config_source": credentials["config_source"],
                             "credential_source": "official" if "password" not in credentials else "profile"}
            self.snapshot["bridge"] = self.bridge.build_info
            result = await self.bridge.rpc("connect", **credentials)
            self.session_id = result["session_id"]
            self.peer_id = credentials["peer_id"]
            self.state = "connecting"
            self.event_task = asyncio.create_task(self._events_loop())
            self.journal.audit("connect_requested", {"peer_id": self.peer_id, "session_id": self.session_id, "elevate": elevate,
                                                     "config_source": self.snapshot["config_source"],
                                                     "credential_source": self.snapshot["credential_source"]})
        try:
            await asyncio.wait_for(self.authenticated.wait(), 30)
            if self.remote_error:
                raise ToolError(self.remote_error)
            if self.snapshot.get('peer',{}).get('platform')!='Windows':
                raise ToolError('unsupported_remote_platform')
            await self.bridge.rpc("terminal.open", session_id=self.session_id)
            await asyncio.wait_for(self.opened.wait(), 15)
            if self.remote_error:
                raise ToolError(self.remote_error)
            await self._probe(connecting=True)
            self.state = "connected"
            self.journal.audit("connected", {"session_id": self.session_id, "peer_id": self.peer_id,
                                           "identity": self.snapshot["system_info"].get("identity"),
                                           "sid": self.snapshot["system_info"].get("sid")})
            return self.status(self.session_id)
        except (ToolError, TimeoutError) as exc:
            await self.disconnect(self.session_id)
            raise ToolError(exc.code if isinstance(exc, ToolError) else "connect_timeout") from None
        except asyncio.CancelledError:
            await self.disconnect(self.session_id)
            raise

    def status(self, session_id: str):
        if session_id != self.session_id:
            raise ToolError("session_closed")
        return {"session_id": session_id, "peer_id": self.peer_id, "state": self.state,
                "terminal_id": 1, "active_command_id": self.active.row["command_id"] if self.active else self.operations.active.row["command_id"] if self.operations.active else None,
                "policy": "auto_approve" if self.config.auto_approve else ("confirm_writes" if self.config.allow_writes else "readonly"),
                "admin_credential_refs": [ref for ref, profile in self.config.admin_profiles.items()
                    if "".join(str(profile.get("peer_id", "")).split()) == self.peer_id
                    and profile.get("username") and profile.get("password_dpapi")],
                "optional_features":{"downloads_enabled":bool(self.config.downloads.get('enabled',True)),
                                     "intel_enabled":bool(self.config.intel.get('enabled'))},
                "capabilities": ["terminal", "powershell", "files", "diagnostics", "operation_journal", "scripts", "trusted_downloads"],
                **self.snapshot}

    async def disconnect(self, session_id: str):
        if session_id != self.session_id:
            raise ToolError("session_closed")
        if self.state == "disconnected":
            return {"session_id": session_id, "state": self.state, "cleanup_confirmed": self.closed.is_set()}
        self.state = "disconnecting"
        if self.operations.active:
            await self.operations.cancel("disconnect")
        if self.active:
            self._finish("cancelled" if self.active.row["state"] == "pending_approval" else "unknown",
                         {"reason": "disconnect", "termination_confirmed": False})
        confirmed = self.closed.is_set()
        if self.bridge and not self.bridge.failure and self.state != "disconnected":
            try:
                await self.bridge.rpc("terminal.close", session_id=session_id)
                await asyncio.wait_for(self.closed.wait(), 3)
                confirmed = True
            except (ToolError, TimeoutError):
                confirmed = False
            try:
                await self.bridge.rpc("disconnect", session_id=session_id)
            except ToolError:
                confirmed = False
        self.state = "disconnected"
        self.journal.audit("disconnect", {"session_id": session_id, "cleanup_confirmed": confirmed})
        return {"session_id": session_id, "state": self.state, "cleanup_confirmed": confirmed}

    def _persist(self):
        if self.active:
            command = self.active
            self.journal.update(command.row["command_id"], command.row["state"], command.output, command.metadata)

    def _finish(self, state: str, metadata: dict):
        if not self.active:
            return
        command = self.active
        command.row["state"] = state
        command.metadata.update(metadata)
        try:
            self._persist()
        except ToolError:
            self.state='failed'
            raise
        finally:
            if command.timer and command.timer is not asyncio.current_task():
                command.timer.cancel()
            command.done.set()
            self.active = None

    async def _events_loop(self):
        # A malformed event must not kill the loop: without a consumer the
        # bridge queue fills up and the session degrades into RPC timeouts.
        dropped = 0
        while True:
            try:
                await self._events()
            except asyncio.CancelledError:
                raise
            except Exception:
                dropped += 1
                if dropped == 1 or dropped % 100 == 0:
                    log.warning("bridge event loop dropped an unhandled event (total %d)", dropped, exc_info=True)

    async def _events(self):
        while True:
            event = await self.bridge.events.get()
            kind, data = event.get("type"), event.get("data", {})
            if kind == "stream_lost":
                self.operations.abort("stream_lost")
                self.remote_error = "stream_lost"
                self.state = "failed"
                self._finish("unknown", {"reason": "stream_lost", "termination_confirmed": False})
                self.authenticated.set()
                self.opened.set()
                continue
            if event.get("session_id") != self.session_id:
                continue
            if event.get("channel") == "files":
                self.operations.feed_file(kind,data)
                continue
            if kind == "terminal.data" and self.operations.active:
                self.operations.feed_terminal(data)
                continue
            if kind == "authenticated":
                self.snapshot["peer"] = data
                self.authenticated.set()
            elif kind == "transport":
                self.snapshot["transport"] = data
            elif kind == "permission":
                self.snapshot["permissions"][data["name"]] = data["enabled"]
            elif kind == "terminal.opened":
                self.snapshot["terminal_pid"] = data.get("pid")
                if not data.get("success") or data.get("replay") or data.get("persistent_sessions"):
                    self.remote_error = "terminal_open_failed"
                self.opened.set()
            elif kind == "terminal.data" and self.active and self.active.row["state"] in {"running", "submitting"}:
                try:
                    output, metadata = self.active.parser.feed(base64.b64decode(data["data"], validate=True))
                    remaining = max(0, OUTPUT_LIMIT - self.active.output_bytes)
                    encoded = output.encode("utf-8")
                    retained = encoded[:remaining].decode("utf-8", errors="ignore")
                    self.active.output += retained
                    self.active.output_bytes += len(retained.encode("utf-8"))
                    if len(encoded) > remaining:
                        self.active.metadata["output_truncated"] = True
                    if metadata is not None:
                        self.snapshot.update(cwd=metadata.get("cwd"), shell_version=metadata.get("shell_version"),
                                             snapshot_at=time.time())
                        self._finish("completed" if metadata["powershell_success"] and metadata["native_exit_code"] == 0 else "failed", metadata)
                except (ToolError, ValueError, KeyError, TypeError):
                    self.state = "failed"
                    self._finish("unknown", {"reason": "stream_lost", "termination_confirmed": False})
                    try:
                        await self.bridge.rpc("terminal.close", session_id=self.session_id)
                    except ToolError:
                        self.remote_error = "stream_lost"
            elif kind == "terminal.closed":
                self.closed.set()
                if self.active:
                    self._finish("cancelled" if self.active.row["state"] == "cancelling" else "unknown",
                                 {"termination_confirmed": True, "shell_exit_code": data.get("exit_code")})
                self.state = "closed"
                if self.operations.active and self.operations.active.row["state"] != "cancelling":
                    self.operations.abort("terminal_closed")
            elif kind == "challenge":
                challenge = data.get("kind", "")
                if challenge not in {"success", "info", "terminal-admin-login", "terminal-admin-login-password"}:
                    self.remote_error = "authentication_failed" if "password" in challenge else "remote_error"
                    self.snapshot["remote_error"] = {"kind": challenge, "title": data.get("title"), "message": data.get("message")}
            elif kind in {"session_closed", "terminal.error", "session_selection_required"}:
                self.operations.abort("session_closed")
                self.remote_error = self.remote_error or "remote_error"
                self.state = "failed"
                self._finish("unknown", {"reason": self.remote_error, "termination_confirmed": False})
                self.authenticated.set()
                self.opened.set()

    async def exec_command(self, session_id: str, operation_id: str, command: str, cwd=None,
                           wait_timeout_s=10, execution_timeout_s=300, native_output_encoding="utf8"):
        if not 1 <= len(operation_id) <= 128 or not 0 <= wait_timeout_s <= 30 or not 1 <= execution_timeout_s <= 3600:
            raise ToolError("invalid_params")
        request = {"session_id": session_id, "command": command, "cwd": cwd, "execution_timeout_s": execution_timeout_s}
        if native_output_encoding not in {"utf8", "gbk"}:
            raise ToolError("unsupported_native_output_encoding")
        if native_output_encoding != "utf8":
            request["native_output_encoding"] = native_output_encoding
        async with self.lock:
            existing, _, _ = self.journal.lookup(operation_id, request)
            if existing:
                return await self.get_command_output(session_id, existing["command_id"], 0, 32768, 0)
            self._require(session_id)
            if self.active or self.operations.active:
                raise ToolError("busy")
            trusted = command in DIAGNOSTICS and cwd is None
            if not trusted and not self.config.allow_writes:
                self.journal.audit("readonly_rejected", {"session_id": session_id, "operation_id": operation_id})
                raise ToolError("readonly_policy")
            auto = not trusted and self.config.auto_approve
            script = DIAGNOSTICS.get(command, command)
            if len(script.encode("utf-8")) > 64 * 1024:
                raise ToolError("command_too_large")
            row = self.journal.create(operation_id, request, "submitting" if (trusted or auto) else "pending_approval")
            running = Running(row, script)
            self.active = running
            if auto:
                self.journal.audit("approved", {"session_id": session_id, "command_id": row["command_id"],
                                                "digest": row["digest"], "source": "auto_approve"})
            if trusted or auto:
                await self._dispatch(running)
        return await self.get_command_output(session_id, running.row["command_id"], 0, 32768, wait_timeout_s)

    async def _dispatch(self, command: Running):
        request = json.loads(command.row["request"])
        command.row["state"] = "submitting"
        self._persist()
        self.journal.audit("command_submitting", {"command_id": command.row["command_id"], "session_id": self.session_id,
                                                  "peer_id": self.peer_id, "source": "mcp",
                                                  "identity": self.snapshot.get("system_info", {}).get("identity"),
                                                  "sid": self.snapshot.get("system_info", {}).get("sid")})
        try:
            await self.bridge.rpc("terminal.write", session_id=self.session_id,
                                  data=wrapper(command.script, command.nonce, request.get("cwd"), request.get("native_output_encoding", "utf8")))
            if self.active is command:
                command.row["state"] = "running"
                self._persist()
                command.timer = asyncio.create_task(self._deadline(command, request["execution_timeout_s"]))
        except (ToolError, asyncio.CancelledError) as exc:
            self.state = "failed"
            self._finish("unknown", {"reason": "submission_unknown", "termination_confirmed": False})
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _deadline(self, command: Running, timeout: float):
        await asyncio.sleep(timeout)
        if self.active is command:
            await self.cancel_command(self.session_id, command.row["command_id"], "execution_timeout")

    async def get_command_output(self, session_id: str, command_id: str, offset=0, max_chars=32768, wait_timeout_s=0):
        if offset < 0 or not 1 <= max_chars <= 65536 or not 0 <= wait_timeout_s <= 30:
            raise ToolError("invalid_params")
        row = self.journal.get(command_id)
        if row["session_id"] != session_id:
            raise ToolError("session_closed")
        if self.active and self.active.row["command_id"] == command_id:
            running = self.active
            if running.row["state"] != "pending_approval" and wait_timeout_s:
                try:
                    await asyncio.wait_for(running.done.wait(), wait_timeout_s)
                except TimeoutError:
                    pass
            if self.active is running:
                self._persist()
            row = self.journal.get(command_id)
        if self.operations.active and self.operations.active.row["command_id"] == command_id:
            operation = self.operations.active
            if operation.row["state"] != "pending_approval" and wait_timeout_s:
                try:
                    await asyncio.wait_for(operation.done.wait(),wait_timeout_s)
                except TimeoutError:
                    pass
            if self.operations.active is operation:
                self.operations._persist()
            row = self.journal.get(command_id)
        output = row["output"]
        if offset > len(output):
            raise ToolError("invalid_offset")
        page = output[offset:offset + max_chars].encode("utf-8")[:32768].decode("utf-8", errors="ignore")
        metadata = json.loads(row["metadata"])
        metadata = {key:value for key,value in metadata.items() if key not in {'review','owned_artifacts'}}
        return {"session_id": session_id, "command_id": command_id, "operation_id": row["operation_id"],
                "state": row["state"], "output": page, "offset": offset, "next_offset": offset + len(page),
                "has_more": offset + len(page) < len(output), "output_truncated": metadata.get("output_truncated", False),
                "approval_required": row["state"] == "pending_approval", "output_stream": "combined",
                "result": metadata, "created_at": row["created_at"]}

    async def cancel_command(self, session_id: str, command_id: str, reason="user_requested"):
        async with self.lock:
            row = self.journal.get(command_id)
            if row["session_id"] != session_id:
                raise ToolError("session_closed")
            if self.operations.active and self.operations.active.row["command_id"] == command_id:
                await self.operations.cancel(reason)
                completed = await self.get_command_output(session_id,command_id)
                return {"accepted":True,"state":completed["state"],"termination_confirmed":completed["result"].get("termination_confirmed",False),"session_state":self.state}
            if not self.active or self.active.row["command_id"] != command_id:
                return {"accepted": False, "state": row["state"], "termination_confirmed": json.loads(row["metadata"]).get("termination_confirmed", False)}
            running = self.active
            if row["state"] == "pending_approval":
                self._finish("cancelled", {"reason": reason, "not_submitted": True, "termination_confirmed": False})
            else:
                running.row["state"] = "cancelling"
                running.metadata["reason"] = reason
                try:
                    self._persist()
                except ToolError:
                    running.metadata['audit_storage_unavailable']=True
                try:
                    await self.bridge.rpc("terminal.close", session_id=session_id)
                    await asyncio.wait_for(running.done.wait(), 3)
                except (ToolError, TimeoutError):
                    self._finish("unknown", {"reason": reason, "termination_confirmed": False})
                    self.state = "failed"
            self.journal.audit("cancel_requested", {"command_id": command_id, "reason": reason})
        result = await self.get_command_output(session_id, command_id)
        return {"accepted": True, "state": result["state"], "termination_confirmed": result["result"].get("termination_confirmed", False),
                "session_state": self.state, "detached_children_may_remain": True}

    async def decide(self, session_id: str, command_id: str, digest: str, approve: bool):
        async with self.lock:
            self._require(session_id)
            if self.operations.active:
                return await self.operations.decide(command_id,digest,approve)
            if not self.active or self.active.row["command_id"] != command_id:
                raise ToolError("approval_conflict")
            row = self.active.row
            if row["state"] != "pending_approval" or not secrets.compare_digest(digest, row["digest"]):
                raise ToolError("approval_conflict")
            self.journal.audit("approved" if approve else "denied", {"session_id": session_id, "command_id": command_id, "digest": digest})
            if approve:
                if not self.config.allow_writes:
                    raise ToolError("readonly_policy")
                await self._dispatch(self.active)
            else:
                self._finish("cancelled", {"reason": "approval_denied", "not_submitted": True})
        return {"decided": True}

    def web_snapshot(self):
        result = {"state": self.state, "peer_id": self.peer_id, "session_id": self.session_id,
                  "policy": "auto_approve" if self.config.auto_approve else ("confirm_writes" if self.config.allow_writes else "readonly"), "snapshot": self.snapshot}
        if self.active:
            row = self.active.row
            result["command"] = {"command_id": row["command_id"], "state": row["state"], "digest": row["digest"],
                                 "request": json.loads(row["request"]), "output_tail": self.active.output[-8192:]}
        elif self.operations.active:
            result["command"] = self.operations.snapshot()
        return result

    async def _probe(self, *, connecting=False):
        async with self.lock:
            self._require(self.session_id, connecting=connecting)
            if self.active or self.operations.active:
                raise ToolError("busy")
            request = {"session_id": self.session_id, "command": "diagnostic:system_info", "cwd": None, "execution_timeout_s": 30}
            row = self.journal.create("probe:" + str(uuid.uuid4()), request, "submitting")
            running = Running(row, SYSTEM_INFO)
            self.active = running
            await self._dispatch(running)
        result = await self.get_command_output(self.session_id, row["command_id"], wait_timeout_s=30)
        if result["state"] != "completed":
            raise ToolError("unsupported_shell_or_probe_failed")
        try:
            info = json.loads(result["output"].strip())
            if not isinstance(info, dict) or not info.get("sid") or info.get("shell") != "powershell":
                raise ValueError()
        except ValueError:
            raise ToolError("unsupported_shell_or_probe_failed") from None
        self.snapshot.update(system_info=info, snapshot_at=time.time())
        return {"session_id": self.session_id, "probed_at": self.snapshot["snapshot_at"], **info}

    async def get_system_info(self, session_id: str):
        self._require(session_id)
        return await self._probe()

    async def close(self):
        try:
            if self.session_id:
                await self.disconnect(self.session_id)
        finally:
            if self.event_task:
                self.event_task.cancel()
                await asyncio.gather(self.event_task, return_exceptions=True)
            if self.bridge:
                await self.bridge.close()
            if self.owns_journal:
                self.journal.close()
