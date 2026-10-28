"""Resume a frozen batch: grouped single GPU producer and one video consumer."""
from copy import deepcopy
from queue import Queue
from pathlib import Path
import threading
import time
import subprocess
import runtime_diagnostics as diag

from core import atomic_json,validate_item,read_json,render_batch,write_summary,now
from voice_pipeline import (freeze_script_on_item,cache_state,VoiceError,voice_identity_keys,
                            model_versions,_file_hash,_finish_prepared_voice)
from voice_execution import execute_packages
from video_plan_policy import apply_item_timing


def recover_owned_handoff(package,provider,ffmpeg,job,claimed,log=lambda _:None):
    """Recover a crash between successful post and Manifest checkpoint, within this job only.

    Not a cross-job WAV cache: matching identity hashes AND paired UUID are required.
    Ambiguous files are never adopted. New pipeline checkpoints before timing gates.
    """
    job=Path(job).resolve()
    if not (job/'.assembler_job_temp').is_file():return None
    profile=package['voice_request_profile'];models=model_versions(provider.root,profile)
    text=package.get('speech_text_override','').strip() or package['speech_text']
    ref=_file_hash(profile['reference_path']);emo=_file_hash(profile['emotion_reference_path']) if profile.get('emotion_mode')=='audio' else ''
    if package.get('reference_sha256') and ref!=package['reference_sha256']:return None
    if package.get('emotion_reference_sha256') and emo!=package['emotion_reference_sha256']:return None
    keys=voice_identity_keys(text,profile,models,ref,emo)
    raw=[p for p in (job/'voice'/'raw').glob(keys[0]+'-*.wav') if p.resolve() not in claimed]
    post=[p for p in (job/'voice'/'processed').glob(keys[1]+'-*.wav') if p.resolve() not in claimed]
    if len(raw)!=1 or len(post)!=1 or raw[0].stem.split('-')[-1]!=post[0].stem.split('-')[-1]:return None
    if not all(p.resolve().is_relative_to(job) and not p.is_symlink() and cache_state(p,ffmpeg)=='CACHE_READY' for p in (*raw,*post)):return None
    if package.get('frozen_model_versions') and models!=package['frozen_model_versions']:return None
    voice=_finish_prepared_voice(raw[0],post[0],profile,ffmpeg,False,'CACHE_READY',keys[1],models,log,
                                time.perf_counter(),{'recovered_owned_handoff':True},package)
    if package.get('voice_speed_snapshot'):
        from voice_speed import apply as apply_voice_speed
        voice=apply_voice_speed(voice,package['voice_speed_snapshot'],ffmpeg,job/'voice_rate')
    claimed.update((raw[0].resolve(),post[0].resolve()))
    log('RECOVERED_OWNED_VOICE_HANDOFF '+package['video_id'])
    return voice


