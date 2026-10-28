"""Bounded asynchronous telemetry. No media or credentials enter diagnostics archives."""
from __future__ import annotations
from mac_paths import path as _portable_path
from collections import deque, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from datetime import datetime, timezone
from pathlib import Path
import functools
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile

STAGES = {'preparation':'准备', 'voice':'配音', 'audio':'音频处理', 'subtitles':'字幕处理',
          'selection':'素材选择', 'preprocess':'素材预处理', 'remotion':'Remotion渲染',
          'ffmpeg':'FFmpeg合成', 'output':'成品输出', 'validation':'完整性检查',
          'queue_wait':'队列等待', 'resource_wait':'资源等待'}
LEVELS = {'DEBUG':10, 'INFO':20, 'WARNING':30, 'ERROR':40, 'CRITICAL':50}
_context = ContextVar('diagnostic_context', default={})
_hub = None
_cpu_sample=None

def stamp():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')

def redact(value):
    """Redact nested credentials, command flags, URL credentials, and home names."""
    secret = re.compile(r'(?:api[_-]?key|password|passwd|access[_-]?token|authorization|secret)', re.I)
    if isinstance(value, dict):
        return {str(k):('[REDACTED]' if secret.search(str(k)) else redact(v)) for k,v in value.items()}
    if isinstance(value, (list,tuple)):
        result=[]; pending=False
        for x in value:
            if pending: result.append('[REDACTED]'); pending=False; continue
            result.append(redact(x)); pending=isinstance(x,str) and x.startswith('-') and bool(secret.search(x)) and '=' not in x
        return result
    if not isinstance(value, str): return value
    value=re.sub(r'(?i)(Bearer\s+)\S+', r'\1[REDACTED]',value)
    value=re.sub(r'(?i)((?:api[_-]?key|password|passwd|access[_-]?token|authorization|secret)["\s]*[:=]["\s]*)[^\s,"}]+',r'\1[REDACTED]',value)
    value=re.sub(r'(https?://)[^/\s:@]+:[^/\s@]+@',r'\1[REDACTED]@',value)
    return re.sub(r'(?i)C:[/\\]Users[/\\][^/\\\s"\']+', 'C:/Users/[USER]',value)

def classify(exc):
    s=str(exc).lower()
    if isinstance(exc,PermissionError):return {'category':'permission','advice':'检查输出目录权限或文件占用。'}
    if isinstance(exc,FileNotFoundError):return {'category':'missing_input','advice':'检查日志中的实际文件路径。'}
    if isinstance(exc,TimeoutError):return {'category':'timeout','advice':'查看进程日志和资源后重试。'}
    rules=[(('out of memory','cuda oom','显存不足'), 'memory_gpu', '等待显存释放，减少并发后重试。'),
           (('cannot allocate memory','内存不足'), 'memory_system','关闭占用内存的程序后重试。'),
           (('no space left','磁盘空间不足','disk full'), 'disk_full','释放输出盘空间后继续原批次。'),
           (('permission denied','access is denied','拒绝访问'), 'permission','检查输出目录权限或文件占用。'),
           (('being used by another process','winerror 32'), 'file_busy','关闭占用该文件的程序后重试。'),
           (('字体不存在','字体缺失','字体文件已丢失','系统字体已不存在','font missing'), 'font_missing','恢复原字体或为新任务重新选择字体。'),
           (('缓存损坏','cache corrupt'), 'cache_corrupt','该缓存不能复用，将重新生成。'),
           (('临时目录','protected'), 'temp_lifecycle','检查临时目录及保护标记；不要手动清理活跃任务。'),
           (('不存在','no such file','not found'), 'missing_input','检查日志中的实际文件路径。'),
           (('模型加载失败','缺少 indextts','模型不可用'), 'model_unavailable','检查模型文件与Worker加载日志。'),
           (('timeout','timed out','超时','运行超过'), 'timeout','查看进程日志和资源记录，确认后重试。'),
           (('cancel','取消'), 'cancelled','任务已取消，原方案保留。')]
    for tokens,code,advice in rules:
        if any(x in s for x in tokens): return {'category':code,'advice':advice}
    return {'category':'unconfirmed','advice':'原因待确认，请查看完整堆栈与子进程输出。'}

