"""Chronological batch directories and a read-only unfinished-history catalog."""
from datetime import datetime, timezone
from pathlib import Path
import json
import re

REVISION = 'chronological_batch_folders_v1'


def directory_name(batch):
    clock = datetime.fromisoformat(batch['created_at'])
    if clock.tzinfo is None: clock = clock.replace(tzinfo=timezone.utc)
    clock = clock.astimezone()
    suffix = re.sub(r'[^a-zA-Z0-9]', '', batch['id'])[-6:] or 'batch'
    return clock.strftime('%Y-%m-%d_%H-%M-%S') + '_' + suffix


def catalog(store):
    from core import read_json
    from batch_retention import owned, _row, _time, _clock
    rows = []
    for path in (store.root/'batches').glob('*/batch_manifest.json'):
        if not owned(path, store): continue
        try:
            batch = read_json(path); bid = batch['id']
            pending = [i for i in batch['items'] if i.get('status') not in ('completed', 'DONE')]
            failed_rows=list(batch.get('planning_failed_tasks', []))+list(batch.get('voice_failed_tasks', []))
            failures = len({(r.get('id'),r.get('number')) for r in failed_rows})
            row = _row(store, bid)
            meta = batch.get('history_retention', {})
            expires = row['expires_at'] if row else meta.get('expires_at')
            if row and row['state'] != 'open': continue
            if expires and _clock() >= _time(expires): continue
            if not pending and not failures: continue
            created = _time(batch['created_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            last = row['last_started_at'] if row else meta.get('last_started_at') or batch.get('last_run_at')
            rows.append(dict(id=bid,path=str(path),created_at=created,
                profile=batch['config'].get('editing_profile_name','未标记品类')+(' / '+batch['config']['product_spec_name'] if batch['config'].get('product_spec_name') else ''),
                unfinished=len(pending),failed=sum(i.get('status') not in ('planned',) for i in pending)+failures,
                last_started_at=_time(last).astimezone().strftime('%Y-%m-%d %H:%M:%S') if last else '尚未开始',
                expires_at=_time(expires).astimezone().strftime('%Y-%m-%d %H:%M:%S') if expires else '待检查',
                output_root=batch.get('output_date_folder') or batch['config'].get('output_root','')))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(rows, key=lambda r:r['created_at'], reverse=True)


def _relocate(value, old, new):
    if isinstance(value, dict): return {k:_relocate(v,old,new) for k,v in value.items()}
    if isinstance(value, list): return [_relocate(v,old,new) for v in value]
    if isinstance(value, str):
        for a,b in ((str(old),str(new)),(old.as_posix(),new.as_posix())):
            if value == a or value.startswith(a+'\\') or value.startswith(a+'/'):
                return b + value[len(a):]
    return value


def migrate(store):
    """Called at a new application's startup, inside its global process lease.

    Stable IDs and frozen media decisions stay intact. Only owned filesystem
    locations change. A journal lets the next startup finish an interrupted move.
    """
    from core import read_json, atomic_json
    from batch_retention import owned, _lock, _live_worker
    root = (store.root/'batches').resolve(); journal_path=store.root/'batch_folder_migrations.json'
    journal=read_json(journal_path,{}); report=dict(moved=[],skipped=[],errors=[])
    with store.connect() as db:
        retired={r[0] for r in db.execute("SELECT batch_id FROM batch_retention WHERE state!='open'")}
    paths=list(root.glob('*/batch_manifest.json'))
    for entry in journal.values():
        p=root/entry['new']/'batch_manifest.json'
        if p.exists() and p not in paths:paths.append(p)
    for path in paths:
        if not owned(path,store):continue
        originals=[];old=None;new=None
        try:
            batch=read_json(path);bid=batch['id']
            if bid in retired:continue
            name=directory_name(batch)
            entry=journal.get(bid)
            if not entry and path.parent.name==name:continue
            if entry and entry.get('state')=='done' and path.parent.name==name:continue
            if _live_worker(path.parent):report['skipped'].append(bid);continue
            old=root/(entry['old'] if entry else path.parent.name);new=root/name
            if old==new:continue
            if old.parent!=root or new.parent!=root or old.is_symlink() or old.is_junction() or new.is_symlink() or new.is_junction():
                raise ValueError('历史目录路径或链接不合法，未移动。')
            if not entry and new.exists():raise ValueError('同名时间目录已存在，未覆盖。')
            if old.exists() and not old.resolve().is_relative_to(root):raise ValueError('历史目录超出任务根目录。')
            if new.exists() and not new.resolve().is_relative_to(root):raise ValueError('目标目录超出任务根目录。')
            with _lock(path,store):
                source=old if old.exists() else new
                files=list(source.rglob('*'))
                if any(p.is_symlink() or p.is_junction() for p in files):raise ValueError('历史目录内有链接，保留现场。')
                # Old user exports inside batch folders must keep their paths.
                if any(p.suffix.lower() in ('.mp4','.mov','.mkv','.webm') and p.name not in ('_silent_video.part.mp4','_final.part.mp4') for p in files):
                    report['skipped'].append(bid);continue
                journal[bid]=dict(old=old.name,new=new.name,state='pending')
                atomic_json(journal_path,journal)
                updates=[]
                for file in source.rglob('*.json'):
                    value=read_json(file);updated=_relocate(value,old,new)
                    if file.name=='batch_manifest.json' and file.parent==source:
                        updated.update(history_folder_name=name,history_folder_revision=REVISION,batch_path=str(new/file.name))
                    if updated!=value:
                        originals.append((file,value));updates.append((file,updated))
                for file,updated in updates:atomic_json(file,updated)
                # Check absolute containment immediately before the one-shell
                # equivalent Path.rename; never overwrite another directory.
                if source==old:
                    if new.exists():raise ValueError('目标目录已存在，未覆盖。')
                    if old.resolve().parent!=root or new.absolute().parent!=root:raise ValueError('历史移动范围不合法。')
                    old.rename(new)
                with store.connect() as db:
                    for table,column,key in (('reservations','payload','output_id'),('history','payload','output_id')):
                        for identity,payload in db.execute(f'SELECT {key},{column} FROM {table}').fetchall():
                            value=json.loads(payload);updated=_relocate(value,old,new)
                            if updated!=value:db.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?',(json.dumps(updated,ensure_ascii=False),identity))
                journal[bid]['state']='done';atomic_json(journal_path,journal)
                report['moved'].append(dict(id=bid,old=str(old),new=str(new)))
        except Exception as exc:
            # An ordinary permission/rename error must leave the old batch
            # immediately usable. A process crash is recovered by the journal.
            if old is not None and old.exists() and new is not None and not new.exists():
                for file,value in originals:
                    try:atomic_json(file,value)
                    except OSError:pass
            report['errors'].append(dict(path=str(path),error=str(exc)))
    return report
