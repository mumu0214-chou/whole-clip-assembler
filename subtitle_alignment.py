"""Audio/text keyed sidecar cache. Does not change or regenerate voice audio."""
from mac_paths import path as _portable_path
from pathlib import Path
from hashlib import sha256
import json
import os
import queue
import subprocess
import threading
import time

ROOT=Path(__file__).resolve().parent
RUNTIME=Path(_portable_path(r'D:\GPTData\asr-projects\ASR_20260907\.venv\Scripts\python.exe'))
MODEL=Path(_portable_path(r'D:\GPTData\asr-projects\ASR_20260907\.models\faster-whisper-large-v3-turbo'))
_lock=threading.Lock()
_process=None
_results=None
_idle=None


def close():
    """Release this client's idle CPU model, never a shared TTS or render process."""
    global _process
    with _lock:
        if _process and _process.poll() is None:
            try:_process.stdin.close()
            except (ValueError,OSError):pass
        _process=None


def _start():
    global _process,_results
    if _process and _process.poll() is None:return
    if not RUNTIME.is_file() or not MODEL.is_dir():
        raise ValueError('本机离线语音对齐环境缺失，需要人工复核；不会下载模型或重新配音')
    folder=ROOT/'data/subtitle_alignment_cache';folder.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,PYTHONUTF8='1',HF_HUB_OFFLINE='1',TEMP=_portable_path(r'D:\GPTData\temp'),TMP=_portable_path(r'D:\GPTData\temp'))
    _process=subprocess.Popen([str(RUNTIME),str(ROOT/'subtitle_alignment_worker.py')],stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE,stderr=(folder/'worker.log').open('a',encoding='utf-8'),
                              text=True,encoding='utf-8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    _results=queue.Queue()
    def reader(process,results):
        for line in process.stdout:
            if line.startswith('RESULT:'):results.put(json.loads(line[7:]))
        results.put({'error':'语音对齐子进程退出，需复核，详见 subtitle_alignment_cache/worker.log'})
    threading.Thread(target=reader,args=(_process,_results),daemon=True).start()


def alignment(audio,text,start,end,verify_content=False,raw_text=None):
    global _idle
    began=time.perf_counter(); audio=Path(audio)
    digest=sha256(audio.read_bytes()).hexdigest()
    identity=dict(audio_sha256=digest,text=text,start=float(start),end=float(end),model=str(MODEL),revision='known_text_dtw_v1')
    if verify_content:
        from transcript_validation import REVISION
        identity.update(content_check=REVISION,validator_sha256=sha256((ROOT/'transcript_validation.py').read_bytes()).hexdigest(),worker_sha256=sha256((ROOT/'subtitle_alignment_worker.py').read_bytes()).hexdigest(),raw_text=raw_text or text)
    key=sha256(json.dumps(identity,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    folder=ROOT/'data/subtitle_alignment_cache';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(key+'.json')
    with _lock:
        if _idle:_idle.cancel()
        if path.is_file():
            result=json.loads(path.read_text(encoding='utf-8'));result['cache_hit']=True
        else:
            _start();_process.stdin.write(json.dumps(dict(audio=str(audio),text=text,start=start,end=end,model=str(MODEL),verify_content=verify_content,raw_text=raw_text),ensure_ascii=False)+'\n');_process.stdin.flush()
            try:result=_results.get(timeout=180)
            except queue.Empty:
                _process.kill();raise ValueError('语音对齐超过180秒，需复核；未生成伪对齐字幕')
            if result.get('error'):
                _idle=threading.Timer(20,close);_idle.daemon=True;_idle.start()
                raise ValueError(result['error'])
            result.update(identity=identity,cache_key=key,cache_hit=False)
            from subtitle_styles import atomic
            atomic(path,result)
        _idle=threading.Timer(20,close);_idle.daemon=True;_idle.start()
    result['request_seconds']=time.perf_counter()-began
    return result
