"""Keep only unfinished canonical batches for 72 hours after the latest run start.

Successful usage is a dedup ledger, not a resumable history. Output media and
shared voice resources are never targets of this lifecycle.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import shutil
import sqlite3

from file_mutex import file_mutex

REVISION = 'unfinished_history_72h_v1'
HOURS = 72
INTERVAL_MS = 10 * 60 * 1000
DONE = frozenset(('completed', 'DONE'))


def _time(value):
    if not value:
        return None
    result = datetime.fromisoformat(value)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _clock(value=None):
    return value or datetime.now(timezone.utc)


def owned(path, store):
    path = Path(path)
    return (path.name == 'batch_manifest.json' and path.parent.parent.resolve() == (store.root/'batches').resolve()
            and not path.is_symlink() and not path.parent.is_symlink() and not path.parent.is_junction())


def _lock(path, store, timeout=0):
    from core import read_json
    bid=read_json(Path(path),{}).get('id',Path(path).parent.name)
    if not isinstance(bid,str) or Path(bid).name!=bid or bid in ('.','..') or '/' in bid or '\\' in bid:
        raise ValueError('历史批次锁身份不合法。')
    return file_mutex(store.root/'batch_history_locks'/(bid+'.lock'), timeout=timeout)


def _row(store, bid):
    with store.connect() as db:
        row = db.execute('SELECT * FROM batch_retention WHERE batch_id=?', (bid,)).fetchone()
        return dict(zip(('batch_id','last_started_at','expires_at','state','completed_count'), row)) if row else None


def initialize(batch, path, store, at=None):
    if not owned(path, store):
        return None
    from batch_history import REVISION as FOLDER_REVISION,directory_name
    folder_valid=(batch.get('id')==Path(path).parent.name or
        (batch.get('history_folder_revision')==FOLDER_REVISION and batch.get('history_folder_name')==Path(path).parent.name==directory_name(batch)))
    if not folder_valid or batch.get('schema_version') != 1 or not isinstance(batch.get('items'), list):
        raise ValueError('历史批次身份或格式不合法，未清理。')
    row = _row(store, batch['id'])
    if row is None:
        meta = batch.get('history_retention', {})
        # Legacy manifests did not record run-start. Use their latest execution
        # checkpoint, then creation; simply opening the app never grants 72h.
        anchor = _time(meta.get('last_started_at') or batch.get('last_run_at') or batch.get('created_at'))
        anchor = anchor or datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc)
        expires = anchor + timedelta(hours=HOURS)
        with store.connect() as db:
            db.execute('INSERT OR IGNORE INTO batch_retention VALUES(?,?,?,?,?)',
                       (batch['id'],anchor.isoformat(),expires.isoformat(),'open',int(meta.get('completed_count',0))))
        row = _row(store, batch['id'])
    batch['history_retention'] = dict(revision=REVISION,hours=HOURS,last_started_at=row['last_started_at'],expires_at=row['expires_at'],completed_count=int(batch.get('history_retention',{}).get('completed_count',0)))
    return row


def ensure_resumable(path, store, *, at=None):
    from core import read_json, AssemblyError
    path = Path(path)
    if not path.is_file():
        raise AssemblyError('历史组合已完成或已超过三天保留期，不能继续；成片请到输出目录查看。')
    batch = read_json(path)
    if owned(path,store):
        row = initialize(batch,path,store,at)
        from sk_materials import active as sk_active
        sk_pending=sk_active(batch.get('config',{})) and not batch.get('abandoned_at') and any(i.get('status') not in DONE for i in batch['items'])
        if row['state'] != 'open' or (_clock(at) >= _time(row['expires_at']) and not sk_pending):
            raise AssemblyError('未完成组合已超过三天保留期，不能重新启用。')
    return batch


@contextmanager
def run_lease(path, store, batch=None):
    """OS releases protection after a crash; reading history cannot renew it."""
    from core import atomic_json
    if not owned(path,store):
        yield
        return
    with _lock(path,store,timeout=2):
        current = ensure_resumable(path,store)
        clock = _clock()
        with store.connect() as db:
            db.execute('UPDATE batch_retention SET last_started_at=?,expires_at=? WHERE batch_id=? AND state=\'open\'',
                       (clock.isoformat(),(clock+timedelta(hours=HOURS)).isoformat(),current['id']))
        row = _row(store,current['id'])
        current['history_retention'].update(last_started_at=row['last_started_at'],expires_at=row['expires_at'])
        atomic_json(Path(path),current)
        if batch is not None:
            batch['history_retention'] = current['history_retention']
        yield


def _live_worker(folder):
    from external_voice import process_alive
    try:
        state = json.loads((folder/'worker_state.json').read_text(encoding='utf-8'))
        return state.get('state') in ('STARTING','PIPELINING','RUNNING','RENDERING') and process_alive(int(state['pid']))
    except (OSError,ValueError,KeyError,TypeError):
        return False


def _release_scripts(store,bid):
    """Release only reserved rows. Success counts and voice bindings survive."""
    path = store.root/'script_library.sqlite3'
    if not path.is_file():
        return
    db = sqlite3.connect(path,timeout=30)
    try:
        with db:
            db.execute('BEGIN IMMEDIATE')
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'script_uses' in tables:
                for sid,count in db.execute("SELECT script_id,count(*) FROM script_uses WHERE batch_id=? AND state='reserved' GROUP BY script_id",(bid,)).fetchall():
                    db.execute('UPDATE scripts SET reserved_count=MAX(0,reserved_count-?) WHERE script_id=?',(count,sid))
                db.execute("DELETE FROM script_uses WHERE batch_id=? AND state='reserved'",(bid,))
            if 'external_uses' in tables:
                for vid,count in db.execute("SELECT version_id,count(*) FROM external_uses WHERE batch_id=? AND state='reserved' GROUP BY version_id",(bid,)).fetchall():
                    db.execute('UPDATE external_versions SET reserved_count=MAX(0,reserved_count-?) WHERE id=?',(count,vid))
                db.execute("DELETE FROM external_uses WHERE batch_id=? AND state='reserved'",(bid,))
    finally:
        db.close()


def _remove_tree(folder,owner,outputs):
    """Checked owned roots only; preserve exports, user protection and junctions."""
    folder,owner = Path(folder),Path(owner).resolve()
    if not folder.exists():
        return 0
    if folder.resolve() == owner or not folder.resolve().is_relative_to(owner) or folder.is_symlink() or folder.is_junction():
        raise ValueError('清理目标超出组合器任务目录。')
    paths = list(folder.rglob('*'))
    if any(p.is_symlink() or p.is_junction() or not p.resolve().is_relative_to(owner) for p in paths):
        raise ValueError('任务目录包含链接，保留现场。')
    if any(p.name in ('.protected','protected.json','golden_voice.keep') for p in paths):
        raise ValueError('任务目录有保护标记，保留现场。')
    protected = {Path(p).resolve() for p in outputs if p}
    exports = [p for p in paths if p.is_file() and (p.resolve() in protected or (p.suffix.lower() in ('.mp4','.mov','.mkv','.avi','.webm') and p.name not in ('_silent_video.part.mp4','_final.part.mp4')))]
    if exports:
        # Legacy batches sometimes stored exported videos next to their plan.
        # Remove owned metadata, keep all actual video files in place.
        amount=0
        for p in paths:
            if p.is_file() and p not in exports:
                amount += p.stat().st_size;p.unlink()
        for p in sorted((p for p in paths if p.is_dir()), key=lambda p:len(p.parts), reverse=True):
            if not any(p.iterdir()):p.rmdir()
        return amount
    amount = sum(p.stat().st_size for p in paths if p.is_file())
    shutil.rmtree(folder)
    return amount


def _cleanup(path,store,at):
    from core import read_json, atomic_json
    batch = read_json(path)
    row = initialize(batch,path,store,at)
    done = [i for i in batch['items'] if i.get('status') in DONE]
    pending = [i for i in batch['items'] if i.get('status') not in DONE]
    expired = row['state']=='expired' or at >= _time(row['expires_at'])
    from sk_materials import active as sk_active
    if sk_active(batch.get('config',{})) and pending and not batch.get('abandoned_at') and row['state']=='open':
        # Rolling successful-history dedup is not a queue-expiry policy.
        # Only explicit cancellation/replacement releases an SK pending plan.
        expired = False
        batch['history_retention']['pending_policy']='explicit_release_sk_v1'
    if sk_active(batch.get('config',{})) and batch.get('abandoned_at'):
        expired=True
    keep = bool(pending or batch.get('voice_failed_tasks') or batch.get('planning_failed_tasks')) and not expired
    before = int(batch['history_retention']['completed_count'])
    completed = before + len(done)
    # Persist existing successes before removing the recoverable Manifest.
    # A crash can retry this phase; INSERT OR IGNORE and max counts are idempotent.
    with store.connect() as db:
        for item in done:
            db.execute('INSERT OR IGNORE INTO history VALUES(?,?,?,?,?)',
                       (item['id'],item['sequence_hash'],item['set_hash'],json.dumps(item,ensure_ascii=False),item.get('finished_at') or batch.get('last_run_at') or batch['created_at']))
            db.execute('DELETE FROM reservations WHERE output_id=?',(item['id'],))
        db.execute('UPDATE batch_retention SET state=?,completed_count=MAX(completed_count,?) WHERE batch_id=?',('open' if keep else 'expired' if expired and pending else 'completed',completed,batch['id']))
        if not keep:db.execute('DELETE FROM reservations WHERE batch_id=?',(batch['id'],))
    if not keep:
        _release_scripts(store,batch['id'])
    batch['items'] = pending
    batch['history_retention']['completed_count'] = completed
    # Checkpoint first: a failed filesystem clean cannot resurrect done entries.
    atomic_json(path,batch)
    outputs=[i.get('output_path') for i in done+pending]
    freed=0;errors=[]
    if keep:
        # Remove obsolete aggregate reports containing completed rows. The
        # remaining Manifest still preserves each unfinished frozen item.
        lines=['# 未完成组合历史','',f'批次：{batch["id"]}',f'已生成累计：{completed}；未完成：{len(pending)}',f'保留到：{row["expires_at"]}','', '| 编号 | 状态 | 文案ID |','|---|---|---|']
        lines += [f'|{i.get("number","")}|{i.get("status","")}|{i.get("script_id","") }|' for i in pending]
        (path.parent/'批次报告.md').write_text('\n'.join(lines),encoding='utf-8')
        for item in done:
            work = Path(item.get('work_dir',''))
            if work.parent.resolve() == path.parent.resolve() and work.name == f'{item["number"]:02d}':
                try:freed += _remove_tree(work,path.parent,outputs)
                except (OSError,ValueError) as exc:errors.append(str(exc))
    else:
        # Job Temp is removable only with its ownership marker, direct-root
        # location and no other retained batch referring to it.
        job=Path(batch.get('config',{}).get('voice_job_temp_dir',''))
        root=store.root/'job_temp'
        others=[]
        for other in (store.root/'batches').glob('*/batch_manifest.json'):
            if other != path:
                try:others.append(read_json(other).get('config',{}).get('voice_job_temp_dir'))
                except Exception:others.append(str(job))  # Unknown ownership protects it.
        if job.parent.resolve()==root.resolve() and (job/'.assembler_job_temp').is_file() and str(job) not in others:
            try:freed += _remove_tree(job,root,outputs)
            except (OSError,ValueError) as exc:errors.append(str(exc))
        try:freed += _remove_tree(path.parent,store.root/'batches',outputs)
        except (OSError,ValueError) as exc:errors.append(str(exc))
    failures=len(batch.get('voice_failed_tasks',[]))+len(batch.get('planning_failed_tasks',[]))
    return dict(batch_id=batch['id'],removed_completed=len(done),expired_unfinished=len(pending)+failures if expired else 0,retained_unfinished=len(pending)+failures if keep else 0,closed=not keep,expires_at=row['expires_at'],freed_bytes=freed,errors=errors)


def sweep(store,*,at=None,only=None,protected=()):
    at=_clock(at);results=[];errors=[];busy=[]
    paths=[Path(only)] if only else sorted((store.root/'batches').glob('*/batch_manifest.json'))
    with file_mutex(store.root/'batch_history_locks'/'sweep.lock',timeout=2):
        for path in paths:
            if not path.exists() or not owned(path,store):continue
            if path.parent.name in protected:busy.append(path.parent.name);continue
            if _live_worker(path.parent):busy.append(path.parent.name);continue
            try:
                with _lock(path,store):results.append(_cleanup(path,store,at))
            except TimeoutError:busy.append(path.parent.name)
            except Exception as exc:errors.append(dict(batch_id=path.parent.name,error=str(exc)))
    report=dict(at=at.isoformat(),revision=REVISION,batches=results,errors=errors,busy=busy)
    if results or errors:
        with (store.root/'batch_history_cleanup.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps(report,ensure_ascii=False)+'\n')
    return report


def run_with_retention(path,store,runner,batch=None):
    """Only canonical, opted-in production paths use retention; dispatches don't."""
    from core import read_json
    enabled=bool((batch or read_json(path,{})).get('history_retention')) and owned(path,store)
    if not enabled:return runner()
    try:
        with run_lease(path,store,batch):return runner()
    finally:
        # Deletion failure must not turn an exported successful video into a
        # reported render failure. Log and retry through startup/timer cleanup.
        try:sweep(store,only=path)
        except Exception as exc:
            with (store.root/'batch_history_cleanup.jsonl').open('a',encoding='utf-8') as log:
                log.write(json.dumps(dict(error=str(exc)),ensure_ascii=False)+'\n')
