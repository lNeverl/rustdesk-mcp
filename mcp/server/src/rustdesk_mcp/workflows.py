import asyncio
import hashlib
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath

from .catalog import Catalog
from .downloads import asset, fetch
from .errors import ToolError
from .operations import script
from .transfers import file_hash


class Workflows:
    def __init__(self,engine):
        self.engine=engine

    def existing(self,operation_id,kind,params,timeout):
        return self.engine.journal.lookup(operation_id,{'session_id':self.engine.session_id,'kind':kind,'params':params,'execution_timeout_s':timeout})[0]

    async def remote(self,ops,kind,tag,**params):
        return await ops.json_script(script('files.ps1',{'kind':kind,'operation_tag':tag,'session_id':self.engine.session_id,
            'roots':self.engine.config.remote_roots,**params}))

    async def exec_script(self,operation_id,local_path=None,runbook=None,parameters=None,shell_path=None,shell_sha256=None,execution_timeout_s=600,portable_deployment_command_id=None):
        parameters=parameters or {}
        params={'local_path':local_path,'runbook':runbook,'parameters':parameters,'shell_path':shell_path,'shell_sha256':shell_sha256,
                'portable_deployment_command_id':portable_deployment_command_id}
        old=self.existing(operation_id,'exec_script',params,execution_timeout_s)
        if old:return await self.engine.get_command_output(self.engine.session_id,old['command_id'])
        if bool(local_path)==bool(runbook):raise ToolError('choose_script_or_runbook')
        if not self.engine.config.allow_writes:raise ToolError('readonly_policy')
        deployment={}
        if portable_deployment_command_id:
            row=self.engine.journal.get(portable_deployment_command_id)
            if row['session_id']!=self.engine.session_id or row['state']!='completed' or json.loads(row['request']).get('kind')!='deploy_pwsh_portable':
                raise ToolError('deployment_record_unavailable')
            deployed=json.loads(row['output'])
            shell_path=deployed['path']
            deployment={'manifest_path':deployed['manifest_path'],'manifest_sha256':deployed['manifest_sha256']}
        if runbook:
            if not re.fullmatch(r'[a-z_]+',runbook):raise ToolError('runbook_not_found')
            source=Path(__file__).parent/'runbooks'/(runbook+'.ps1')
        else:
            source=Path(local_path)
            if not source.is_absolute() or source.suffix.lower()!='.ps1' or source.is_symlink():raise ToolError('invalid_script_path')
        if not source.is_file() or source.stat().st_size>64*1024:raise ToolError('script_unavailable_or_too_large')
        try:text=source.read_text('utf-8-sig')
        except UnicodeError:raise ToolError('script_requires_utf8') from None
        if any(not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',k) or not isinstance(v,(str,int,float,bool,type(None))) for k,v in parameters.items()):
            raise ToolError('invalid_script_parameters')
        if len(json.dumps(parameters).encode())>8192:raise ToolError('script_parameters_too_large')
        spool=self.engine.config.data_dir/'spool';spool.mkdir(exist_ok=True)
        fd,name=tempfile.mkstemp(prefix='script-',suffix='.ps1',dir=spool);os.close(fd);snapshot=Path(name)
        # UTF-8 BOM makes script literals readable in Windows PowerShell 5.1.
        snapshot.write_bytes(b'\xef\xbb\xbf'+text.encode())
        digest=file_hash(snapshot);tag=hashlib.sha256(operation_id.encode()).hexdigest()[:24]
        async def execute(ops):
            stage=await self.remote(ops,'prepare_stage',tag)
            path=stage['directory']+'\\script.ps1'
            records=[{'path':path,'sha256':digest,'kind':'file'},{'path':stage['directory'],'kind':'directory'}]
            ops.active.metadata['owned_artifacts']=records;ops._persist()
            await ops.transfer('upload',snapshot,path)
            result=await self.remote(ops,'exec_uploaded_script',tag,remote_path=path,source_sha256=digest,
                                     parameters=parameters,shell_path=shell_path,shell_sha256=shell_sha256,**deployment)
            if not result['success']:raise ToolError('script_failed')
            result['owned_artifacts']=records
            return result
        try:
            return Catalog.result(await self.engine.operations.submit(self.engine.session_id,operation_id,'exec_script',params,execute,
                review={**params,'script_text':text,'source_sha256':digest,'cleanup':'keep'},execution_timeout_s=execution_timeout_s,
                on_finish=lambda:snapshot.unlink(missing_ok=True)))
        except BaseException:
            snapshot.unlink(missing_ok=True);raise

    async def download(self,operation_id,asset_ref,remote_path=None,deploy=False,execution_timeout_s=1200):
        kind='deploy_pwsh_portable' if deploy else 'download_url';params={'asset_ref':asset_ref,'remote_path':remote_path}
        old=self.existing(operation_id,kind,params,execution_timeout_s)
        if old:return await self.engine.get_command_output(self.engine.session_id,old['command_id'])
        spec=asset(self.engine.config,asset_ref)
        if deploy and (spec.get('architecture')!='x64' or asset_ref!='pwsh-7.4.20-win-x64'):
            raise ToolError('unsupported_portable_manifest')
        if deploy:
            official=json.loads((Path(__file__).parent/'assets.json').read_text('utf-8'))[asset_ref]
            if spec['sha256'].lower()!=official['sha256'] or spec.get('version')!=official['version']:
                raise ToolError('portable_manifest_conflict')
        if not deploy and not remote_path:raise ToolError('invalid_path')
        tag=hashlib.sha256(operation_id.encode()).hexdigest()[:24]
        async def execute(ops):
            spool=self.engine.config.data_dir/'spool';spool.mkdir(exist_ok=True)
            folder=Path(tempfile.mkdtemp(prefix='asset-',dir=spool));local=folder/'payload.zip'
            def progress(value):
                if ops.active:ops.active.metadata['download']=value
            try:
                self.engine.journal.guard()
                verified=await fetch(spec,local,min(self.engine.config.max_transfer_bytes,self.engine.config.storage_budget_bytes//2),progress)
                stage=await self.remote(ops,'prepare_stage',tag)
                records=[{'path':stage['path'],'kind':'file','sha256':verified['sha256']},{'path':stage['directory'],'kind':'directory'}]
                ops.active.metadata['owned_artifacts']=records;ops._persist()
                await ops.transfer('upload',local,stage['path'])
                if deploy:
                    root=stage['directory']+'\\pwsh-'+spec['version']
                    def inventory():
                        items=[];directories={root}
                        with zipfile.ZipFile(local) as archive:
                            for member in archive.infolist():
                                relative=PureWindowsPath(member.filename)
                                if relative.is_absolute() or '..' in relative.parts or ':' in member.filename:
                                    raise ToolError('unsafe_archive_path')
                                target=str(PureWindowsPath(root)/relative)
                                for parent in relative.parents:
                                    directories.add(str(PureWindowsPath(root)/parent))
                                if member.is_dir():directories.add(target);continue
                                with archive.open(member) as content:
                                    digest=hashlib.file_digest(content,'sha256').hexdigest()
                                items.append({'path':target,'kind':'file','sha256':digest})
                        return items+[{'path':p,'kind':'directory'} for p in sorted(directories)]
                    records+=await asyncio.to_thread(inventory)
                    ops.active.metadata['owned_artifacts']=records;ops._persist()
                    result=await self.remote(ops,'deploy_portable',tag,remote_path=stage['path'],source_sha256=verified['sha256'],
                                             version=spec['version'],max_bytes=self.engine.config.max_transfer_bytes)
                    records.append({'path':result['manifest_path'],'sha256':result['manifest_sha256'],'kind':'file'})
                    ops.active.metadata['owned_artifacts']=records
                    ops._persist()
                else:
                    result=await self.remote(ops,'commit_upload',tag,remote_path=remote_path,stage_path=stage['path'],source_sha256=verified['sha256'],mode='create')
                return {**result,'download':verified,'asset_ref':asset_ref,'cleanup':'keep'}
            finally:
                local.unlink(missing_ok=True);folder.rmdir()
        return Catalog.result(await self.engine.operations.submit(self.engine.session_id,operation_id,kind,params,execute,review=spec,
            execution_timeout_s=execution_timeout_s))

    async def cleanup(self,operation_id,command_id):
        row=self.engine.journal.get(command_id)
        if row['session_id']!=self.engine.session_id:raise ToolError('artifact_session_mismatch')
        records=json.loads(row['metadata']).get('owned_artifacts',[])
        if not records:raise ToolError('owned_artifacts_unavailable')
        deployment=json.loads(row['output']) if row['state']=='completed' and json.loads(row['request']).get('kind')=='deploy_pwsh_portable' else None
        async def execute(ops):
            deleted=[];pending=[]
            tag=hashlib.sha256(operation_id.encode()).hexdigest()[:24]
            items=records
            if deployment:
                result=await self.remote(ops,'cleanup_portable',tag,manifest_path=deployment['manifest_path'],manifest_sha256=deployment['manifest_sha256'])
                if result['cleanup_pending']:return result
                deleted.append({'deployment_files':result['deleted_count']})
                items=[r for r in records if not r['path'].startswith(deployment['root'])]
            for item in sorted(items,key=lambda i:(i.get('kind')=='directory',-len(i['path']))):
                try:
                    await self.remote(ops,'remove_file',tag,remote_path=item['path'],expected_sha256=item.get('sha256'))
                    deleted.append(item['path'])
                except ToolError as exc:
                    if exc.code=='file_not_found':deleted.append(item['path'])
                    else:pending.append({'path':item['path'],'error':exc.code})
            return {'deleted':deleted,'cleanup_pending':pending,'recursive':False}
        return Catalog.result(await self.engine.operations.submit(self.engine.session_id,operation_id,'cleanup_artifacts',{'command_id':command_id},execute,
            review={'artifacts':records,'scope':'recorded artifacts only'}))
