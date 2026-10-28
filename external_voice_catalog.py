"""Filename voice filters and journaled ID-folder organization over the existing DB."""
from pathlib import Path
import json
import os
import re

AUDIO_SUFFIXES = frozenset(('.mp3', '.wav', '.m4a', '.flac', '.aac', '.mp4'))


def filename_identity(path):
    match = re.fullmatch(r'(L\d+)(\D.*)', Path(path).stem, re.I)
    return (match[1].upper(), match[2].strip()) if match and match[2].strip() else None


def voice_name(row):
    # The bound script is authoritative; a similar ID must never match.
    for path in (row.get('source_path'), row.get('metadata', {}).get('path'), row.get('path')):
        identity = filename_identity(path) if path else None
        if identity:
            return identity[1] if identity[0] == row.get('display_id', '').upper() else None
        if path and Path(path).parent.name.upper() == row.get('display_id','').upper():
            return Path(path).stem
    return None


def pairing_candidates(external,directory,profile_id,spec_id=None):
    """Folder names match saved document display IDs in the current category."""
    from external_voice import error
    root=Path(directory).resolve()
    if not root.is_dir():raise error('配音库目录不存在。')
    scripts=external.library.list(profile_id=profile_id,spec_id=spec_id)
    mapping={}
    for script in scripts:mapping.setdefault(script['display_id'].upper(),[]).append(script)
    folders=[root] if re.fullmatch(r'L\d+',root.name,re.I) else sorted(p for p in root.iterdir() if p.is_dir() and re.fullmatch(r'L\d+',p.name,re.I))
    result=[];issues=[];matched=set()
    for folder in folders:
        display=folder.name.upper();choices=mapping.get(display,[])
        if not choices:continue
        if len(choices)!=1:
            issues.append(dict(folder=str(folder),reason='当前文案库同ID不唯一，未自动绑定'));continue
        if folder.is_symlink() or folder.is_junction() or not folder.resolve().is_relative_to(root):
            issues.append(dict(folder=str(folder),reason='ID目录是链接或位于配音库之外'));continue
        script=choices[0];matched.add(script['script_id'])
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:continue
            identity=filename_identity(path)
            if path.is_symlink() or not path.resolve().is_relative_to(root) or (identity and identity[0]!=display):
                issues.append(dict(path=str(path),reason='文件ID与文件夹不一致或文件位于库外'));continue
            result.append(dict(path=str(path),script_id=script['script_id'],script_uid=script['script_uid'],display_id=display,profile_id=profile_id,spec_id=script['spec_id']))
    return dict(root=str(root),script_count=len(scripts),matched_scripts=len(matched),missing_ids=[s['display_id'] for s in scripts if s['script_id'] not in matched],candidates=result,issues=issues)


def pair_by_id(external,directory,profile_id,ffmpeg,ffprobe,*,progress=lambda _:None,cancel=None,spec_id=None):
    from external_voice import digest
    inventory=pairing_candidates(external,directory,profile_id,spec_id)
    existing={(r['uid'],r['sha']) for r in external.versions(include_deleted=True) if r['file_state']!='purged'}
    rows=[]
    for number,candidate in enumerate(inventory['candidates'],1):
        if cancel and cancel.is_set():break
        result=dict(candidate)
        try:
            sha=digest(candidate['path'])
            if (candidate['script_uid'],sha) in existing:
                result.update(status='duplicate')
            else:
                checked=external.probe(candidate['path'],ffmpeg,ffprobe)
                if checked['status']!='valid':raise ValueError('多音轨请通过原导入预览手动选择，未自动绑定。')
                checked['script_id']=candidate['script_id']
                value=external.bind(checked,managed=True,confirmed=False,expected_script=candidate)
                result.update(value)
                existing.add((candidate['script_uid'],checked['sha']))
        except Exception as exc:result.update(status='failed',error=str(exc))
        rows.append(result);progress(f'按ID配对 {number}/{len(inventory["candidates"])}：{Path(candidate["path"]).name} · '+result['status'])
    return dict(root=inventory['root'],script_count=inventory['script_count'],matched_scripts=inventory['matched_scripts'],missing_ids=inventory['missing_ids'],candidate_count=len(inventory['candidates']),bound=sum(r['status']=='bound' for r in rows),duplicate=sum(r['status']=='duplicate' for r in rows),failed=sum(r['status']=='failed' for r in rows),cancelled=bool(cancel and cancel.is_set()),issues=inventory['issues'],rows=rows)


def selected_names(cfg):
    from external_voice import error
    value = cfg.get('external_voice_selected_names')
    if value is None:
        return None  # Legacy configs keep all valid bindings, including unnamed ones.
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise error('外部音色筛选必须是音色名称列表。')
    return set(value)


def filter_rows(rows, cfg):
    names = selected_names(cfg)
    return rows if names is None else [r for r in rows if voice_name(r) in names]


