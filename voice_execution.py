"""Execution order only: one GPU producer, bounded CPU post-processing consumers."""
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import time,uuid
import runtime_diagnostics as diag

from voice_pipeline import prepare_voice, VoiceError


def execution_key(package):
    profile=package['voice_request_profile']
    return (profile['voice_id'],str(profile['profile_version']),profile.get('emotion_mode','speaker'),
            package.get('reference_sha256',''),str(Path(profile['reference_path']).resolve()),
            package.get('emotion_reference_sha256',''),str(profile.get('emotion_reference_path','')),
            float(profile.get('emotion_alpha',1)),profile.get('emotion_text',''),
            json.dumps(profile.get('emotion_vector',[0.]*8)))


def execute_packages(packages, provider, ffmpeg, job_root, *, on_ready=lambda p:None,
                     log=lambda _:None, grouped=True, pipeline=True,stop_requested=lambda:False, cache_enabled=True, force_regenerate=False):
    """Mutates each original package, never reorders Plan packages or voice assignments."""
    pending=[p for p in packages if p and p.get('narration_enabled') and not p.get('voice')]
    tasks=sorted(pending,key=execution_key) if grouped else pending
    with ThreadPoolExecutor(max_workers=1,thread_name_prefix='voice-post') as cpu:
        waiting=[]
        def finish(package,future):
            with diag.bind(**package.get("diagnostic_context",{})):
                return finish_bound(package,future)
        def finish_bound(package,future):
            try:
                voice=future.result() if hasattr(future,'result') else future
                actual=voice.get('performance',{})
                text=package.get('speech_text_override','').strip() or package['speech_text']
                if actual.get('text_sha256') and actual['text_sha256']!=sha256(text.encode()).hexdigest():
                    raise VoiceError('队列文案hash与冻结Plan不一致')
                if actual.get('voice_id')!=package['voice_request_profile']['voice_id']:
                    raise VoiceError('队列voice_id与冻结Plan不一致')
                for key in ('video_id','job_id','script_id'):
                    if package.get(key) and actual.get(key)!=package[key]:
                        raise VoiceError('队列结果身份错配：'+key)
                if package.get('frozen_model_versions') and voice['model_versions']!=package['frozen_model_versions']:
                    raise VoiceError('冻结的模型版本已变化')
                if package.get('voice_speed_snapshot'):
                    from voice_speed import apply as apply_voice_speed
                    voice=apply_voice_speed(voice,package['voice_speed_snapshot'],ffmpeg,Path(job_root)/'voice_rate')
                if diag.hub():
                    with diag.hub().lock:measured=dict(diag.hub().stage_totals.get(package['diagnostic_context']['attempt_id'],{}))
                    voice['diagnostic']={**package['diagnostic_context'],'run_id':diag.hub().run_id,'started_monotonic':package['diagnostic_started'],'stages':measured}
                package['voice']=voice
                package.pop('voice_error',None)
            except Exception as exc:
                diag.error(exc,component='voice-post')
                package['voice_error']=str(exc)
            diag.emit("voice_request_end","配音请求结束",elapsed=time.monotonic()-package.get("diagnostic_started",time.monotonic()),status="failed" if package.get("voice_error") else "success")
            on_ready(package)
        for package in tasks:
            if stop_requested():break
            # A bounded handoff keeps job WAVs and CPU memory from accumulating.
            if len(waiting)>=2:
                finish(*waiting.pop(0))
            package['diagnostic_started']=time.monotonic()
            package['diagnostic_context']=dict(batch_id=package.get('job_id',str(Path(job_root).name)),job_id=package.get('video_id',package.get('script_id','')),
                number=package.get('diagnostic_number'),attempt_id=uuid.uuid4().hex,component='voice',stage='voice')
            with diag.bind(**package['diagnostic_context']):
                diag.emit('voice_request_start','开始配音/检查缓存')
                try:
                    def submit(fn):return cpu.submit(diag.context_call(fn))
                    future=prepare_voice(package,package['voice_request_profile'],provider,ffmpeg,
                          cache_root=Path(job_root)/'voice',cache_enabled=cache_enabled,force_regenerate=force_regenerate,log=log,
                          post_submit=submit if pipeline else None)
                    if pipeline:waiting.append((package,future))
                    else:finish(package,future)
                except Exception as exc:
                    diag.error(exc,component='voice')
                    package['voice_error']=str(exc);on_ready(package)
            while waiting and waiting[0][1].done():
                finish(*waiting.pop(0))
        for package,future in waiting:finish(package,future)
    return packages
