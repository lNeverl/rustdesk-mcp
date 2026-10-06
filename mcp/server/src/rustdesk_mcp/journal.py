import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from .errors import ToolError


class Journal:
    def __init__(self, path: Path, budget_bytes=512*1024*1024):
        self.path = path
        self.budget_bytes = budget_bytes
        self.owner = path.with_suffix('.lock').open('a+b')
        try:
            self.owner.seek(0)
            if not self.owner.read(1):
                self.owner.write(b'\0');self.owner.flush()
            self.owner.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise ToolError('runtime_in_use') from None
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
            self.db.close()
            self.owner.close()
            raise ToolError("journal_schema_incompatible")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS commands (
            command_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, digest TEXT NOT NULL,
            session_id TEXT NOT NULL, state TEXT NOT NULL, request TEXT NOT NULL,
            output TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        )""")
        self.db.execute("UPDATE commands SET state='unknown' WHERE state IN ('submitting','running','cancelling','pending_approval')")
        self.db.execute("CREATE TABLE IF NOT EXISTS audit (at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), action TEXT, details TEXT)")
        self.db.execute("PRAGMA user_version=1")
        self.db.commit()

    def audit(self, action: str, details: dict):
        self.guard()
        self._write("INSERT INTO audit(action,details) VALUES(?,?)", (action, json.dumps(details, ensure_ascii=False)))

    def lookup(self, operation_id: str, request: dict):
        serialized = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        row = self.db.execute("SELECT * FROM commands WHERE operation_id=?", (operation_id,)).fetchone()
        if row and row["digest"] != digest:
            raise ToolError("operation_conflict")
        return dict(row) if row else None, serialized, digest

    def create(self, operation_id: str, request: dict, state: str) -> dict:
        existing, serialized, digest = self.lookup(operation_id, request)
        if existing:
            return existing
        self.guard()
        command_id = str(uuid.uuid4())
        self._write("INSERT INTO commands(command_id,operation_id,digest,session_id,state,request) VALUES(?,?,?,?,?,?)",
                        (command_id, operation_id, digest, request["session_id"], state, serialized))
        return self.get(command_id)

    def get(self, command_id: str) -> dict:
        row = self.db.execute("SELECT * FROM commands WHERE command_id=?", (command_id,)).fetchone()
        if not row:
            raise ToolError("command_not_found")
        return dict(row)

    def update(self, command_id: str, state: str, output: str, metadata: dict):
        self._write("UPDATE commands SET state=?,output=?,metadata=? WHERE command_id=?",
                        (state, output, json.dumps(metadata, ensure_ascii=False), command_id))

    def close(self):
        self.db.close()
        self.owner.close()

    def _write(self,statement,params):
        try:
            self.db.execute(statement,params)
            self.db.commit()
        except sqlite3.DatabaseError:
            self.db.rollback()
            raise ToolError('audit_storage_unavailable') from None

    def guard(self):
        used = sum(p.stat().st_size for p in self.path.parent.rglob('*') if p.is_file())
        if used + 1024*1024 > self.budget_bytes or shutil.disk_usage(self.path.parent).free < 16*1024*1024:
            raise ToolError('storage_budget_exceeded')

    def history(self, session_id=None, offset=0, limit=50):
        if offset < 0 or not 1 <= limit <= 200:
            raise ToolError('invalid_params')
        where, args = (' WHERE session_id=?',[session_id]) if session_id else ('',[])
        rows = self.db.execute('SELECT command_id,operation_id,session_id,state,created_at FROM commands'+where+
            ' ORDER BY created_at DESC,command_id DESC LIMIT ? OFFSET ?',args+[limit+1,offset]).fetchall()
        return {'items':[dict(r) for r in rows[:limit]],'offset':offset,'next_offset':offset+min(len(rows),limit),'has_more':len(rows)>limit}

    def audit_history(self, offset=0, limit=50):
        if offset < 0 or not 1 <= limit <= 200:
            raise ToolError('invalid_params')
        rows = self.db.execute('SELECT rowid,at,action,details FROM audit ORDER BY rowid DESC LIMIT ? OFFSET ?',(limit+1,offset)).fetchall()
        return {'items':[{**dict(r),'details':json.loads(r['details'])} for r in rows[:limit]],'next_offset':offset+min(len(rows),limit),'has_more':len(rows)>limit}
