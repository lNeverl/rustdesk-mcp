import argparse
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import Config
from .hub import Hub
from . import tool_diagnostics, tool_files, tool_transfers, tool_workflows
from .web import ConfirmationWeb


def make_server(config: Config) -> FastMCP:
    @asynccontextmanager
    async def lifespan(_server):
        engine = Hub(config)
        web = ConfirmationWeb(engine)
        try:
            yield engine
        finally:
            await web.close()
            await engine.close()

    server = FastMCP("RustDesk MCP", lifespan=lifespan,
                     instructions="Remote Windows tools. Default readonly. Arbitrary PowerShell requires local human approval unless the operator enabled auto_approve (fully automatic mode). Use stable operation_id; never retry an unknown command with a new ID.")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

    def engine(ctx: Context) -> Hub:
        return ctx.request_context.lifespan_context

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=True))
    async def connect(ctx: Context, peer_id: str, credential_ref: str | None = None,
                      admin_credential_ref: str | None = None, elevate: bool = False,
                      config_source: Literal["profile", "official"] | None = None) -> dict[str, Any]:
        """Connect a remote Windows peer with an independent bridge process. official reads official settings without writing back; credential_ref can override its password. profile uses private credentials. Never pass passwords. Elevation requires an admin_credential_ref and local human approval (skipped only in auto_approve mode) before connecting."""
        return await engine(ctx).connect(peer_id, credential_ref, admin_credential_ref, elevate, config_source)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False))
    async def disconnect(ctx: Context, session_id: str) -> dict[str, Any]:
        """Close this server's terminal and connection. Reports whether terminal cleanup was confirmed."""
        return await engine(ctx).disconnect(session_id)

    @server.tool(annotations=read)
    async def status(ctx: Context, session_id: str) -> dict[str, Any]:
        """Read cached connection, identity, shell, cwd, policy and active command state. Does not insert a remote probe."""
        return engine(ctx).status(session_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True))
    async def exec_command(ctx: Context, session_id: str, operation_id: Annotated[str, Field(min_length=1, max_length=128)],
                           command: str, cwd: str | None = None,
                           wait_timeout_s: Annotated[float, Field(ge=0, le=30)] = 10,
                           execution_timeout_s: Annotated[float, Field(ge=1, le=3600)] = 300) -> dict[str, Any]:
        """Execute PowerShell in the persistent terminal. Stable operation_id deduplicates retries. Readonly accepts diagnostic:system_info/processes/services/network/disk/unicode with default cwd only. Free commands or custom cwd require enabled write policy and local approval (auto-approved when auto_approve is on). PTY output is combined; busy rejects concurrent commands."""
        return await engine(ctx).session(session_id).exec_command(session_id, operation_id, command, cwd, wait_timeout_s, execution_timeout_s)

    @server.tool(annotations=read)
    async def get_command_output(ctx: Context, session_id: str, command_id: str,
                                 offset: Annotated[int, Field(ge=0)] = 0,
                                 max_chars: Annotated[int, Field(ge=1, le=65536)] = 32768,
                                 wait_timeout_s: Annotated[float, Field(ge=0, le=30)] = 0) -> dict[str, Any]:
        """Wait or page retained output by Unicode character offset. Waiting timeout leaves the command running. unknown means never automatically resubmit."""
        return await engine(ctx).get_command_output(session_id, command_id, offset, max_chars, wait_timeout_s)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
    async def cancel_command(ctx: Context, session_id: str, command_id: str,
                             reason: Annotated[str, Field(max_length=256)] = "user_requested") -> dict[str, Any]:
        """Cancel an approval or close the owned terminal. Confirmed terminal shutdown does not undo changes or guarantee that detached child processes stopped. Reconnect after terminal cancellation."""
        return await engine(ctx).cancel_command(session_id, command_id, reason)

    @server.tool(annotations=read)
    async def get_system_info(ctx: Context, session_id: str) -> dict[str, Any]:
        """Run a fixed readonly probe for OS/build/architecture, PowerShell, identity/SID, administrator membership, cwd and existing tools. Returns busy if another command owns the terminal."""
        return await engine(ctx).session(session_id).get_system_info(session_id)

    @server.tool(annotations=read)
    async def list_sessions(ctx:Context)->dict[str,Any]:
        """List this MCP instance's sessions and cached identities without connecting or probing."""
        return {'sessions':[engine(ctx).status(sid) for sid in list(engine(ctx).sessions)+list(engine(ctx).pending)]}

    @server.tool(annotations=read)
    async def list_commands(ctx:Context,session_id:str|None=None,offset:Annotated[int,Field(ge=0)]=0,
                            limit:Annotated[int,Field(ge=1,le=200)]=50)->dict[str,Any]:
        """Page persistent operation history, including earlier sessions. Output is queried with get_command_output."""
        return engine(ctx).journal.history(session_id,offset,limit)

    @server.tool(annotations=read)
    async def list_audit_events(ctx:Context,offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=200)]=50)->dict[str,Any]:
        """Page local audit events. Stored scripts/output may contain sensitive data; credentials are not recorded."""
        return engine(ctx).journal.audit_history(offset,limit)

    tool_files.register(server,engine)
    tool_diagnostics.register(server,engine)
    tool_transfers.register(server,engine)
    tool_workflows.register(server,engine)

    return server


def main():
    parser = argparse.ArgumentParser(description="RustDesk MCP stdio server")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--bridge-exe", type=Path)
    parser.add_argument("--data-dir", type=Path, help="Override the private runtime directory (e.g. isolated acceptance runs)")
    parser.add_argument("--config-source", choices=("profile", "official"), help="Default connection settings source")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    config = Config.load(args.config, args.bridge_exe)
    if args.config_source:
        config.config_source = args.config_source
    if args.data_dir:
        config.data_dir = args.data_dir.resolve()
    make_server(config).run(transport="stdio")


if __name__ == "__main__":
    main()