class Hub:
    def __init__(self, root, *, debug=False, retention_days=30, file_bytes=8*1024**2, total_bytes=512*1024**2):
        self.root=Path(root); self.debug=debug
        self.retention_days=retention_days; self.file_bytes=file_bytes; self.total_bytes=total_bytes
        self.run_id=uuid.uuid4().hex; self.root.mkdir(parents=True,exist_ok=True)
        self.path=self.root/self.run_id; self.path.mkdir()
        self.queue=queue.Queue(maxsize=20000); self.recent=deque(maxlen=5000)
        self.lock=threading.RLock(); self.active={}; self.dropped=0; self.fault=None; self.overflow=deque(maxlen=1000); self.stage_totals=defaultdict(lambda:defaultdict(float))
        self.closed=False; self.processes=set(); self._part=0
        self._lease=(self.path/'.active'); self._lease.write_text(str(os.getpid()),encoding='ascii')
        self.writer=threading.Thread(target=self._write,name='diagnostic-writer',daemon=True); self.writer.start()
        self.monitor=threading.Thread(target=self._monitor,name='resource-monitor',daemon=True); self.monitor.start()
        self.emit('run_start','诊断日志已启动', component='runtime', python=sys.version, os=platform.platform())

    def emit(self,event,message='',level='INFO',**fields):
        if level=='DEBUG' and not self.debug:return
        data=dict(run_id=self.run_id,batch_id='',job_id='',attempt_id='',stage='',component='runtime',
                  timestamp=stamp(),monotonic=time.monotonic(),level=level,event=event,message=str(message))
        data.update(_context.get()); data.update(fields); data=redact(data)
        with self.lock:
            self.recent.append(data)
            if len(self.stage_totals)>5000:self.stage_totals.pop(next(iter(self.stage_totals)))
            if event=="stage_end" and data.get("attempt_id"):
                self.stage_totals[data["attempt_id"]][data["stage"]]+=data.get("elapsed",0)
        try:self.queue.put_nowait(data)
        except queue.Full:
            self.dropped+=1
            # Rare overload: preserve errors separately without blocking render on disk I/O.
            if LEVELS.get(level,20)>=40:
                with self.lock:
                    self.fault='日志队列已满；错误详情已保留并延后写入。'
                    self.overflow.append(data)
        return data

    def _write(self):
        stream=None; human=None; size=0
        try:
            while True:
                data=self.queue.get()
                try:
                    if data is None:break
                    if data=='flush':
                        if stream:stream.flush(); human.flush()
                        continue
                    if stream is None or size>=self.file_bytes:
                        if stream:stream.close();human.close()
                        self._part+=1; size=0
                        stream=(self.path/f'events-{self._part:04d}.jsonl').open('a',encoding='utf-8')
                        human=(self.path/f'events-{self._part:04d}.log').open('a',encoding='utf-8')
                    line=json.dumps(data,ensure_ascii=False,default=str)+'\n'
                    stream.write(line); size+=len(line.encode('utf-8'))
                    human.write(f"{data['timestamp']} {data['level']} [{data['batch_id']}/{data['job_id']}/{data['attempt_id']}] {data['stage']} {data['event']} {data['message']}\n")
                    # Buffered disk writes; visibility at human-speed without flush per frame.
                    if self.queue.empty():stream.flush();human.flush()
                except Exception as exc:
                    self.fault='日志写入失败，已降级内存记录：'+str(exc)
                    if stream:
                        try:stream.close();human.close()
                        except OSError:pass
                    stream=human=None
                finally:self.queue.task_done()
        finally:
            if stream:stream.close();human.close()

    def flush(self):
        if not self.closed:
            with self.lock:overflow=list(self.overflow);self.overflow.clear()
            for data in overflow:self.queue.put(data)
            self.queue.put('flush');self.queue.join()

    def close(self):
        if self.closed:return
        self.flush()
        self.emit('run_end','诊断日志关闭',dropped=self.dropped)
        self.closed=True;self.queue.put(None);self.writer.join(5)
        if not self.writer.is_alive():self._lease.unlink(missing_ok=True)

    def _monitor(self):
        samples=0
        while not self.closed:
            for _ in range(50):
                if self.closed:return
                time.sleep(.1)
            samples+=1
            if samples%120==0:
                try:self.cleanup()
                except OSError as exc:self.fault='日志清理失败：'+str(exc)
            with self.lock:active=list(self.active.values())
            if active:
                self.emit('resource_sample','资源采样',component='resources',resources=resources(self.root),
                          active_processes=len(self.processes),active_tasks=len(active))
                for task in active:
                    self.emit('heartbeat','执行中；无可靠百分比时不估算进度',**task,
                              elapsed=time.monotonic()-task['started_monotonic'])
            if self.fault:
                # Visible without recursing through a broken disk writer.
                with self.lock:self.recent.append(dict(run_id=self.run_id,timestamp=stamp(),level='WARNING',event='logging_degraded',message=self.fault))

    def cleanup(self):
        """Only this system's inactive UUID directories. Never visit media/cache roots."""
        removed=[]; dirs=[]
        for p in self.root.iterdir():
            if not p.is_dir() or p.is_symlink() or not re.fullmatch('[a-f0-9]{32}',p.name):continue
            lease=p/'.active'
            if p==self.path:continue
            if lease.exists():
                try:
                    pid=int(lease.read_text(encoding='ascii'))
                    if os.name=='nt':
                        import ctypes
                        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,pid)
                        if handle:ctypes.windll.kernel32.CloseHandle(handle);continue
                        if ctypes.windll.kernel32.GetLastError()!=87:continue
                    else:
                        try:os.kill(pid,0);continue
                        except ProcessLookupError:pass
                    lease.unlink()
                    # A crash has evidence, and its prior records remain queryable.
                    with (p/'events-9999.jsonl').open('a',encoding='utf-8') as f:
                        f.write(json.dumps(dict(run_id=p.name,timestamp=stamp(),level='ERROR',event='abnormal_exit',message='上次程序未正常关闭；已确认进程不存在',component='runtime',stage='unknown',diagnosis={'category':'process_crash'}),ensure_ascii=False)+'\n')
                except (OSError,ValueError):continue
            files=list(p.iterdir())
            if any(x.is_dir() or x.is_symlink() or not (re.fullmatch(r'events-\d+\.(jsonl|log)',x.name) or re.fullmatch(r'[a-f0-9]{32}\.(stdout|stderr)(?:\.\d+)?\.log',x.name)) for x in files):continue
            dirs.append((p,max((x.stat().st_mtime for x in files),default=p.stat().st_mtime),sum(x.stat().st_size for x in files)))
        total=sum(x[2] for x in dirs)
        for p,mt,size in sorted(dirs,key=lambda x:x[1]):
            if mt<time.time()-self.retention_days*86400 or total>self.total_bytes:
                shutil.rmtree(p);removed.append(str(p));total-=size
        return removed

