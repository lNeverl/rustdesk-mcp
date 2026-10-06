import asyncio
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from .catalog import Catalog
from .errors import ToolError
from .operations import script


def file_hash(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source,'sha256').hexdigest()


class Transfers:
    def __init__(self,engine):
        self.engine=engine

    async def call(self,direction,operation_id,local_path,remote_path,mode='create',expected_sha256=None,wait_timeout_s=10,execution_timeout_s=600):
        engine=self.engine
        local=Path(local_path)
        if not local.is_absolute() or local.is_symlink() or any(p.is_symlink() for p in local.parents):
            raise ToolError('invalid_local_path')
        local=local.resolve()
        params={'local_path':str(local),'remote_path':remote_path,'mode':mode,'expected_sha256':expected_sha256}
        request={'session_id':engine.session_id,'kind':direction+'_file','params':params,'execution_timeout_s':execution_timeout_s}
        existing,_,_=engine.journal.lookup(operation_id,request)
        if existing:
            return Catalog.result(await engine.get_command_output(engine.session_id,existing['command_id']))
        engine._require(engine.session_id)
        if not engine.config.allow_writes:
            raise ToolError('readonly_policy')
        if mode=='overwrite' and not expected_sha256:
            raise ToolError('expected_hash_required')
        if direction=='download' and mode!='create':
            raise ToolError('local_overwrite_unsupported_use_new_path')
        spool=engine.config.data_dir/'spool'
        spool.mkdir(exist_ok=True)
        snapshot=None
        review=dict(params)
        if direction=='upload':
            if not local.is_file() or local.stat().st_size>engine.config.max_transfer_bytes:
                raise ToolError('file_unavailable_or_too_large')
            used=sum(p.stat().st_size for p in engine.config.data_dir.rglob('*') if p.is_file())
            if used+local.stat().st_size+16*1024*1024>engine.config.storage_budget_bytes:
                raise ToolError('storage_budget_exceeded')
            fd,name=tempfile.mkstemp(prefix='upload-',dir=spool)
            os.close(fd);snapshot=Path(name)
            await asyncio.to_thread(shutil.copyfile,local,snapshot)
            if snapshot.stat().st_size>engine.config.max_transfer_bytes:
                snapshot.unlink()
                raise ToolError('file_too_large')
            review.update(source_sha256=await asyncio.to_thread(file_hash,snapshot),size=snapshot.stat().st_size)
        tag=hashlib.sha256(operation_id.encode()).hexdigest()[:24]
        async def remote(ops,kind,**extra):
            return await ops.json_script(script('files.ps1',{'kind':kind,'roots':engine.config.remote_roots,
                'operation_tag':tag,'session_id':engine.session_id,**extra}))
        async def execute(ops):
            if direction=='upload':
                stage=await remote(ops,'prepare_stage')
                records=[{'path':stage['path'],'sha256':review['source_sha256'],'kind':'file'},
                         {'path':stage['directory'],'kind':'directory'}]
                ops.active.metadata['owned_artifacts']=records
                ops._persist()
                try:
                    await ops.transfer('upload',snapshot,stage['path'])
                    result=await remote(ops,'commit_upload',remote_path=remote_path,stage_path=stage['path'],
                                        source_sha256=review['source_sha256'],mode=mode,expected_sha256=expected_sha256)
                    if result.get('backup_path'):
                        records.append({'path':result['backup_path'],'kind':'file','sha256':result['previous_sha256']})
                    result['owned_artifacts']=records
                    ops.active.metadata['owned_artifacts']=records
                    return result
                finally:
                    snapshot.unlink(missing_ok=True)
            info=await remote(ops,'get_file_info',remote_path=remote_path)
            if info['directory'] or info['size']>engine.config.max_transfer_bytes:
                raise ToolError('file_too_large_or_directory')
            before=await remote(ops,'get_file_hash',remote_path=remote_path)
            if mode=='create' and local.exists():
                raise ToolError('destination_exists')
            local.parent.mkdir(parents=True,exist_ok=True)
            # RustDesk owns this new path; a pre-existing empty tempfile would request overwrite.
            stage_dir=Path(tempfile.mkdtemp(prefix='download-',dir=local.parent))
            stage=stage_dir/'payload.bin'
            try:
                await ops.transfer('download',stage,remote_path)
                digest=await asyncio.to_thread(file_hash,stage)
                after=await remote(ops,'get_file_hash',remote_path=remote_path)
                if digest!=before['sha256'] or digest!=after['sha256']:
                    raise ToolError('file_conflict')
                if os.name=='nt':
                    os.rename(stage,local)
                else:
                    os.link(stage,local);stage.unlink()
                return {'local_path':str(local),'remote_path':remote_path,'sha256':digest,'size':local.stat().st_size,'verified':True}
            finally:
                stage.unlink(missing_ok=True)
                stage_dir.rmdir()
        try:
            result=await engine.operations.submit(engine.session_id,operation_id,direction+'_file',params,execute,
                review=review,wait_timeout_s=wait_timeout_s,execution_timeout_s=execution_timeout_s,
                on_finish=(lambda:snapshot.unlink(missing_ok=True)) if snapshot else None)
            return Catalog.result(result)
        except BaseException:
            if snapshot:
                snapshot.unlink(missing_ok=True)
            raise
