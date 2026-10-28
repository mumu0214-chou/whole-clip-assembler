"""Loopback-only frozen media mapping with per-request and lifecycle diagnostics."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import mimetypes
import re
import threading
import time
import uuid
from datetime import datetime, timezone

@contextmanager
def local_media_server(paths, *, diagnostics=None, job_id=None, source_map=None):
    token=uuid.uuid4().hex
    lock=threading.Lock()
    source_map=source_map or {}
    def record(event, **fields):
        if not diagnostics:return
        row=dict(timestamp=datetime.now(timezone.utc).isoformat(),event=event,job_id=job_id,**fields)
        try:
            with lock:
                with Path(diagnostics).open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
        except OSError:pass  # Diagnostics must not change successful media responses.
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_HEAD(self):self.send_file(False)
        def do_GET(self):self.send_file(True)
        def send_file(self,body):
            start_time=time.monotonic();key=self.path.split('?',1)[0]
            prefix='/'+token+'/'
            asset=key[len(prefix):] if key.startswith(prefix) else None
            p=paths.get(asset)
            info=source_map.get(asset,{})
            fields=dict(url=f'http://127.0.0.1:{self.server.server_address[1]}'+self.path,
                        method=self.command,range=self.headers.get('Range'),render_path=str(p) if p else None,
                        original_path=info.get('original_path'),stage='local_media_transfer')
            status=None;sent=0;error=None
            record('media_request_start',**fields)
            try:
                if p is None or not Path(p).is_file():
                    status=404;self.send_error(status);return
                size=Path(p).stat().st_size
                start,end,status=0,size-1,200
                header=self.headers.get('Range')
                if header:
                    try:
                        match=re.fullmatch(r'bytes=(\d*)-(\d*)',header.strip())
                        if not match or not any(match.groups()):raise ValueError()
                        a,b=match.groups()
                        if a:start=int(a);end=min(int(b),size-1) if b else size-1
                        else:
                            length=int(b)
                            if length<=0:raise ValueError()
                            start=max(0,size-length)
                        if not 0<=start<=end<size:raise ValueError()
                        status=206
                    except ValueError:
                        status=416;self.send_response(status)
                        self.send_header('Content-Range',f'bytes */{size}')
                        self.send_header('Content-Length','0');self.end_headers();return
                self.send_response(status)
                self.send_header('Content-Type',mimetypes.guess_type(str(p))[0] or 'application/octet-stream')
                self.send_header('Accept-Ranges','bytes');self.send_header('Access-Control-Allow-Origin','*')
                self.send_header('Content-Length',str(end-start+1))
                if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                self.end_headers()
                if body:
                    with Path(p).open('rb') as f:
                        f.seek(start);remaining=end-start+1
                        while remaining:
                            block=f.read(min(1024*1024,remaining))
                            if not block:raise OSError('Source ended before advertised Content-Length')
                            self.wfile.write(block);sent+=len(block);remaining-=len(block)
            except (OSError,ConnectionError) as exc:
                error=repr(exc);self.close_connection=True
            finally:
                record('media_request_end',**fields,status=status,bytes_sent=sent,error=error,
                       elapsed_ms=round((time.monotonic()-start_time)*1000,3))
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    base=f'http://127.0.0.1:{server.server_address[1]}/{token}/'
    thread.start();record('media_server_started',base_url=base)
    try:yield base
    finally:
        record('media_server_stopping',base_url=base)
        server.shutdown();server.server_close();thread.join(timeout=3)
        record('media_server_stopped',base_url=base,thread_stopped=not thread.is_alive())