def resources(path):
    result={'disk_free_bytes':shutil.disk_usage(path).free,'cpu_percent':None,'memory_percent':None,
            'process_memory_bytes':None,'gpu':None}
    try:
        import ctypes
        global _cpu_sample
        if os.name=='nt':
            idle=ctypes.c_ulonglong();kernel=ctypes.c_ulonglong();user=ctypes.c_ulonglong()
            if ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle),ctypes.byref(kernel),ctypes.byref(user)):
                current=(idle.value,kernel.value+user.value)
                if _cpu_sample and current[1]>_cpu_sample[1]:result['cpu_percent']=round(100*(1-(current[0]-_cpu_sample[0])/(current[1]-_cpu_sample[1])),1)
                _cpu_sample=current
            class Counters(ctypes.Structure):
                _fields_=[('cb',ctypes.c_ulong),('faults',ctypes.c_ulong)]+[(n,ctypes.c_size_t) for n in ('peak_ws','ws','peak_paged','paged','peak_nonpaged','nonpaged','pagefile','peak_pagefile')]
            pc=Counters();pc.cb=ctypes.sizeof(pc)
            if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(-1),ctypes.byref(pc),pc.cb):result['process_memory_bytes']=pc.ws
            class Memory(ctypes.Structure):
                _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total','avail','total_page','avail_page','total_virtual','avail_virtual','extended')]
            m=Memory();m.length=ctypes.sizeof(m)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):result['memory_percent']=m.load
        import psutil # optional existing library only
        result['process_memory_bytes']=psutil.Process().memory_info().rss
        if result['cpu_percent'] is None:result['cpu_percent']=psutil.cpu_percent(interval=.05)
    except (ImportError,OSError,AttributeError):pass
    try:
        p=subprocess.run(['nvidia-smi','--query-gpu=memory.used,memory.total,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=3,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if not p.returncode:result['gpu']=[list(map(int,line.split(','))) for line in p.stdout.splitlines()]
    except (OSError,ValueError,subprocess.TimeoutExpired):pass
    return result

def configure(root,**options):
    global _hub
    if _hub and not _hub.closed:_hub.close()
    _hub=Hub(root,**options);return _hub

def hub():return _hub

def emit(event,message='',level='INFO',**fields):
    if _hub:return _hub.emit(event,message,level,**fields)

def error(exc,**fields):
    return emit('exception',str(exc),'ERROR',error_type=type(exc).__name__,traceback=''.join(traceback.format_exception(exc)),
                diagnosis=classify(exc),**fields)

@contextmanager
def bind(**fields):
    token=_context.set(dict(_context.get(),**fields))
    try:yield
    finally:_context.reset(token)

def context_call(fn):
    ctx=copy_context();return lambda *a,**k:ctx.run(fn,*a,**k)

@contextmanager
def stage(name,component=None,**fields):
    start=time.monotonic(); span=uuid.uuid4().hex
    with bind(stage=name,**({'component':component} if component else {})):
        emit('stage_start',STAGES.get(name,name),span_id=span,**fields)
        try:yield
        except BaseException as exc:
            error(exc,span_id=span)
            emit('stage_end',STAGES.get(name,name),span_id=span,elapsed=time.monotonic()-start,status='cancelled' if classify(exc)['category']=='cancelled' else 'failed')
            raise
        else:emit('stage_end',STAGES.get(name,name),span_id=span,elapsed=time.monotonic()-start,status='success',**fields)

def measured(name,component=None):
    def deco(fn):
        @functools.wraps(fn)
        def wrapped(*a,**kw):
            with stage(name,component or fn.__module__):return fn(*a,**kw)
        return wrapped
    return deco

class Task:
    def __init__(self,*,batch_id='',job_id='',number=None,component='render',kind='video',**fields):
        self.fields=dict(batch_id=str(batch_id),job_id=str(job_id),number=number,component=component,kind=kind,
                         attempt_id=uuid.uuid4().hex,**fields)
        self.start=time.monotonic();self.phase_start=self.start;self.phase_name=None;self.times={};self.status='running'
    def __enter__(self):
        self.token=_context.set(dict(_context.get(),**self.fields))
        emit('task_start','任务开始')
        self._active();return self
    def _active(self):
        if _hub:
            with _hub.lock:_hub.active[self.fields['attempt_id']]=dict(self.fields,stage=self.phase_name or '',started_monotonic=self.phase_start)
    def phase(self,name,status='success'):
        at=time.monotonic()
        if self.phase_name:
            elapsed=at-self.phase_start;self.times[self.phase_name]=self.times.get(self.phase_name,0)+elapsed
            emit('stage_end',STAGES.get(self.phase_name,self.phase_name),stage=self.phase_name,elapsed=elapsed,status=status)
        self.phase_name=name;self.phase_start=at
        _context.set(dict(_context.get(),stage=name or ''))
        if name:emit('stage_start',STAGES.get(name,name))
        self._active()
    def finish(self,status='success',exc=None,**fields):
        if self.status!='running':return
        if exc:error(exc,elapsed=time.monotonic()-self.start,cleanup=fields.pop('cleanup','日志层未执行媒体清理；查看所属任务的清理记录'),resume=fields.pop('resume','沿用冻结Manifest重试'))
        self.phase(None,status);self.status=status
        emit('task_end','任务结束',status=status,elapsed=time.monotonic()-self.start,stages=self.times,**fields)
        if _hub:
            with _hub.lock:_hub.active.pop(self.fields['attempt_id'],None)
    def __exit__(self,typ,exc,tb):
        self.finish('failed' if exc else 'success',exc);_context.reset(self.token)
    def summary(self):
        slow=max(self.times,key=self.times.get,default='')
        return f"视频 #{self.fields['number']}｜{self.status}："+'；'.join(f'{STAGES.get(k,k)} {v:.2f}秒' for k,v in self.times.items())+f'；总耗时 {time.monotonic()-self.start:.2f}秒；最慢 {STAGES.get(slow,slow)}'

def query(root,filters=None,limit=10000):
    filters=dict(filters or {});result=deque(maxlen=limit)
    paths=sorted(Path(root).glob('*/events-*.jsonl'),key=lambda p:p.stat().st_mtime)
    batch_alias={filters.get('batch_id','')};job_alias={filters.get('job_id','')};job_pairs=set()
    # Planning happens before the formal batch ID exists. Explicit links join both identities.
    if filters.get('batch_id') or filters.get('job_id'):
        for p in paths:
            try:
                with p.open(encoding='utf-8') as f:
                    for line in f:
                        if '"batch_link"' not in line:continue
                        try:d=json.loads(line)
                        except ValueError:continue
                        if filters.get('batch_id') and filters['batch_id'] in d.get('batch_id',''):
                            batch_alias.add(d.get('source_batch_id',''))
                        for item in d.get('items',[]):
                            if filters.get('job_id') and item.get('job_id')==filters['job_id']:job_pairs.add((d.get('source_batch_id',''),item.get('script_id','')))
            except OSError:continue
    for p in paths:
        try:
            with p.open(encoding='utf-8') as f:
                for line in f:
                    try:d=json.loads(line)
                    except ValueError:continue
                    local=filters
                    if d.get('batch_id') in batch_alias or d.get('job_id') in job_alias or (d.get('batch_id'),d.get('job_id')) in job_pairs:
                        local=dict(filters)
                        if d.get('batch_id') in batch_alias:local.pop('batch_id',None)
                        if d.get('job_id') in job_alias or (d.get('batch_id'),d.get('job_id')) in job_pairs:local.pop('job_id',None)
                    if matches(d,local):result.append(d)
        except OSError:continue # a completed, expired log may rotate during a background query
    return list(result)

def matches(d,f):
    for k in ('batch_id','job_id','component','stage','status'):
        if f.get(k) and str(f[k]) not in str(d.get(k,'')):return False
    if f.get('number') and str(d.get('number',''))!=str(f['number']):return False
    if f.get('level')=='WARNING+ERROR' and LEVELS.get(d.get('level'),0)<30:return False
    if f.get('level') in LEVELS and LEVELS.get(d.get('level'),0)<LEVELS[f['level']]:return False
    if f.get('from') and d.get('timestamp','')<f['from']:return False
    if f.get('to') and d.get('timestamp','')>f['to']:return False
    if f.get('keyword') and f['keyword'].lower() not in json.dumps(d,ensure_ascii=False).lower():return False
    return True

def statistics(records):
    tasks=[d for d in records if d.get('event')=='task_end' and d.get('kind')=='video']
    stages=defaultdict(list)
    for d in tasks:
        for k,v in d.get('stages',{}).items():stages[k].append(v)
    hits=[d for d in records if d.get('event')=='voice_cache']
    failures=defaultdict(int)
    for d in records:
        if d.get('event')=='exception':failures[d.get('stage','unknown')]+=1
    latest={}
    for task in tasks:latest[(task.get('batch_id'),task.get('job_id'))]=task
    batches=[x for x in records if x.get('event')=='task_end' and x.get('kind')=='batch']
    return dict(unique_jobs=len(latest),latest_counts={s:sum(d.get('status')==s for d in latest.values()) for s in ('success','failed','cancelled','skipped')},batch_wall_seconds=[x['elapsed'] for x in batches],attempts=len(tasks),counts={s:sum(d.get('status')==s for d in tasks) for s in ('success','failed','cancelled','skipped')},
                average_seconds=sum(d['elapsed'] for d in tasks)/len(tasks) if tasks else None,
                stages={k:dict(average=sum(v)/len(v),maximum=max(v)) for k,v in stages.items()},
                cache_hit_rate=sum(bool(d.get('cache_hit')) for d in hits)/len(hits) if hits else None,
                failures=dict(failures),slowest=sorted(tasks,key=lambda x:x['elapsed'],reverse=True)[:10])

def export_report(destination,root,*,batch=None,version='',filters=None):
    if _hub:_hub.flush()
    records=query(root,filters,limit=200000)
    if _hub:
        with _hub.lock:records.extend(d for d in _hub.overflow if matches(d,filters or {}))
    # Explicit metadata allowlist; never recursively archive task folders or media.
    summary={'software_version':version,'os':platform.platform(),'python':sys.version,
             'configuration':redact((batch or {}).get('config',{})),
             'task_status':[{'id':i.get('id'),'number':i.get('number'),'status':i.get('status'),'error':redact(i.get('error',''))} for i in (batch or {}).get('items',[])],
             'statistics':statistics(records),'resources':resources(Path(root)),
             'runtime_versions':{}}
    cfg=(batch or {}).get('config',{})
    for name,flag in [('node','--version'),('ffmpeg','-version')]:
        exe=cfg.get(name) or shutil.which(name)
        if exe:
            try:
                p=subprocess.run([exe,flag],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=5,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                summary['runtime_versions'][name]=(p.stdout or p.stderr).splitlines()[0]
            except (OSError,subprocess.TimeoutExpired,IndexError) as exc:summary['runtime_versions'][name]='不可用：'+str(exc)
    dep=Path(cfg.get('remotion_project') or '.')/'node_modules'/'remotion'/'package.json'
    if dep.is_file():summary['runtime_versions']['remotion']=json.loads(dep.read_text(encoding='utf-8')).get('version')
    dest=Path(destination);dest.parent.mkdir(parents=True,exist_ok=True);part=dest.with_suffix('.part.zip')
    try:
        with zipfile.ZipFile(part,'w',zipfile.ZIP_DEFLATED) as z:
            z.writestr('summary.json',json.dumps(redact(summary),ensure_ascii=False,indent=2,default=str))
            z.writestr('events.jsonl',''.join(json.dumps(redact(d),ensure_ascii=False,default=str)+'\n' for d in records))
            z.writestr('errors.txt','\n\n'.join(json.dumps(redact(d),ensure_ascii=False,indent=2) for d in records if d.get('event')=='exception'))
            for d in records:
                if d.get('event')!='process_end':continue
                paths=d.get('stdout_parts',[d.get('stdout_path','')])+d.get('stderr_parts',[d.get('stderr_path','')])
                for source in paths:
                    p=Path(source)
                    # Logs only from diagnostics root; no arbitrary path inclusion.
                    if p.is_file() and p.resolve().is_relative_to(Path(root).resolve()):
                        z.writestr('processes/'+p.name,redact(p.read_text(encoding='utf-8',errors='replace')))
        os.replace(part,dest)
    finally:part.unlink(missing_ok=True)
    emit('diagnostic_export','诊断报告已导出',path=str(dest));return dest

class ProcessFailure(RuntimeError):
    def __init__(self,message,returncode=None):super().__init__(message);self.returncode=returncode

def process(args,*,timeout=90,log=lambda _:None,logfile=None,stage_name='子进程',cancel=None,progress_seconds=0,cwd=None,return_stderr=False):
    """Drain stdout and stderr separately to disk, bounded tails; no unbounded pipe queue."""
    start=time.monotonic(); pid=None; code=None; token=uuid.uuid4().hex
    base=_hub.path if _hub else (Path(logfile).parent if logfile else Path(_portable_path(r'D:\GPTData\temp\assembler_process_logs')))
    try:base.mkdir(parents=True,exist_ok=True)
    except OSError:
        base=Path(_portable_path(r'D:\GPTData\temp\assembler_process_logs'));base.mkdir(parents=True,exist_ok=True)
    out=base/(token+'.stdout.log');err=base/(token+'.stderr.log');tails={'stdout':deque(maxlen=100),'stderr':deque(maxlen=100)}
    captured=[]; total_capture=0; last_output=[start]; display=queue.Queue(maxsize=100)
    stderr_capture=[];stderr_size=[0]
    parts={'stdout':[],'stderr':[]}
    def drain(pipe,path,key):
        nonlocal total_capture
        f=None;size=0;part=0;limit=max(256,int(_hub.file_bytes if _hub else 8*1024**2))
        try:
            try:f=path.open('wb');parts[key].append(str(path))
            except OSError as exc:
                if _hub:_hub.fault='子进程日志无法写入，保留输出尾部：'+str(exc)
            for line in iter(lambda:pipe.readline(4096),''):
                last_output[0]=time.monotonic();line=redact(line)
                if f:
                    try:
                        data=line.encode('utf-8')
                        while data:
                            if size>=limit:
                                f.close();part+=1;size=0
                                next_path=path.with_name(path.stem+f'.{part:04d}.log')
                                f=next_path.open('wb');parts[key].append(str(next_path))
                            chunk=data[:limit-size].decode('utf-8',errors='ignore').encode('utf-8')
                            if not chunk:size=limit;continue  # keep each rotated file valid UTF-8
                            f.write(chunk);size+=len(chunk);data=data[len(chunk):]
                    except OSError as exc:
                        if _hub:_hub.fault='子进程日志写入失败：'+str(exc)
                        try:f.close()
                        except OSError:pass
                        f=None
                tails[key].append(line)
                if key=='stdout' and total_capture<8*1024**2:captured.append(line);total_capture+=len(line)
                if key=='stderr' and stderr_size[0]<8*1024**2:stderr_capture.append(line);stderr_size[0]+=len(line)
                if line.startswith(('ASSEMBLY:','out_time_us=')):
                    try:display.put_nowait(line)
                    except queue.Full:pass
        except OSError as exc:emit('process_log_error',str(exc),'WARNING')
        finally:
            if f:
                try:f.close()
                except OSError:pass
            pipe.close()
    expected=None
    if args and str(args[-1]).lower().endswith(('.wav','.mp4','.png','.mp3','.m4a')):expected=Path(args[-1])
    elif len(args)>2 and str(args[-1]).endswith('.json'):
        try:
            request=json.loads(Path(args[-1]).read_text(encoding='utf-8-sig'))
            if request.get('output'):expected=Path(request['output'])
        except (OSError,ValueError,AttributeError):pass
    p=None;threads=[];failure=None;last_ui=start;warned=False
    emit('process_starting',stage_name,executable=args[0],args=args,cwd=str(cwd or Path.cwd()),timeout=timeout)
    try:
        p=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',cwd=cwd,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        pid=p.pid
        if _hub:
            with _hub.lock:_hub.processes.add(pid)
        emit('process_start',stage_name,pid=pid,executable=args[0],args=args,cwd=str(cwd or Path.cwd()))
        for pipe,path,key in [(p.stdout,out,'stdout'),(p.stderr,err,'stderr')]:
            t=threading.Thread(target=drain,args=(pipe,path,key),daemon=True);t.start();threads.append(t)
        while p.poll() is None:
            at=time.monotonic()
            if cancel and cancel.is_set():raise ProcessFailure(stage_name+'已取消')
            if at-start>timeout:raise ProcessFailure(stage_name+f'明确超时（{timeout}秒）')
            if at-last_output[0]>min(60,timeout/2) and not warned:
                emit('progress_unknown','长时间没有输出，进度暂时无法获取；请检查日志与资源。','WARNING',pid=pid,elapsed=at-start);warned=True
            if at-last_ui>=2:
                lines=[]
                while not display.empty():lines.append(display.get_nowait())
                if lines:
                    log(stage_name+' '+lines[-1].strip().removeprefix('ASSEMBLY:'))
                    emit('process_progress',lines[-1].strip(),pid=pid,elapsed=at-start)
                else:emit('process_heartbeat',f'{stage_name}执行中；进度暂时无法获取',pid=pid,elapsed=at-start)
                last_ui=at
            time.sleep(.01)
        code=p.returncode
        if code:raise ProcessFailure(f'{Path(args[0]).name} 执行失败（退出码 {code}）：'+''.join(tails['stderr'] or tails['stdout'])[-3500:],code)
    except BaseException as exc:failure=exc;raise
    finally:
        if p and p.poll() is None:
            if os.name=='nt':subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],capture_output=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            else:p.terminate()
            try:p.wait(5)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for t in threads:t.join(5)
        if p:code=p.returncode
        if _hub:
            with _hub.lock:_hub.processes.discard(pid)
        if logfile:
            try:
                with Path(logfile).open('a',encoding='utf-8') as f:
                    f.write('\n'+stamp()+' '+json.dumps(redact(args),ensure_ascii=False)+'\n')
                    for path in map(Path,parts['stdout']+parts['stderr']):
                        if path.is_file():
                            with path.open(encoding='utf-8') as src:shutil.copyfileobj(src,f)
            except OSError as exc:emit('legacy_log_error',str(exc),'WARNING')
        emit('process_end',stage_name,'ERROR' if failure else 'INFO',pid=pid,exit_code=code,elapsed=time.monotonic()-start,
             stdout_path=str(out),stderr_path=str(err),stdout_parts=parts['stdout'],stderr_parts=parts['stderr'],stderr_tail=''.join(tails['stderr'])[-3500:],
             executable=args[0],args=args,status='failed' if failure else 'success',output_path=str(expected) if expected else None,output_exists=expected.is_file() if expected else None,output_validity='需查看后续实际完整性检查' if expected else '无文件输出')
    return ''.join(stderr_capture) if return_stderr else ''.join(captured)
