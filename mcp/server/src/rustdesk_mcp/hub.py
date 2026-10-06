import asyncio
import json
import secrets
import uuid

from .config import private_directory
from .engine import Engine
from .errors import ToolError
from .journal import Journal


class Hub:
    def __init__(self, config):
        self.config = config
        private_directory(config.data_dir)
        self.journal = Journal(config.data_dir / 'operations.sqlite3', config.storage_budget_bytes)
        self.sessions = {}
        self.pending = {}
        self.tasks = set()
        self.lock = asyncio.Lock()

    def session(self, session_id):
        engine = self.sessions.get(session_id)
        if not engine:
            raise ToolError('session_closed')
        return engine

    async def connect(self, peer_id, credential_ref=None, admin_credential_ref=None, elevate=False, config_source=None):
        async with self.lock:
            if sum(e.state not in {'disconnected','closed','failed'} for e in self.sessions.values()) + len(self.pending) >= self.config.max_sessions:
                raise ToolError('session_limit')
            session_id = str(uuid.uuid4())
            engine = Engine(self.config, self.journal)
            args = dict(peer_id=peer_id, credential_ref=credential_ref, admin_credential_ref=admin_credential_ref,
                        elevate=elevate, config_source=config_source, owned_session_id=session_id)
            if elevate:
                if not self.config.allow_writes:
                    raise ToolError('readonly_policy')
                admin = self.config.admin_profiles.get(admin_credential_ref)
                if not admin or str(admin.get('peer_id','')).replace(' ','') != peer_id.replace(' ','') or not admin.get('password_dpapi'):
                    raise ToolError('credential_unavailable')
                request = {'session_id':session_id,'kind':'administrator_connect','params':{k:v for k,v in args.items() if k!='owned_session_id'},
                           'os_username':admin.get('username')}
                row = self.journal.create('connect:'+session_id, request, 'pending_approval')
                self.pending[session_id] = (engine,args,row)
                return {'session_id':session_id,'command_id':row['command_id'],'state':'pending_approval','approval_required':True}
            self.sessions[session_id] = engine
            engine.session_id, engine.peer_id, engine.state = session_id, peer_id, 'connecting'
        try:
            return await engine.connect(**args)
        except BaseException:
            await engine.close()
            raise

    def status(self, session_id):
        if session_id in self.pending:
            row = self.pending[session_id][2]
            return {'session_id':session_id,'state':row['state'],'command_id':row['command_id'],'approval_required':True}
        return self.session(session_id).status(session_id)

    async def disconnect(self, session_id):
        if session_id in self.pending:
            _,_,row = self.pending.pop(session_id)
            self.journal.update(row['command_id'],'cancelled','',{'not_submitted':True})
            return {'session_id':session_id,'state':'disconnected','cleanup_confirmed':True}
        engine = self.session(session_id)
        result = await engine.disconnect(session_id)
        await engine.close()
        return result

    async def decide(self, session_id, command_id, digest, approve):
        if session_id not in self.pending:
            return await self.session(session_id).decide(session_id,command_id,digest,approve)
        engine,args,row = self.pending[session_id]
        if row['command_id'] != command_id or not secrets.compare_digest(digest,row['digest']):
            raise ToolError('approval_conflict')
        self.journal.audit('administrator_approved' if approve else 'administrator_denied',{'session_id':session_id,'command_id':command_id,'digest':digest})
        if not approve:
            self.pending.pop(session_id)
            self.journal.update(command_id,'cancelled','',{'not_submitted':True})
            return {'decided':True}
        if not self.config.allow_writes:
            raise ToolError('readonly_policy')
        self.pending.pop(session_id)
        self.sessions[session_id] = engine
        engine.session_id,engine.peer_id,engine.state = session_id,args['peer_id'],'connecting'
        async def run():
            try:
                result = await engine.connect(**args,elevation_approved=True)
                self.journal.update(command_id,'completed',json.dumps(result,ensure_ascii=False),{})
            except (ToolError, OSError) as exc:
                engine.state = 'failed'
                self.journal.update(command_id,'failed','',{'error':{'code':getattr(exc,'code','connect_failed')}})
        self.journal.update(command_id,'running','',{})
        task = asyncio.create_task(run())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {'decided':True,'state':'connecting'}

    async def get_command_output(self, session_id, command_id, offset=0, max_chars=32768, wait_timeout_s=0):
        # Archived output remains available after its native process is closed.
        engine = self.sessions.get(session_id)
        if engine:
            return await engine.get_command_output(session_id,command_id,offset,max_chars,wait_timeout_s)
        row = self.journal.get(command_id)
        if row['session_id'] != session_id:
            raise ToolError('session_closed')
        if offset < 0 or offset > len(row['output']) or not 1 <= max_chars <= 65536:
            raise ToolError('invalid_offset')
        page = row['output'][offset:offset+max_chars].encode()[:32768].decode('utf-8',errors='ignore')
        return {'session_id':session_id,'command_id':command_id,'operation_id':row['operation_id'],'state':row['state'],
                'output':page,'offset':offset,'next_offset':offset+len(page),'has_more':offset+len(page)<len(row['output']),
                'result':{k:v for k,v in json.loads(row['metadata']).items() if k not in {'review','owned_artifacts'}},'approval_required':row['state']=='pending_approval'}

    async def cancel_command(self, session_id, command_id, reason='user_requested'):
        if session_id in self.pending:
            if self.pending[session_id][2]['command_id'] != command_id:
                raise ToolError('command_not_found')
            await self.disconnect(session_id)
            return {'accepted':True,'state':'cancelled','not_submitted':True}
        return await self.session(session_id).cancel_command(session_id,command_id,reason)

    def web_snapshot(self):
        sessions = [e.web_snapshot() for e in self.sessions.values()]
        for sid,(_,args,row) in self.pending.items():
            sessions.append({'session_id':sid,'peer_id':args['peer_id'],'state':'pending_approval',
                'command':{'command_id':row['command_id'],'state':row['state'],'digest':row['digest'],'request':json.loads(row['request'])}})
        return {'sessions':sessions,'history':self.journal.history(limit=30),'policy':'confirm_writes' if self.config.allow_writes else 'readonly'}

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        for sid in list(self.pending):
            await self.disconnect(sid)
        results=await asyncio.gather(*(engine.close() for engine in self.sessions.values()),return_exceptions=True)
        self.journal.close()
        for result in results:
            if isinstance(result,Exception):raise result
