"""Versioned durable audio, separate from task-owned WAVs and audition cleanup."""
from mac_paths import path as _portable_path
from pathlib import Path
from hashlib import sha256
import json
import os
import shutil
import time
import uuid
import traceback
from file_mutex import file_mutex
import runtime_diagnostics as diag

ROOT = Path(_portable_path(r'D:\DY video\本地配音\formal_voice_cache_v2'))
REVISION = 'formal_voice_v2.1'

def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()

def file_hash(path):
    h = sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def identities(text, profile, models, ref, emotion, provider):
    # Include all actual inference controls, with explicit defaults. UI labels,
    # job IDs and subtitle/BGM settings intentionally do not affect speech.
    fields = ('engine','voice_id','profile_version','reference_text','reference_language',
              'target_language','speed','pause','emotion_mode','emotion_alpha',
              'emotion_text','emotion_vector','preview_seed','sampling_seed','temperature',
              'top_p','top_k','do_sample','num_beams','repetition_penalty','max_mel_tokens')
    source = Path(__file__).parent
    revision = {p.name:file_hash(p) for p in [source/'indextts25_worker.py',
                source/'indextts_runtime.py', source/'voice_pipeline.py'] if p.is_file()}
    from voice_cache_compat import pipeline_revision
    revision['voice_pipeline.py']=pipeline_revision(source/'voice_pipeline.py')
    engine = Path(getattr(provider, 'root', '.'))
    for p in (engine/'indextts/infer_v2_5.py', engine/'checkpoints/config.yaml'):
        if p.is_file(): revision[str(p)] = file_hash(p)
    raw = digest(dict(revision=REVISION, code=revision, text=text, reference_sha256=ref,
                      emotion_sha256=emotion, models=models,
                      parameters={k:profile.get(k) for k in fields}))
    from voice_pipeline import voice_identity_keys, VOICE_POST_REVISION
    legacy = voice_identity_keys(text, profile, models, ref, emotion)[1]
    return raw, digest(dict(raw=raw, post=legacy, revision=VOICE_POST_REVISION))

@diag.measured("cache_lookup","voice-cache")
def valid(folder, ffmpeg):
    from voice_pipeline import cache_state
    try:
        meta = json.loads((folder/'entry.json').read_text(encoding='utf-8'))
        wav = folder/'audio.wav'
        if meta['sha256'] != file_hash(wav): raise ValueError('缓存损坏：音频SHA256不匹配')
        if cache_state(wav, ffmpeg) != 'CACHE_READY': raise ValueError('缓存损坏：音频不能完整解码')
        os.utime(folder/'entry.json', None)
        return meta
    except FileNotFoundError as exc:
        if (folder/'entry.json').is_file():
            diag.emit('cache_invalid','缓存音频缺失，将重新生成','WARNING',path=str(folder),reason=str(exc),diagnosis={'category':'cache_corrupt'})
        return None
    except (OSError, ValueError, KeyError) as exc:
        diag.emit('cache_invalid','缓存验证失败，将重新生成','WARNING',path=str(folder),reason=str(exc),traceback=traceback.format_exc(),diagnosis=diag.classify(exc) if isinstance(exc,OSError) else {'category':'cache_corrupt'})
        return None

def publish(folder, wav, extra=None):
    folder.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    audio = folder/(token+'.part.wav')
    meta = folder/(token+'.part.json')
    try:
        shutil.copyfile(wav, audio)
        metadata = dict(extra or {}, sha256=file_hash(audio), created_at=time.time())
        meta.write_text(json.dumps(metadata, ensure_ascii=False), encoding='utf-8')
        os.replace(audio, folder/'audio.wav')
        os.replace(meta, folder/'entry.json')  # commit marker last
    finally:
        audio.unlink(missing_ok=True); meta.unlink(missing_ok=True)

