from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .transfers import Transfers


def register(server,owner):
    write=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=False)

    @server.tool(annotations=write)
    async def upload_file(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                          local_path:str,remote_path:str,mode:Literal['create','overwrite']='create',
                          expected_sha256:Annotated[str|None,Field(pattern=r'^[0-9a-fA-F]{64}$')]=None,
                          wait_timeout_s:Annotated[int,Field(ge=0,le=30)]=10,execution_timeout_s:Annotated[int,Field(ge=1,le=3600)]=600)->dict[str,Any]:
        """Upload one immutable captured local file using RustDesk's file channel, hash-verify staging, then commit. Human approval reviews source hash. Overwrite requires current remote SHA256 and retains a backup. No directory/automatic resume."""
        return await Transfers(owner(ctx).session(session_id)).call('upload',operation_id,local_path,remote_path,mode,
            expected_sha256.lower() if expected_sha256 else None,wait_timeout_s,execution_timeout_s)

    @server.tool(annotations=write)
    async def download_file(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                            remote_path:str,local_path:str,wait_timeout_s:Annotated[int,Field(ge=0,le=30)]=10,
                            execution_timeout_s:Annotated[int,Field(ge=1,le=3600)]=600)->dict[str,Any]:
        """Download one file to a new absolute local path after human approval. Verify local SHA256 against remote before/after hashes. Existing local destinations are refused; use a new path."""
        return await Transfers(owner(ctx).session(session_id)).call('download',operation_id,local_path,remote_path,
            wait_timeout_s=wait_timeout_s,execution_timeout_s=execution_timeout_s)
