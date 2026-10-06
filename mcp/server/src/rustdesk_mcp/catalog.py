import hashlib
import json
import uuid

from .operations import script
from .errors import ToolError


class Catalog:
    def __init__(self,engine):
        self.engine = engine

    async def call(self,kind,params,*,operation_id=None,readonly=False,resource="files.ps1",
                   wait_timeout_s=10,execution_timeout_s=300,review=None):
        operation_id = operation_id or "query:"+str(uuid.uuid4())
        arguments = {**params,"kind":kind,"roots":self.engine.config.remote_roots,
                     "operation_tag":hashlib.sha256(operation_id.encode()).hexdigest()[:24]}
        payload = script(resource,arguments)
        if len(payload.encode('utf-8'))>64*1024:
            raise ToolError('command_too_large_use_upload_or_script')
        async def execute(operations):
            return await operations.json_script(payload)
        result = await self.engine.operations.submit(self.engine.session_id,operation_id,kind,params,execute,
            readonly=readonly,review=review or params,wait_timeout_s=wait_timeout_s,execution_timeout_s=execution_timeout_s)
        return self.result(result)

    @staticmethod
    def result(result):
        if result["state"] == "completed" and not result.get("has_more"):
            try:
                data = json.loads(result["output"])
            except ValueError:
                return result
            return {key:result[key] for key in ("session_id","command_id","operation_id","state","approval_required","result")} | {"data":data}
        return result
