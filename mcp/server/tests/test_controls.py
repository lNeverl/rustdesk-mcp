import asyncio
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, patch

from rustdesk_mcp.config import Config
from rustdesk_mcp.engine import Engine
from rustdesk_mcp.errors import ToolError
from rustdesk_mcp.hub import Hub
from rustdesk_mcp.web import ConfirmationWeb


class ControlsTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_approval_origin_digest_and_operation_retry(self):
        with tempfile.TemporaryDirectory() as root:
            hub=Hub(Config(Path('missing.exe'),Path(root),allow_writes=True))
            engine=Engine(hub.config,hub.journal)
            engine.session_id,engine.peer_id,engine.state='session','123','connected'
            hub.sessions['session']=engine
            web=ConfirmationWeb(hub)
            executed=AsyncMock(return_value={'value':'中文😀'})
            try:
                pending=await engine.operations.submit('session','stable','fixture',{'path':'owned'},executed,wait_timeout_s=0)
                self.assertEqual(pending['state'],'pending_approval');executed.assert_not_called()
                command=engine.web_snapshot()['command']
                def post(origin,digest):
                    body=json.dumps({'session_id':'session','command_id':command['command_id'],'digest':digest,'approve':True}).encode()
                    req=urllib.request.Request(web.origin+'/api/decision',data=body,headers={'Authorization':'Bearer '+web.token,'Origin':origin,'Content-Type':'application/json'})
                    try:
                        with urllib.request.urlopen(req,timeout=5) as response:return response.status
                    except urllib.error.HTTPError as exc:return exc.code
                self.assertEqual(await asyncio.to_thread(post,'https://example.org',command['digest']),403)
                self.assertEqual(await asyncio.to_thread(post,web.origin,'incorrect'),409)
                executed.assert_not_called()
                self.assertEqual(await asyncio.to_thread(post,web.origin,command['digest']),200)
                completed=await engine.get_command_output('session',pending['command_id'],wait_timeout_s=2)
                self.assertEqual(completed['state'],'completed');self.assertIn('中文😀',completed['output'])
                repeated=await engine.operations.submit('session','stable','fixture',{'path':'owned'},executed,wait_timeout_s=0)
                self.assertEqual(repeated['command_id'],pending['command_id']);executed.assert_awaited_once()
            finally:
                await web.close();engine.state='disconnected';await hub.close()

    async def test_admin_connect_requires_approval_and_capacity_blocks_execution(self):
        with tempfile.TemporaryDirectory() as root:
            config=Config(Path('missing.exe'),Path(root),allow_writes=True,admin_profiles={'admin':{'peer_id':'123','username':'admin','password_dpapi':'protected'}})
            hub=Hub(config)
            try:
                with self.assertRaisesRegex(ToolError,'runtime_in_use'):
                    Hub(config)
                with patch.object(Engine,'connect',new_callable=AsyncMock,return_value={'state':'connected'}) as connect:
                    pending=await hub.connect('123',admin_credential_ref='admin',elevate=True)
                    connect.assert_not_called()
                    command=hub.web_snapshot()['sessions'][0]['command']
                    await hub.decide(pending['session_id'],command['command_id'],command['digest'],True)
                    await asyncio.gather(*hub.tasks)
                    connect.assert_awaited_once()
                    self.assertTrue(connect.call_args.kwargs['elevation_approved'])
                engine=Engine(config,hub.journal);engine.session_id='fixture';engine.state='connected'
                execute=AsyncMock();hub.journal.budget_bytes=1
                with self.assertRaisesRegex(ToolError,'storage_budget_exceeded'):
                    await engine.operations.submit('fixture','blocked','fixture',{},execute,readonly=True)
                execute.assert_not_called()
            finally:
                hub.journal.budget_bytes=512*1024*1024
                for engine in hub.sessions.values():engine.state='disconnected'
                await hub.close()