def gpu_headroom(log=lambda _:None):
    """Existing Remotion software rendering remains unchanged; pause new Raw at high VRAM."""
    try:
        result=subprocess.run(['nvidia-smi','--query-gpu=memory.used,memory.total',
                               '--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        used,total=map(int,result.stdout.splitlines()[0].split(','))
        available=(total-used)*1024**2
        # PyTorch's idle allocator reserve is reusable by the same persistent model.
        # Counting only driver-free VRAM otherwise blocks the second request forever.
        try:
            import urllib.request,json
            with urllib.request.urlopen('http://127.0.0.1:9881/health',timeout=2) as response:health=json.load(response)
            if not health.get('active_inference'):
                available+=max(0,health.get('cuda_reserved_bytes',0)-health.get('cuda_allocated_bytes',0))
        except (OSError,ValueError):pass
        return available>=640*1024**2
    except Exception:
        # One GPU inference and one software video consumer still bound concurrency.
        return True


def run_pipeline(path,batch,store,scripts,provider,ffmpeg,*,log=lambda _:None,
                 update=lambda **kw:None,render=render_batch,pause=None,cancel=None,on_item=lambda i:None):
    from batch_retention import run_with_retention
    return run_with_retention(path,store,lambda:_run_pipeline(path,batch,store,scripts,provider,ffmpeg,log=log,update=update,render=render,pause=pause,cancel=cancel,on_item=on_item),batch)


def _run_pipeline(path,batch,store,scripts,provider,ffmpeg,*,log=lambda _:None,
                  update=lambda **kw:None,render=render_batch,pause=None,cancel=None,on_item=lambda i:None):
    path=Path(path);cfg=batch['config'];job=Path(cfg['voice_job_temp_dir'])
    from review_session import blocked, observe
    if blocked(batch):
        if pause is not None: pause.set()
        log('首条已完成，等待审片；请接受当前参数或按新参数继续。')
        return batch
    batch.pop('reservation_state',None)
    # Queue holds metadata only. Already-ready media must not stall the GPU producer
    # behind a long render; Raw->post handoff is independently bounded to two tasks.
    guard=threading.RLock();queue=Queue();errors=[];queued=set()
    master={i['id']:i for i in batch['items']}
    claimed={Path(i['voice'][k]).resolve() for i in batch['items'] if i.get('voice') for k in ('voice_raw','voice_processed')}
    def stopping():return bool(blocked(batch) or (pause and pause.is_set()) or (cancel and cancel.is_set()))
    def checkpoint():
        with guard:atomic_json(path,batch)
    def notify(item):
        with guard:
            observe(batch,item,pause)
            master[item['id']].update(deepcopy(item));checkpoint()
        if item.get('status') in ('completed','failed','VOICE_FAILED','RENDER_BLOCKED'):
            scripts.finish_item(item,item['status']=='completed')
        update(current_item=item['number'],phase=item.get('status'))
        on_item(deepcopy(item))
    def consume():
        while True:
            item=queue.get()
            try:
                if item is None:return
                if stopping():
                    diag.emit('queue_cancelled','取消/暂停后保留原方案',batch_id=batch['id'],job_id=item['id'],number=item['number'],status='cancelled')
                    continue
                diag.emit('stage_end','视频队列等待',batch_id=batch['id'],job_id=item['id'],number=item['number'],stage='queue_wait',elapsed=time.monotonic()-item.get('diagnostic_enqueued',time.monotonic()),status='success')
                with guard:
                    view={k:deepcopy(v) for k,v in batch.items() if k!='items'}
                    view['items']=[deepcopy(item)]
                    from voice_references import paths as voice_paths
                    view['protected_voice_paths']=voice_paths(batch['items'],item['id'])
                dispatch=path.parent/'render_dispatch'/f'{item["id"]}.json'
                dispatch.parent.mkdir(exist_ok=True);atomic_json(dispatch,view)
                result=render(dispatch,store,log=log,on_item=notify,pause=pause,cancel=cancel)
                notify(result['items'][0])
                dispatch.unlink(missing_ok=True)
            except Exception as exc:
                diag.error(exc,component='video-consumer',job_id=item.get('id','') if item else '',batch_id=batch['id'])
                if item is not None:
                    item.update(status='failed',error=str(exc))
                    try:notify(item)
                    except Exception as recording_error:
                        log('VIDEO_RESULT_RECORD_ERROR '+str(recording_error))
                errors.append(str(exc));log('VIDEO_CONSUMER_ERROR '+str(exc))
            finally:queue.task_done()
    def enqueue(item):
        if item['id'] not in queued:
            item['diagnostic_enqueued']=time.monotonic()
            diag.emit('stage_start','进入视频队列',batch_id=batch['id'],job_id=item['id'],number=item['number'],stage='queue_wait',queue_length=queue.qsize())
            queued.add(item['id']);queue.put(item)
    def ready(item,package=None):
        try:
            with guard:
                if package:
                    if package.get('voice_error'):
                        item.update(status='VOICE_FAILED',error=package['voice_error']);checkpoint()
                        scripts.finish_item(item,False);on_item(deepcopy(item));return
                    voice=package['voice'];previous=item.get('effective_voice_duration')
                    if item.get('voice_cache_key') and item['voice_cache_key']!=voice['voice_cache_key']:
                        raise VoiceError('冻结音色参数或模型版本变化；未替换画面方案。')
                    item['voice']=voice # preserve a successful Raw/Processed even if a later timing gate blocks.
                    item['voice_temp_version']='job_temp_v1'
                    if previous and abs(previous-voice['effective_voice_duration'])>1/cfg['fps']:
                        raise VoiceError('配音时长变化超过一帧；保持原画面方案等待处理。')
                    freeze_script_on_item(item,package,cfg);apply_item_timing(item,cfg)
                validate_item(item,cfg)
                item.update(status='planned');item.pop('error',None);checkpoint()
            enqueue(item)
        except Exception as exc:
            diag.error(exc,component='handoff',job_id=item['id'],batch_id=batch['id'],number=item['number'])
            with guard:
                item.update(status='RENDER_BLOCKED',error=str(exc));checkpoint()
            log(f'RENDER_BLOCKED item={item["number"]} {exc}')
            scripts.finish_item(item,False)
            on_item(deepcopy(item))
        finally:
            update(formal_tts_success_count=sum(bool(i.get('voice')) and i.get('voice_source')!='external_files' for i in batch['items']),
                   external_voice_reuse_count=sum(bool(i.get('voice')) and i.get('voice_source')=='external_files' for i in batch['items']))
    store.reserve(batch);scripts.reserve_batch(batch)
    consumer=threading.Thread(target=diag.context_call(consume),name='video-consumer',daemon=True);consumer.start()
    pending=[]
    try:
        for item in batch['items']:
            try:
                if item.get('status') in ('completed','DONE'):continue
                if not item.get('narration_enabled'):ready(item);continue
                voice=item.get('voice') or {}
                if voice:
                    from voice_speed import restore_missing
                    if restore_missing(voice,ffmpeg):log('已从原冻结配音恢复同Hash派生副本：'+item['id'])
                    if all(cache_state(Path(voice[k]),ffmpeg)=='CACHE_READY' for k in ('voice_raw','voice_processed')):
                        ready(item)
                    else:
                        item.update(status='RENDER_BLOCKED',error='已生成的配音文件缺失或损坏；保留记录，未自动重配。');checkpoint()
                        scripts.finish_item(item,False)
                    continue
                package=deepcopy(item['script_package'])
                if item.get('voice_source')=='external_files':
                    item.update(status='RENDER_BLOCKED',error='冻结的外部配音缺失，不能重新轮换或调用本地配音。');checkpoint();scripts.finish_item(item,False);continue
                if not package.get('voice_request_profile'):raise VoiceError('Manifest缺少冻结音色参数，不能重选音色。')
                package.update(video_id=item['id'],job_id=batch['id'],diagnostic_number=item['number'])
                recovered=recover_owned_handoff(package,provider,ffmpeg,job,claimed,log)
                if recovered:
                    package['voice']=recovered;ready(item,package);continue
                pending.append(package)
            except Exception as exc:
                if cfg.get('batch_failure_policy')!='skip_recompose_v1':raise
                item.update(status='RENDER_BLOCKED',error=str(exc));checkpoint()
                scripts.finish_item(item,False);on_item(deepcopy(item))
                log(f'第{item["number"]}条交接失败，记录并继续后续：{exc}')
        # Check only between requests. A healthy in-flight GPU request is never interrupted.
        execute_packages(pending,provider,ffmpeg,job,on_ready=lambda p:ready(master[p['video_id']],p),log=log,stop_requested=stopping,cache_enabled=cfg.get('voice_cache_enabled',True),force_regenerate=cfg.get('voice_force_regenerate',False))
    finally:
        queue.put(None);queue.join();consumer.join()
        if cancel is not None and cancel.is_set():
            scripts.abandon_batch(batch);store.abandon(batch['id']);batch['reservation_state']='released'
        batch['last_run_at']=now();checkpoint();write_summary(path,batch)
        diag.emit('batch_summary','流水线批次结束',batch_id=batch['id'],counts={s:sum(i.get('status')==s for i in batch['items']) for s in ('completed','failed','VOICE_FAILED','planned','RENDER_BLOCKED')})
    return batch
