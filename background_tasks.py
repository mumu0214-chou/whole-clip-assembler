"""Independent UI lanes and conservative, cross-process single-GPU scheduling."""
from mac_paths import path as _portable_path
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from pathlib import Path
import hashlib
import json
import shutil
import threading
import time
import uuid
from file_mutex import file_mutex
import runtime_diagnostics as diag

class TaskCancelled(RuntimeError):pass

def gpu_serial(fn):
    @wraps(fn)
    def call(provider,*args,**kwargs):
        cancel=getattr(provider,'cancel_event',None)
        deadline=time.monotonic()+getattr(provider,'resource_timeout',300)
        first=True
        with diag.stage('resource_wait','scheduler'):
            while True:
                if cancel and cancel.is_set():raise TaskCancelled('试听已取消，未影响正式视频。')
                if time.monotonic()>deadline:raise TimeoutError('等待GPU资源超时，正式任务继续；可稍后重新提交试听。')
                lease=file_mutex(Path(getattr(provider,'gpu_lock_path',_portable_path(r'D:\GPTData\temp\assembler_gpu_inference.lock'))),timeout=.1)
                try:lease.__enter__()
                except TimeoutError:
                    if first:provider.log('等待GPU资源：已有配音推理，排队期间可以继续编辑。');first=False
                    continue
                try:
                    # Existing resident model needs headroom for inference, not a second model.
                    from voice_production_pipeline import gpu_headroom
                    if gpu_headroom():break
                    if first:provider.log('等待GPU资源：显存余量不足。');first=False
                except BaseException:
                    lease.__exit__(None,None,None);raise
                lease.__exit__(None,None,None);time.sleep(.2)
        try:
            if cancel and cancel.is_set():raise TaskCancelled('试听已取消，未启动推理。')
            with diag.stage('voice','indextts'):
                result=fn(provider,*args,**kwargs)
            # A healthy in-flight HTTP synthesis cannot safely be aborted server-side.
            # Discard only this audition's result after it finishes; never kill the shared worker.
            if cancel and cancel.is_set():raise TaskCancelled('试听已取消；本次推理已安全结束，正式视频继续。')
            return result
        finally:lease.__exit__(None,None,None)
    return call

def worker_start_serial(fn):
    @wraps(fn)
    def call(provider,*a,**kw):
        with file_mutex(Path(_portable_path(r'D:\GPTData\temp\assembler_worker_start.lock')),timeout=500):
            return fn(provider,*a,**kw)
    return call


def snapshot_profiles(profiles,job_root):
    """Copy references by content before allowing the user to edit their source profiles."""
    root=Path(job_root)/'snapshot'/'references';root.mkdir(parents=True,exist_ok=True)
    result=deepcopy(profiles)
    for profile in result:
        if not profile.get('available'):continue
        for key in ('reference_path','emotion_reference_path'):
            source=Path(profile.get(key,'') or '.')
            if not source.is_file():continue
            data=source.read_bytes();digest=hashlib.sha256(data).hexdigest()
            dest=root/(digest+source.suffix.lower())
            if not dest.exists():dest.write_bytes(data)
            profile[key]=str(dest)
            profile[key+'_sha256']=digest
    return result

def write_snapshot(job_root,cfg,scripts,profiles):
    from core import atomic_json,object_hash
    snapshot=dict(revision=1,config=deepcopy(cfg),scripts=deepcopy(scripts),voices=deepcopy(profiles))
    fingerprint=object_hash(snapshot);snapshot['sha256']=fingerprint
    path=Path(job_root)/'snapshot'/'configuration.json';atomic_json(path,snapshot)
    return dict(path=str(path),sha256=fingerprint)

class Lanes:
    def __init__(self,events):self.events=events;self.active={};self.lock=threading.RLock()
    def running(self,lane):
        with self.lock:return lane in self.active
    def submit(self,lane,worker,done,*,on_error=None,context=None):
        with self.lock:
            if lane in self.active:raise RuntimeError('该类任务正在执行；可继续编辑或取消试听。')
            token=uuid.uuid4().hex;cancel=threading.Event();self.active[lane]=(token,cancel)
        context=dict(context or {},lane=lane)
        def run():
            try:
                with diag.bind(**context):
                    with diag.Task(job_id=token,component='ui-worker',kind='audition' if lane.startswith('voice') else 'batch'):
                        value=worker()
                self.events.put(('lane_done',lane,token,done,value,on_error))
            except BaseException as exc:
                self.events.put(('lane_error',lane,token,on_error,exc,None))
        threading.Thread(target=run,name='assembler-'+lane,daemon=True).start()
        return cancel
    def complete(self,lane,token):
        with self.lock:
            if self.active.get(lane,(None,))[0]==token:self.active.pop(lane,None)
    def cancel(self,lane):
        with self.lock:
            if lane in self.active:self.active[lane][1].set()
    def cancel_event(self,lane):
        with self.lock:return self.active.get(lane,(None,None))[1]

def snapshot_batch_references(batch,root):
    from voice_pipeline import _file_hash
    for item in batch['items']:
        package=item.get('script_package') or {}
        profile=package.get('voice_request_profile')
        if not profile:continue
        for key,hash_key in [('reference_path','reference_sha256'),('emotion_reference_path','emotion_reference_sha256')]:
            source=Path(profile.get(key,'') or '.')
            expected=package.get(hash_key)
            if source.is_file() and expected and _file_hash(source)!=expected:
                raise ValueError('冻结批次参考音频内容已改变，不能替换原参考：'+str(source))
        copied=snapshot_profiles([dict(profile,available=True)],root)[0]
        package['voice_request_profile']=copied
