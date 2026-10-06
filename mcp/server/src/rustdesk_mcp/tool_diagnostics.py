from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .catalog import Catalog


def register(server, owner):
    read = ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=False)

    async def query(ctx,sid,kind,offset=0,limit=100,**params):
        return await Catalog(owner(ctx).session(sid)).call(kind,{'offset':offset,'limit':limit,**params},readonly=True,resource='diagnostics.ps1')

    @server.tool(annotations=read)
    async def list_processes(ctx:Context,session_id:str,offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=500)]=100)->dict[str,Any]:
        """Page processes with PID, creation time, parent, path, command line and unavailable fields."""
        return await query(ctx,session_id,'list_processes',offset,limit)

    @server.tool(annotations=read)
    async def list_services(ctx:Context,session_id:str,offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=500)]=100)->dict[str,Any]:
        """Page service state, startup mode, account, executable and PID."""
        return await query(ctx,session_id,'list_services',offset,limit)

    @server.tool(annotations=read)
    async def list_installed_software(ctx:Context,session_id:str,offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=500)]=100)->dict[str,Any]:
        """Inventory HKLM 64/32 bit and current user's uninstall registry; portable software is not exhaustive."""
        return await query(ctx,session_id,'list_installed_software',offset,limit)

    @server.tool(annotations=read)
    async def get_startup_items(ctx:Context,session_id:str,offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=500)]=100)->dict[str,Any]:
        """Inventory Run keys, startup folders, automatic services and scheduled tasks; report unavailable scopes."""
        return await query(ctx,session_id,'get_startup_items',offset,limit)

    @server.tool(annotations=read)
    async def get_network_connections(ctx:Context,session_id:str,offset:Annotated[int,Field(ge=0)]=0,limit:Annotated[int,Field(ge=1,le=500)]=100)->dict[str,Any]:
        """Page TCP/UDP endpoints and owning PIDs; querying does not modify networking."""
        return await query(ctx,session_id,'get_network_connections',offset,limit)

    @server.tool(annotations=read)
    async def get_recent_events(ctx:Context,session_id:str,log_name:Literal['Application','System','Security','Microsoft-Windows-Windows Defender/Operational']='System',
                                provider:Annotated[str,Field(max_length=256)]|None=None,hours:Annotated[int,Field(ge=1,le=168)]=24,
                                levels:list[Annotated[int,Field(ge=1,le=5)]]|None=None,limit:Annotated[int,Field(ge=1,le=100)]=20)->dict[str,Any]:
        """Read bounded recent events. Security logs may require elevation; messages are capped."""
        return await query(ctx,session_id,'get_recent_events',limit=limit,log_name=log_name,provider=provider,hours=hours,levels=levels or [])

    @server.tool(annotations=read)
    async def get_security_status(ctx:Context,session_id:str)->dict[str,Any]:
        """Read Defender availability, realtime protection, engine mode, signature and scan timestamps."""
        return await query(ctx,session_id,'get_security_status')

    @server.tool(annotations=write)
    async def kill_process(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                           pid:Annotated[int,Field(gt=4)],creation_time:str)->dict[str,Any]:
        """Request human approval to terminate the exact PID and creation time. Protected processes are refused."""
        return await Catalog(owner(ctx).session(session_id)).call('kill_process',{'pid':pid,'creation_time':creation_time},operation_id=operation_id,resource='diagnostics.ps1')

    @server.tool(annotations=write)
    async def control_service(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                              name:Annotated[str,Field(min_length=1,max_length=256,pattern=r'^[A-Za-z0-9_.\-]+$')],action:Literal['start','stop','restart'])->dict[str,Any]:
        """Request start/stop/restart with human approval and final state confirmation; critical services are refused."""
        return await Catalog(owner(ctx).session(session_id)).call('control_service',{'name':name,'action':action},operation_id=operation_id,resource='diagnostics.ps1')

    @server.tool(annotations=write)
    async def run_defender_scan(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                                scan_type:Literal['quick','full','custom']='quick',remote_path:str|None=None,
                                execution_timeout_s:Annotated[int,Field(ge=30,le=3600)]=1800)->dict[str,Any]:
        """Request a Defender scan. May quarantine files. Scan submission and confirmed scan completion are separate fields."""
        return await Catalog(owner(ctx).session(session_id)).call('run_defender_scan',{'scan_type':scan_type,'remote_path':remote_path},operation_id=operation_id,resource='diagnostics.ps1',execution_timeout_s=execution_timeout_s)