def library_audio_files(directory):
    """One library level plus exact ID folders; never crawl TTS scratch folders."""
    root = Path(directory).resolve()
    files = []
    for source in sorted(root.iterdir()):
        if source.is_file() and source.suffix.lower() in AUDIO_SUFFIXES and filename_identity(source) and source.resolve().is_relative_to(root):
            files.append(source)
        elif source.is_dir() and re.fullmatch(r'L\d+', source.name, re.I) and source.resolve().is_relative_to(root):
            files.extend(p for p in sorted(source.iterdir()) if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES and filename_identity(p) and filename_identity(p)[0] == source.name.upper() and p.resolve().is_relative_to(root))
    return files


def _key(path):
    return os.path.normcase(str(Path(path).resolve())) if path else None


def _destination(root, source):
    from external_voice import error
    identity = filename_identity(source)
    target = root / identity[0] / source.name
    if source.resolve().parent != root or not target.resolve().is_relative_to(root):
        raise error('归类路径超出指定配音库。')
    return target


def organize_library(external, directory, *, resume_op=None, progress=lambda _: None):
    """Move only flat ID+voice files; retain resources, reviews and frozen references.

    A pending journal can recover a crash between rename and SQLite commit.
    Existing destinations are never overwritten or removed.
    """
    from external_voice import digest, error
    root = Path(directory).resolve()
    if not root.is_dir():
        raise error('配音库目录不存在。')
    with external.locked():
        if resume_op:
            with external.library._connect() as db:
                record = db.execute("SELECT * FROM external_operations WHERE id=? AND kind='organize'", (resume_op,)).fetchone()
            if not record:
                raise error('归类操作不存在。')
            payload = json.loads(record['payload'])
            if _key(payload['root']) != _key(root):
                raise error('归类恢复目录不一致。')
            op = resume_op
        else:
            files = []
            for source in sorted(root.iterdir()):
                if source.is_file() and source.suffix.lower() in AUDIO_SUFFIXES and filename_identity(source):
                    target = _destination(root, source)
                    files.append(dict(source=str(source), target=str(target), sha=digest(source), state='pending'))
            payload = dict(root=str(root), files=files)
            op = external.operation('organize', payload)
        resource_index = {}
        with external.library._connect() as db:
            for row in db.execute('SELECT * FROM external_resources'):
                for path in (row['path'], row['source_path'], json.loads(row['metadata']).get('path')):
                    if path:
                        resource_index.setdefault(_key(path), set()).add(row['id'])
        for entry in payload['files']:
            if entry['state'] == 'complete':
                continue
            source, target = Path(entry['source']), Path(entry['target'])
            renamed = False
            try:
                if _destination(root, source) != target or target.parent.is_symlink():
                    raise error('归类日志目标不合法。')
                if source.exists() and target.exists():
                    raise error('目标已存在，跳过且不覆盖：' + str(target))
                current = source if source.exists() else target
                if not current.is_file() or digest(current) != entry['sha']:
                    raise error('归类文件缺失或内容已改变。')
                with external.library._connect() as db:
                    db.execute('BEGIN IMMEDIATE')
                    changes = []
                    for resource_id in resource_index.get(_key(source), ()):
                        row = db.execute('SELECT * FROM external_resources WHERE id=?', (resource_id,)).fetchone()
                        if row is None:
                            raise error('归类期间资源登记发生变化。')
                        metadata = json.loads(row['metadata'])
                        fields = (row['path'], row['source_path'], metadata.get('path'))
                        if not any(_key(x) == _key(source) for x in fields if x):
                            continue
                        if row['sha'] != entry['sha']:
                            raise error('原件内容与登记资源不一致，保留现场：' + str(source))
                        path = str(target) if _key(row['path']) == _key(source) else row['path']
                        original = str(target) if _key(row['source_path']) == _key(source) else row['source_path']
                        if _key(metadata.get('path')) == _key(source):
                            metadata['path'] = str(target)
                        changes.append((path, original, json.dumps(metadata, ensure_ascii=False), row['id']))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if source.exists():
                        source.rename(target)  # Windows rename refuses an existing target.
                        renamed = True
                    if digest(target) != entry['sha']:
                        raise error('归类后内容校验失败。')
                    db.executemany('UPDATE external_resources SET path=?,source_path=?,metadata=? WHERE id=?', changes)
                entry.update(state='complete', resource_ids=[x[3] for x in changes])
                entry.pop('error', None)
            except Exception as exc:
                if renamed and not source.exists() and target.is_file() and digest(target) == entry['sha']:
                    target.rename(source)
                entry.update(state='failed', error=str(exc))
            external.update_op(op, payload, 'pending')
            progress(dict(entry))
        failed = sum(x['state'] != 'complete' for x in payload['files'])
        failures='; '.join(x.get('error','') for x in payload['files'] if x['state']!='complete')
        external.update_op(op, payload, 'partial' if failed else 'complete', failures)
        return dict(operation_id=op, moved=len(payload['files'])-failed, failed=failed, files=payload['files'])
