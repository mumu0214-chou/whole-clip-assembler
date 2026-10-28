"""Detached resume of a saved batch, using the application's existing voice/render paths."""
from __future__ import annotations

import argparse
from collections import Counter
import os
from pathlib import Path
import threading
import traceback

from core import (Store, atomic_json, instance_lock, now, read_json,
                  resolve_tool, validate_config)
from script_library import ScriptLibrary
from voice_pipeline import IndexTTS25Provider, discover_profiles
from voice_output_paths import validate_output_path, PATH_POLICY_VERSION


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch', type=Path, required=True)
    args = parser.parse_args()
    path = args.batch.resolve()
    data_dir = path.parent.parent.parent
    state_path = path.parent/'worker_state.json'
    guard = threading.RLock()
    stop = threading.Event()
    state = {'job_id':path.parent.name, 'pid':os.getpid(), 'started_at':now(),
             'state':'STARTING', 'formal_tts_success_count':0, 'failures':[]}

    def update(**values):
        with guard:
            state.update(values, heartbeat_at=now())
            atomic_json(state_path, state)

    def log(message):
        print(f'{now()} {message}', flush=True)

    def heartbeat():
        while not stop.wait(15):
            update()
            log(f"HEARTBEAT job={state['job_id']} pid={os.getpid()} state={state['state']}")

    # The OS file lock remains held for the entire batch and rejects duplicate GUI/CLI workers.
    with instance_lock(data_dir):
        update()
        atomic_json(path.parent/'job_pid.json', {'job_id':state['job_id'], 'pid':os.getpid(),
                                                'manifest':str(path), 'started_at':state['started_at']})
        heartbeat_thread=threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            from batch_retention import ensure_resumable,initialize,sweep
            store = Store(data_dir)
            batch = ensure_resumable(path,store)
            initialize(batch,path,store)
            atomic_json(path,batch)
            cfg = batch['config']
            validate_config(cfg)
            job_root = Path(cfg['voice_job_temp_dir']).resolve()
            if not (job_root/'.assembler_job_temp').is_file():
                raise RuntimeError('Missing owned Job Temp marker')
            needs_local=any(i.get('narration_enabled') and not i.get('voice') and i.get('voice_source',cfg.get('voice_source','indextts25'))!='external_files' and i.get('status') not in ('completed','DONE') for i in batch['items'])
            if needs_local:validate_output_path(job_root/'voice'/'startup_probe.wav')
            current = {p['voice_id']:p for p in discover_profiles() if p.get('available')} if needs_local else {}
            for item in batch['items']:
                if item.get('voice') or item.get('status') in ('completed','DONE','rendering','RENDERING'):continue
                if item.get('narration_enabled') and item.get('voice_source',cfg.get('voice_source','indextts25'))!='external_files' and item['script_package']['voice_profile'] not in current:
                    raise RuntimeError('Frozen voice is no longer available: '+item['script_package']['voice_profile'])
            store = Store(data_dir)
            scripts = ScriptLibrary(cfg.get('script_library_path') or data_dir/'script_library.sqlite3')
            provider = None
            if needs_local:
                provider = IndexTTS25Provider(log=log)
                provider.ensure_ready()
                if provider._health().get('path_policy') != PATH_POLICY_VERSION:
                    raise RuntimeError('IndexTTS worker has not loaded the fixed output path policy')
                if provider._health().get('performance_version')!='runtime_v1':
                    raise RuntimeError('Worker has not loaded the verified performance scheduler')
            ffmpeg = resolve_tool('ffmpeg', cfg['ffmpeg'])
            update(state='PIPELINING',total=len(batch['items']),output_root=cfg['output_root'],
                   formal_tts_success_count=sum(bool(i.get('voice')) and i.get('voice_source')!='external_files' for i in batch['items']),
                   external_voice_reuse_count=sum(bool(i.get('voice')) and i.get('voice_source')=='external_files' for i in batch['items']))
            from voice_production_pipeline import run_pipeline
            result=run_pipeline(path,batch,store,scripts,provider,ffmpeg,log=log,update=update)
            counts = dict(Counter(i.get('status') for i in result['items']))
            completed = counts.get('completed', 0) == len(result['items'])
            update(state='COMPLETED' if completed else 'NEEDS_ATTENTION', counts=counts, finished_at=now(),
                   failures=[{'item':i['number'],'error':i.get('error')} for i in result['items'] if i.get('error')])
            log(f'WORKER FINISHED {counts}')
        except Exception as exc:
            update(state='FAILED', error=str(exc), finished_at=now())
            traceback.print_exc()
            raise
        finally:
            stop.set()
            heartbeat_thread.join(timeout=2)
            from batch_retention import sweep
            try:sweep(Store(data_dir),only=path)
            except Exception as exc:log('HISTORY_CLEANUP_ERROR '+str(exc))


if __name__ == '__main__':
    main()
