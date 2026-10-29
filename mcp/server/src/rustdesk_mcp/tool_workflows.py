import asyncio
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Annotated, Any

from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .errors import ToolError
from .windows import unprotect
from .workflows import Workflows


def register(server,owner):
    read=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=False)
    write=ToolAnnotations(readOnlyHint=False,destructiveHint=True,idempotentHint=True,openWorldHint=True)

    @server.tool(annotations=read)
    async def list_runbooks(ctx:Context)->dict[str,Any]:
        """List packaged PowerShell runbooks and full review text. These are templates, not automatic installation."""
        folder=Path(__file__).parent/'runbooks'
        return {'items':[{'name':p.stem,'script_text':p.read_text('utf-8')} for p in sorted(folder.glob('*.ps1'))]}

    @server.tool(annotations=write)
    async def exec_script(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                          local_path:str|None=None,runbook:str|None=None,parameters:dict[str,Any]|None=None,
                          shell_path:str|None=None,shell_sha256:Annotated[str|None,Field(pattern=r'^[0-9a-fA-F]{64}$')]=None,
                          execution_timeout_s:Annotated[int,Field(ge=1,le=3600)]=600,portable_deployment_command_id:str|None=None)->dict[str,Any]:
        """Approve full UTF-8 .ps1 text and immutable SHA256, upload using the official file channel, execute with scalar typed parameters, retain artifacts. Choose local_path OR a listed runbook. Existing shell is default; shell_path explicitly invokes a separate PowerShell process. Use cleanup_artifacts for recorded files."""
        return await Workflows(owner(ctx).session(session_id)).exec_script(operation_id,local_path,runbook,parameters,shell_path,
            shell_sha256.lower() if shell_sha256 else None,execution_timeout_s,portable_deployment_command_id)

    @server.tool(annotations=write)
    async def download_url(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                           asset_ref:str,remote_path:str,execution_timeout_s:Annotated[int,Field(ge=30,le=3600)]=1200)->dict[str,Any]:
        """Trusted manifest download: HTTPS source, allowlisted redirects, fixed SHA256, bounded local staging, official upload and exclusive remote create. Enabled by default; downloads.enabled=false disables it. Write policy still applies. Arbitrary URLs/untrusted hashes are not accepted. Download progress can have unknown total."""
        return await Workflows(owner(ctx).session(session_id)).download(operation_id,asset_ref,remote_path,execution_timeout_s=execution_timeout_s)

    @server.tool(annotations=write)
    async def deploy_pwsh_portable(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],
                                   asset_ref:str='pwsh-7.4.20-win-x64',execution_timeout_s:Annotated[int,Field(ge=30,le=3600)]=1800)->dict[str,Any]:
        """Deploy pinned official PowerShell ZIP into a new owned directory, hash-check before bounded safe extraction and version probe. Enabled by default; downloads.enabled=false disables it. Write policy still applies. Prefer this when pwsh is absent. Windows x64 only. No MSI, PATH, registry changes or default shell switch; execute via exec_script(portable_deployment_command_id=<completed command_id>). No unchecked cache reuse."""
        return await Workflows(owner(ctx).session(session_id)).download(operation_id,asset_ref,deploy=True,execution_timeout_s=execution_timeout_s)

    @server.tool(annotations=write)
    async def cleanup_artifacts(ctx:Context,session_id:str,operation_id:Annotated[str,Field(min_length=1,max_length=128)],command_id:str)->dict[str,Any]:
        """Approve deletion of this session's recorded artifacts only. Verify each file hash, reject reparse paths and remove directories only if empty. Changed/partial/unrecorded content remains with cleanup_pending; audit is retained."""
        return await Workflows(owner(ctx).session(session_id)).cleanup(operation_id,command_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False,idempotentHint=True,openWorldHint=True))
    async def analyze_file_hash(ctx:Context,sha256:Annotated[str,Field(pattern=r'^[0-9a-fA-F]{64}$')])->dict[str,Any]:
        """Optional VirusTotal hash-only lookup, enabled by local DPAPI API-key configuration. Sends no file content. Unknown/not-found/unavailable is not a clean verdict."""
        config=owner(ctx).config.intel
        if not config.get('enabled') or not config.get('virustotal_api_key_dpapi'):
            raise ToolError('intel_disabled_or_credential_unavailable')
        key=unprotect(config['virustotal_api_key_dpapi'])
        def request():
            req=urllib.request.Request('https://www.virustotal.com/api/v3/files/'+sha256.lower(),headers={'x-apikey':key})
            try:
                with urllib.request.urlopen(req,timeout=15) as response:
                    body=response.read(1024*1024+1)
                    if len(body)>1024*1024:raise ToolError('intel_response_too_large')
                    attrs=json.loads(body)['data']['attributes']
                    return {'sha256':sha256.lower(),'provider':'virustotal','found':True,'stats':attrs.get('last_analysis_stats'),
                            'analyzed_at':attrs.get('last_analysis_date'),'reputation':attrs.get('reputation'),'file_uploaded':False}
            except urllib.error.HTTPError as exc:
                if exc.code==404:return {'sha256':sha256.lower(),'provider':'virustotal','found':False,'file_uploaded':False}
                raise ToolError('intel_rate_limited' if exc.code==429 else 'intel_provider_error') from None
            except (urllib.error.URLError,ValueError,KeyError):raise ToolError('intel_unavailable') from None
        return await asyncio.to_thread(request)
