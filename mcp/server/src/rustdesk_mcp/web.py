import asyncio
import json
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .errors import ToolError

PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>RustDesk MCP 确认</title>
<style>body{font:16px system-ui;max-width:960px;margin:40px auto;padding:0 20px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f4f6;padding:16px}button{padding:10px 24px;margin-right:12px}#error{color:#b00}</style>
<h1>RustDesk MCP</h1><p>核对机器、完整命令和目录后批准。批准只对当前这次操作有效。</p>
<p>关闭终端不能回滚修改，也不保证终止脱离终端的子进程。</p>
<label>会话 <select id="sessions"></select></label><pre id="status">加载中…</pre><h2>当前操作与审批全文</h2><pre id="command"></pre>
<button id="approve" disabled>批准本次操作</button><button id="deny" disabled>拒绝</button>
<button id="cancel" disabled>取消当前操作</button><button id="disconnect" disabled>断开会话</button>
<p id="error"></p><h2>当前输出 / 传输进度</h2><pre id="output"></pre>
<h2>操作历史</h2><select id="history"></select><button id="load">读取结果</button><button id="next" disabled>下一页输出</button><pre id="archive"></pre>
<script src="/app.js"></script></html>"""

SCRIPT = """const token=location.hash.slice(1);history.replaceState(null,'',location.pathname);
let current=null,selected=null,archived=null,nextOffset=0;
async function api(path,body){const r=await fetch(path,{method:body?'POST':'GET',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(d.error||'请求失败');return d;}
async function refresh(){try{const all=await api('/api/status');const rows=all.sessions||[all];
const select=document.getElementById('sessions');selected=select.value||selected;select.replaceChildren();
for(const s of rows){const o=document.createElement('option');o.value=s.session_id;o.textContent=s.peer_id+' / '+s.state+' / '+s.session_id;select.append(o);}if(rows.some(s=>s.session_id===selected))select.value=selected;
const s=rows.find(s=>s.session_id===select.value)||{state:'无会话',policy:all.policy};selected=s.session_id;current=s.command;
document.getElementById('status').textContent=JSON.stringify({peer_id:s.peer_id,session_id:s.session_id,state:s.state,policy:s.policy,snapshot:s.snapshot},null,2);
document.getElementById('command').textContent=current?JSON.stringify(current.review||current.request,null,2)+'\\n状态：'+current.state+'\\n摘要：'+current.digest:'无';
document.getElementById('output').textContent=current?(current.output_tail||'')+'\\n'+JSON.stringify(current.transfer||{},null,2):'';
for(const id of ['approve','deny'])document.getElementById(id).disabled=!current||current.state!=='pending_approval';
document.getElementById('cancel').disabled=!current;document.getElementById('disconnect').disabled=!selected;
const h=document.getElementById('history');const previous=h.value;h.replaceChildren();for(const row of all.history?.items||[]){const o=document.createElement('option');o.value=row.command_id;o.textContent=row.created_at+' / '+row.state+' / '+row.operation_id;o.dataset.session=row.session_id;h.append(o);}if([...h.options].some(o=>o.value===previous))h.value=previous;
document.getElementById('error').textContent='';}catch(e){document.getElementById('error').textContent=e.message;}}
async function decide(approve){const c=current;if(!c)return;for(const id of ['approve','deny'])document.getElementById(id).disabled=true;
try{await api('/api/decision',{session_id:c.request.session_id,command_id:c.command_id,digest:c.digest,approve});await refresh();}catch(e){document.getElementById('error').textContent=e.message;}}
async function action(kind){try{await api('/api/'+kind,{session_id:selected,...(kind==='cancel'?{command_id:current.command_id}: {})});await refresh();}catch(e){document.getElementById('error').textContent=e.message;}}
async function load(next){try{if(!next){const o=document.getElementById('history').selectedOptions[0];if(!o)return;archived={session_id:o.dataset.session,command_id:o.value};nextOffset=0;}const r=await api('/api/output?'+new URLSearchParams({...archived,offset:nextOffset}));document.getElementById('archive').textContent=JSON.stringify(r,null,2);nextOffset=r.next_offset;document.getElementById('next').disabled=!r.has_more;}catch(e){document.getElementById('error').textContent=e.message;}}
document.getElementById('approve').onclick=()=>decide(true);document.getElementById('deny').onclick=()=>decide(false);
document.getElementById('cancel').onclick=()=>action('cancel');document.getElementById('disconnect').onclick=()=>action('disconnect');
document.getElementById('load').onclick=()=>load(false);document.getElementById('next').onclick=()=>load(true);document.getElementById('sessions').onchange=refresh;
refresh();setInterval(refresh,1000);"""


class ConfirmationWeb:
    def __init__(self, engine):
        self.engine = engine
        self.loop = asyncio.get_running_loop()
        self.token = secrets.token_urlsafe(32)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status, body, content_type="application/json"):
                data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(data)

            def authorized(self):
                return self.headers.get("Host") == owner.authority and secrets.compare_digest(
                    self.headers.get("Authorization", ""), "Bearer " + owner.token)

            def do_GET(self):
                if self.headers.get("Host") != owner.authority:
                    return self.respond(403, {"error": "forbidden"})
                if self.path == "/":
                    return self.respond(200, PAGE, "text/html")
                if self.path == "/app.js":
                    return self.respond(200, SCRIPT, "text/javascript")
                path = urlsplit(self.path)
                if path.path not in {"/api/status","/api/output"} or not self.authorized():
                    return self.respond(403, {"error": "forbidden"})
                async def snapshot():
                    if path.path == '/api/output':
                        params = parse_qs(path.query,strict_parsing=True)
                        return await owner.engine.get_command_output(params['session_id'][0],params['command_id'][0],int(params.get('offset',['0'])[0]))
                    return owner.engine.web_snapshot()
                future = asyncio.run_coroutine_threadsafe(snapshot(), owner.loop)
                try:
                    self.respond(200, future.result(timeout=5))
                except Exception:
                    future.cancel()
                    self.respond(503, {"error": "unavailable"})

            def do_POST(self):
                if self.path not in {"/api/decision","/api/cancel","/api/disconnect"} or not self.authorized() or self.headers.get("Origin") != owner.origin:
                    return self.respond(403, {"error": "forbidden"})
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError()
                    body = json.loads(self.rfile.read(length))
                    expected = {'/api/decision':{'session_id','command_id','digest','approve'},'/api/cancel':{'session_id','command_id'},'/api/disconnect':{'session_id'}}[self.path]
                    if set(body) != expected or (self.path=='/api/decision' and not isinstance(body['approve'],bool)) or not all(isinstance(v,str) for k,v in body.items() if k!='approve'):
                        raise ValueError()
                    action = {'/api/decision':owner.engine.decide,'/api/cancel':owner.engine.cancel_command,'/api/disconnect':owner.engine.disconnect}[self.path]
                    future = asyncio.run_coroutine_threadsafe(action(**body), owner.loop)
                    try:
                        result = future.result(timeout=15)
                    except Exception:
                        future.cancel()
                        raise
                    self.respond(200, result)
                except ToolError as exc:
                    self.respond(409, {"error": exc.code})
                except (ValueError, TypeError, KeyError):
                    self.respond(400, {"error": "invalid_request"})
                except Exception:
                    self.respond(503, {"error": "decision_unknown"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.authority = f"127.0.0.1:{self.server.server_port}"
        self.origin = f"http://{self.authority}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        try:
            entry_file = engine.config.data_dir / "approval-url.txt"
            entry_file.write_text(f"{self.origin}/#{self.token}\n", encoding="utf-8")
            print(f"RustDesk MCP 本地确认页地址（含 token，勿共享）已写入：{entry_file}", file=sys.stderr)
        except OSError:
            print(f"RustDesk MCP 本地确认页：{self.origin}/#{self.token}", file=sys.stderr)

    async def close(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        self.thread.join(timeout=2)