def prepare(script, profile, provider, ffmpeg, job_root, log, post_submit,
            text, models, reference_hash, emotion_hash, legacy_key, started,
            force=False, root=ROOT):
    from voice_pipeline import _finish_prepared_voice, cache_state, VoiceError
    root, job_root = Path(root), Path(job_root)
    raw_key, key = identities(text, profile, models, reference_hash, emotion_hash, provider)
    raw_store, post_store = root/'raw'/raw_key, root/'processed'/key
    token = uuid.uuid4().hex
    raw, processed = job_root/'raw'/(raw_key+'-'+token+'.wav'), job_root/'processed'/(key+'-'+token+'.wav')
    raw.parent.mkdir(parents=True, exist_ok=True); processed.parent.mkdir(parents=True, exist_ok=True)
    raw_result = dict(text_sha256=sha256(text.encode()).hexdigest(), tts_generation_seconds=0,
                      synthesis_seconds=0, stages={})
    def finish():
        with file_mutex(root/'locks'/('post-'+key+'.lock')):
            hit = None if force else valid(post_store, ffmpeg)
            if hit and hit.get('raw_sha256') != file_hash(raw): hit = None
            if hit:
                shutil.copyfile(post_store/'audio.wav', processed)
                log(('正式配音缓存命中，跳过 TTS：' if raw_hit else '后处理缓存命中：')+key[:12])
            result = _finish_prepared_voice(raw, processed, profile, ffmpeg, False,
                       'CACHE_READY' if hit else 'CACHE_MISSING', legacy_key, models,
                       log, started, dict(raw_result, cache_hit=bool(hit and raw_hit), post_cache_hit=bool(hit), persistent_cache_key=key), script)
            if not hit: publish(post_store, processed, {'raw_sha256':file_hash(raw)})
            result.update(persistent_cache_key=key, persistent_raw_key=raw_key, cache_hit=bool(hit and raw_hit))
            diag.emit("voice_cache","正式配音缓存",cache_hit=bool(hit and raw_hit),raw_hit=bool(raw_hit),post_hit=bool(hit),force=bool(force),cache_key=key)
            return result
    # Lock raw independently from post: retains the existing GPU/CPU pipeline.
    with file_mutex(root/'locks'/('raw-'+raw_key+'.lock')):
        raw_hit = None if force else valid(raw_store, ffmpeg)
        if raw_hit:
            shutil.copyfile(raw_store/'audio.wav', raw)
        else:
            log('正式配音缓存未命中'+('（本次强制）' if force else '')+'，调用 TTS：'+raw_key[:12])
            provider.request_context = {k:script.get(k) for k in ('video_id','job_id','script_id')}
            try:
                provider.synthesize(text, profile, raw)
                if cache_state(raw, ffmpeg) != 'CACHE_READY': raise VoiceError('TTS 缓存音频无法完整解码')
                raw_result = dict(getattr(provider,'last_result',{}) or {}, cache_raw_hit=False)
                publish(raw_store, raw)
            except Exception:
                raw.unlink(missing_ok=True)
                raise
        raw_result['cache_raw_hit'] = bool(raw_hit)
    return post_submit(finish) if post_submit is not None else finish()

def stats(root=ROOT):
    root=Path(root)
    files=list(root.rglob('*')) if root.is_dir() else []
    return dict(entries=sum(p.name=='entry.json' for p in files),
                bytes=sum(p.stat().st_size for p in files if p.is_file()), root=str(root))

def unused_candidates(data_dir, root=ROOT, days=7):
    """Only committed entries older than seven days and unreferenced by manifests."""
    protected=set()
    for manifest in Path(data_dir).rglob('*.json'):
        try:
            content=manifest.read_text(encoding='utf-8-sig')
            import re
            protected.update(re.findall(r'"persistent_(?:cache|raw)_key"\s*:\s*"([0-9a-f]{64})"',content))
        except OSError: pass
    return [p.parent for p in Path(root).glob('*/*/entry.json')
            if p.parent.parent.name in ('raw','processed') and len(p.parent.name)==64 and all(c in '0123456789abcdef' for c in p.parent.name) and p.parent.name not in protected and p.stat().st_mtime < time.time()-days*86400]

def remove_candidates(candidates, data_dir, root=ROOT):
    root=Path(root).resolve(); allowed=set(unused_candidates(data_dir,root)); count=0
    for folder in candidates:
        folder=Path(folder)
        if folder not in allowed or folder.is_symlink() or not folder.resolve().is_relative_to(root): continue
        try:
            with file_mutex(root/'locks'/(('post-' if folder.parent.name=='processed' else 'raw-')+folder.name+'.lock'), timeout=0):
                if folder not in set(unused_candidates(data_dir,root)): continue
                if any(p.is_dir() or p.is_symlink() or p.name not in ('audio.wav','entry.json') and not p.name.endswith(('.part.wav','.part.json')) for p in folder.iterdir()): continue
                shutil.rmtree(folder); count+=1
        except TimeoutError: pass
    return count
