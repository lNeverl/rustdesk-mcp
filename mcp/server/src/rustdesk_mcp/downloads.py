import asyncio
import hashlib
import json
import ssl
import threading
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ToolError


def asset(config,ref):
    if not config.downloads.get('enabled',True):
        raise ToolError('downloads_disabled')
    builtins=json.loads((Path(__file__).parent/'assets.json').read_text('utf-8'))
    value=config.downloads.get('assets',{}).get(ref) or builtins.get(ref)
    if not value or len(value.get('sha256',''))!=64:
        raise ToolError('trusted_asset_unavailable')
    value=json.loads(json.dumps(value))
    url=urlsplit(value['url'])
    if url.scheme!='https' or url.username or url.password or url.port not in {None,443} or not url.hostname:
        raise ToolError('invalid_download_source')
    value['redirect_hosts']=value.get('redirect_hosts',[url.hostname])
    return value


async def fetch(spec,destination,max_bytes,progress):
    stop=threading.Event()
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            url=urlsplit(newurl)
            if url.scheme!='https' or url.hostname not in spec['redirect_hosts'] or url.username or url.password or url.port not in {None,443}:
                raise ToolError('download_redirect_refused')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    loop=asyncio.get_running_loop()
    def run():
        opener=urllib.request.build_opener(Redirect(),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        digest=hashlib.sha256();received=0
        try:
            with opener.open(urllib.request.Request(spec['url'],headers={'User-Agent':'RustDeskMCP/0.2.0'}),timeout=10) as response, Path(destination).open('xb') as output:
                length=response.headers.get('Content-Length');total=int(length) if length and length.isdigit() else None
                if total and total>max_bytes:raise ToolError('download_too_large')
                while not stop.is_set():
                    chunk=response.read(256*1024)
                    if not chunk:break
                    received+=len(chunk)
                    if received>max_bytes:raise ToolError('download_too_large')
                    output.write(chunk);digest.update(chunk)
                    loop.call_soon_threadsafe(progress,{'received_bytes':received,'total_bytes':total})
                if stop.is_set():raise ToolError('download_cancelled')
            if digest.hexdigest()!=spec['sha256'].lower():raise ToolError('download_hash_mismatch')
            return {'sha256':digest.hexdigest(),'size':received,'source':spec['url']}
        except (urllib.error.URLError,OSError,ValueError):
            raise ToolError('download_failed') from None
    task=asyncio.create_task(asyncio.to_thread(run))
    try:
        return await asyncio.shield(task)
    finally:
        stop.set()
        # A blocking TLS read is bounded by the network timeout before the worker releases its file.
        try:
            await asyncio.wait_for(asyncio.shield(task),12)
        except (TimeoutError,ToolError,asyncio.CancelledError):
            pass
