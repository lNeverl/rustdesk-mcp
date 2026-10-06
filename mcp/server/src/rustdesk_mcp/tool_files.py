from typing import Annotated, Any, Literal

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .catalog import Catalog
from .errors import ToolError


def register(server,engine):
    read = ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=False)
    def catalog(ctx,session_id):
        owner = engine(ctx)
        return Catalog(owner.session(session_id) if hasattr(owner,"session") else owner)

    @server.tool(annotations=read)
    async def list_dir(ctx:Context,session_id:str,remote_path:str,offset:Annotated[int,Field(ge=0)]=0,
                       limit:Annotated[int,Field(ge=1,le=1000)]=100) -> dict[str,Any]:
        """List one remote directory with metadata and bounded pagination. Reparse paths and configured root escapes are refused."""
        return await catalog(ctx,session_id).call("list_dir",{"remote_path":remote_path,"offset":offset,"limit":limit},readonly=True)

    @server.tool(annotations=read)
    async def get_file_info(ctx:Context,session_id:str,remote_path:str) -> dict[str,Any]:
        """Read remote file/directory size, timestamps and attributes."""
        return await catalog(ctx,session_id).call("get_file_info",{"remote_path":remote_path},readonly=True)

    @server.tool(annotations=read)
    async def read_file(ctx:Context,session_id:str,remote_path:str,
        offset:Annotated[int,Field(ge=0)]=0,max_chars:Annotated[int,Field(ge=1,le=32768)]=8192,
        encoding:Literal["auto","utf8","utf16le","utf16be","gb18030"]="auto",
        start_line:Annotated[int,Field(ge=1)]=1,tail_lines:Annotated[int,Field(ge=0,le=10000)]=0,
        expected_sha256:Annotated[str|None,Field(pattern=r"^[0-9a-fA-F]{64}$")]=None) -> dict[str,Any]:
        """Read a text file up to 8 MiB. Unicode codepoint pagination, optional lines/tail, encoding/BOM/newline and SHA256. Use the returned next_offset and hash for stable follow-up reads; binary or unknown encoding is refused."""
        return await catalog(ctx,session_id).call("read_file",{"remote_path":remote_path,"offset":offset,"max_chars":max_chars,
            "encoding":encoding,"start_line":start_line,"tail_lines":tail_lines,"expected_sha256":expected_sha256.lower() if expected_sha256 else None},readonly=True)

    @server.tool(annotations=write)
    async def write_file(ctx:Context,session_id:str,operation_id:str,remote_path:str,
        content:Annotated[str,Field(max_length=32768)],mode:Literal["create","overwrite","append"]="create",
        expected_sha256:Annotated[str|None,Field(pattern=r"^[0-9a-fA-F]{64}$")]=None,
        encoding:Literal["preserve","utf8","utf16le","utf16be","gb18030"]="preserve",
        newline:Literal["preserve","lf","crlf"]="preserve") -> dict[str,Any]:
        """Approved text create/overwrite/append. Create is exclusive; existing files require expected_sha256. Locks during hash check/write, preserves encoding/BOM, retains a backup. Writes are not power-loss atomic. Larger/binary payloads use upload_file."""
        if mode != "create" and not expected_sha256:
            raise ToolError("expected_hash_required")
        return await catalog(ctx,session_id).call("write_file",{"remote_path":remote_path,"content":content,"mode":mode,
            "expected_sha256":expected_sha256.lower() if expected_sha256 else None,"encoding":encoding,"newline":newline},operation_id=operation_id)

    @server.tool(annotations=write)
    async def edit_file(ctx:Context,session_id:str,operation_id:str,remote_path:str,
        old_text:Annotated[str,Field(min_length=1,max_length=32768)],new_text:Annotated[str,Field(max_length=32768)],
        expected_sha256:Annotated[str,Field(pattern=r"^[0-9a-fA-F]{64}$")],replace_all:bool=False,
        encoding:Literal["auto","utf8","utf16le","utf16be","gb18030"]="auto") -> dict[str,Any]:
        """Approved exact replacement with hash and exclusive file lock. Default requires one match, preserves encoding/BOM/newlines and retains backup. No regex replacements."""
        return await catalog(ctx,session_id).call("edit_file",{"remote_path":remote_path,"old_text":old_text,"new_text":new_text,
            "expected_sha256":expected_sha256.lower(),"replace_all":replace_all,"encoding":encoding,"newline":"preserve"},operation_id=operation_id)

    @server.tool(annotations=read)
    async def find_files(ctx:Context,session_id:str,remote_path:str,pattern:str="*",
        max_depth:Annotated[int,Field(ge=0,le=32)]=8,limit:Annotated[int,Field(ge=1,le=1000)]=100,
        max_files:Annotated[int,Field(ge=1,le=100000)]=10000,timeout_s:Annotated[int,Field(ge=1,le=60)]=10,
        include_hidden:bool=False,exclude:list[str]|None=None) -> dict[str,Any]:
        """Bounded filename wildcard search. Does not follow directory junctions/symlinks. Reports scanned/skipped counts and truncation."""
        return await catalog(ctx,session_id).call("find_files",{"remote_path":remote_path,"pattern":pattern,"max_depth":max_depth,
            "limit":limit,"max_files":max_files,"timeout_s":timeout_s,"include_hidden":include_hidden,"exclude":exclude or []},readonly=True,execution_timeout_s=timeout_s+20)

    @server.tool(annotations=read)
    async def search_text(ctx:Context,session_id:str,remote_path:str,query:Annotated[str,Field(min_length=1,max_length=1024)],
        pattern:str="*",regex:bool=False,case_sensitive:bool=False,
        max_depth:Annotated[int,Field(ge=0,le=32)]=8,limit:Annotated[int,Field(ge=1,le=500)]=100,
        max_files:Annotated[int,Field(ge=1,le=100000)]=10000,timeout_s:Annotated[int,Field(ge=1,le=60)]=10,
        max_file_bytes:Annotated[int,Field(ge=1,le=8388608)]=1048576,
        encoding:Literal["auto","utf8","utf16le","utf16be","gb18030"]="auto",exclude:list[str]|None=None) -> dict[str,Any]:
        """Bounded content search, literal by default; regex has a per-match timeout. Returns paths, line numbers and capped text. Skips binary/inaccessible/large files and reparse directories."""
        return await catalog(ctx,session_id).call("search_text",{"remote_path":remote_path,"query":query,"pattern":pattern,"regex":regex,
            "case_sensitive":case_sensitive,"max_depth":max_depth,"limit":limit,"max_files":max_files,"timeout_s":timeout_s,
            "max_file_bytes":max_file_bytes,"encoding":encoding,"include_hidden":False,"exclude":exclude or []},readonly=True,execution_timeout_s=timeout_s+20)

    @server.tool(annotations=write)
    async def make_dir(ctx:Context,session_id:str,operation_id:str,remote_path:str) -> dict[str,Any]:
        """Approved directory creation. Existing paths and reparse components are refused."""
        return await catalog(ctx,session_id).call("make_dir",{"remote_path":remote_path},operation_id=operation_id)

    @server.tool(annotations=write)
    async def rename_file(ctx:Context,session_id:str,operation_id:str,remote_path:str,destination:str) -> dict[str,Any]:
        """Approved file/directory move. Destination must not exist; no overwrite or recursive deletion."""
        return await catalog(ctx,session_id).call("rename_file",{"remote_path":remote_path,"destination":destination},operation_id=operation_id)

    @server.tool(annotations=write)
    async def remove_file(ctx:Context,session_id:str,operation_id:str,remote_path:str,
        expected_sha256:Annotated[str|None,Field(pattern=r"^[0-9a-fA-F]{64}$")]=None) -> dict[str,Any]:
        """Approved file or empty-directory removal. Files require expected SHA256. Recursive deletion is not exposed."""
        return await catalog(ctx,session_id).call("remove_file",{"remote_path":remote_path,"expected_sha256":expected_sha256.lower() if expected_sha256 else None},operation_id=operation_id)

    @server.tool(annotations=read)
    async def get_file_hash(ctx:Context,session_id:str,remote_path:str) -> dict[str,Any]:
        """Read remote file SHA256 without uploading the file."""
        return await catalog(ctx,session_id).call("get_file_hash",{"remote_path":remote_path},readonly=True)

    @server.tool(annotations=read)
    async def get_file_signature(ctx:Context,session_id:str,remote_path:str) -> dict[str,Any]:
        """Read Windows Authenticode status/publisher. A valid signature is not a malware verdict."""
        return await catalog(ctx,session_id).call("get_file_signature",{"remote_path":remote_path},readonly=True)
