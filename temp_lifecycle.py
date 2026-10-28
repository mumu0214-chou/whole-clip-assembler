# -*- coding: utf-8 -*-
"""Cleanup only media created inside explicitly owned assembler temp roots."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import re
import shutil


PREVIEW_NAMES = {'reference.wav', 'preview_raw.wav', 'preview.wav',
                 'preview_raw.part.wav', 'preview.part.wav', 'preview_log.json'}
DIAGNOSTIC_NAMES = {'api_raw.wav', 'api_processed.wav', 'webui_baseline.wav', 'params.json',
                    'indextts_raw.wav', 'indextts_processed.wav', 'baseline.wav', 'diagnostic_summary.json'}
PROTECTION_NAMES = {'protected', '.protected', 'protected.json', 'golden_voice.keep'}


def _protected_tree(folder: Path) -> bool:
    for path in folder.rglob('*'):
        name = path.name.casefold()
        if (name in PROTECTION_NAMES or 'golden' in name or '满意' in name or
                name in ('reference.wav', 'profile.json') or
                (path.is_file() and path.suffix.lower() == '.mp4' and
                 name not in ('_silent_video.part.mp4', '_final.part.mp4'))):
            return True
    return False


def _inside(path: Path, root: Path) -> bool:
    path, root = Path(path), Path(root)
    return path.resolve().is_relative_to(root.resolve()) and not path.is_symlink()


def _log(log_file: Path | None, kind: str, job_id: str, count: int, size: int, errors: list[str]):
    if log_file is None:
        return
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps({'at':datetime.now().astimezone().isoformat(),
                                 'type':kind, 'job_id':job_id, 'files':count,
                                 'bytes':size, 'errors':errors}, ensure_ascii=False) + '\n')


def cleanup_known_folder(folder, root, names, *, kind, log_file=None):
    from file_mutex import file_mutex
    if not Path(root).is_dir(): return (0, 0)
    with file_mutex(Path(root)/'.lifecycle.lock'):
        return _cleanup_known_folder(folder, root, names, kind=kind, log_file=log_file)


def _cleanup_known_folder(folder: Path, root: Path, names: set[str], *,
                         kind: str, log_file: Path | None = None) -> tuple[int, int]:
    """Refuse symlinks, unknown files, nested dirs and protection markers."""
    folder, root = Path(folder), Path(root)
    if not folder.is_dir() or folder.parent != root or not _inside(folder, root):
        return 0, 0
    contents = list(folder.iterdir())
    def known_name(name):
        return name in names or (names == PREVIEW_NAMES and kind.startswith('voice_preview') and
                                re.fullmatch(r'preview_(?:raw|processed)_\d{8}_\d{6}_[0-9a-f]{12}(?:\.part)?\.wav', name))
    if (any(p.is_symlink() or not p.is_file() for p in contents) or
            any(p.name in PROTECTION_NAMES or 'golden' in p.name.casefold() or not known_name(p.name) for p in contents)):
        return 0, 0
    size = sum(p.stat().st_size for p in contents)
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        _log(log_file, kind, folder.name, 0, 0, [str(exc)])
        return 0, 0
    _log(log_file, kind, folder.name, len(contents), size, [])
    return len(contents), size


def prune_transient_runs(root: Path, names: set[str], *, kind: str,
                         keep: Path | None = None, log_file: Path | None = None) -> tuple[int, int]:
    root = Path(root)
    if not root.is_dir():
        return 0, 0
    total = [0, 0]
    for folder in root.iterdir():
        if not folder.is_dir() or folder == keep:
            continue
        if kind == 'voice_preview' and not re.fullmatch(r'[0-9a-f]{32}', folder.name):
            continue
        if kind == 'voice_diagnostic' and not re.fullmatch(r'\d{8}_\d{6}_[0-9a-f]{6}', folder.name):
            continue
        count, size = cleanup_known_folder(folder, root, names, kind=kind, log_file=log_file)
        total[0] += count; total[1] += size
    return tuple(total)


def cleanup_item_media(item: dict, job_root: Path, *, log_file: Path | None = None, protected_paths=()) -> tuple[int, int]:
    """New jobs only. Never follow Manifest paths outside their own job root."""
    root = Path(job_root)
    if (not root.is_dir() or not item.get('voice_temp_version') or
            item.get('protected') or (item.get('voice') or {}).get('protected')):
        return 0, 0
    count = size = 0
    errors = []
    protected_paths={Path(p).resolve() for p in protected_paths if p}
    voice = item.get('voice') or {}
    paths = [voice.get('voice_raw'), voice.get('voice_processed')]
    paths += [str(root/'render'/item['id']/name) for name in
              ('_silent_video.part.mp4', '_bgm.part.wav', '_final.part.mp4')]
    for raw_path in paths:
        if not raw_path:
            continue
        path = Path(raw_path)
        if (path.resolve() in protected_paths or not _inside(path, root) or not path.is_file() or
                any((path.parent/name).exists() for name in PROTECTION_NAMES)):
            continue
        try:
            amount = path.stat().st_size
            path.unlink()
            count += 1; size += amount
        except OSError as exc:
            errors.append(f'{path}: {exc}')
    for raw_path in paths[:2]:
        if not raw_path:
            continue
        path = Path(raw_path).with_suffix('.part.wav')
        if (path.is_file() and _inside(path, root) and
                not any((path.parent/name).exists() for name in PROTECTION_NAMES)):
            try:
                amount = path.stat().st_size; path.unlink(); count += 1; size += amount
            except OSError as exc:
                errors.append(f'{path}: {exc}')
    _log(log_file, 'job_media', item['id'], count, size, errors)
    return count, size


def cleanup_complete_jobs(data_dir: Path, *, dry_run: bool = False) -> tuple[int, int]:
    """Startup recovery: only jobs whose saved Manifest has every item completed."""
    data_dir = Path(data_dir)
    root = data_dir/'job_temp'
    if not root.is_dir():
        return 0, 0
    complete = set()
    referenced = set()
    pending_references=set()
    for manifest in (data_dir/'batches').glob('*/batch_manifest.json'):
        try:
            batch = json.loads(manifest.read_text(encoding='utf-8'))
            temp = batch.get('config', {}).get('voice_job_temp_dir')
            from voice_references import job_roots
            pending_roots=job_roots(batch.get('items',[]),root)
            for raw in pending_roots:
                pending_references.add(Path(raw).name);referenced.add(Path(raw).name)
            if temp and Path(temp).parent == root:
                referenced.add(Path(temp).name)
            if temp and all(i.get('status') == 'completed' for i in batch.get('items', [])):
                candidate = Path(temp)
                if candidate.parent == root:
                    complete.add(candidate.name)
        except (OSError, ValueError, TypeError):
            continue
    count = size = 0
    for folder in root.iterdir():
        marked_orphan = (folder.name not in referenced and
                         (folder/'.assembler_job_temp').is_file())
        if (folder.name in pending_references or (folder.name not in complete and not marked_orphan) or not folder.is_dir() or
                not _inside(folder, root) or _protected_tree(folder)):
            continue
        # This root belongs entirely to one finished assembler job.
        files = [p for p in folder.rglob('*') if p.is_file()]
        if any(not _inside(p, root) for p in files):
            continue
        if dry_run:
            count += len(files); size += sum(p.stat().st_size for p in files)
            continue
        try:
            amount = sum(p.stat().st_size for p in files)
            shutil.rmtree(folder)
            count += len(files); size += amount
            _log(data_dir/'cleanup.jsonl', 'completed_job_recovery', folder.name, len(files), amount, [])
        except OSError as exc:
            _log(data_dir/'cleanup.jsonl', 'completed_job_recovery', folder.name, 0, 0, [str(exc)])
    return count, size


def cleanup_stale_partials(data_dir: Path) -> tuple[int, int]:
    """At startup the instance lock ensures no assembler job is writing here."""
    data_dir = Path(data_dir)
    root = data_dir/'job_temp'
    if not root.is_dir():
        return 0, 0
    count = size = 0
    for job in root.iterdir():
        if (not job.is_dir() or not (job/'.assembler_job_temp').is_file() or
                not _inside(job, root) or _protected_tree(job)):
            continue
        for path in job.rglob('*'):
            if (path.is_file() and _inside(path, root) and
                    (path.name.endswith('.part.wav') or path.name.endswith('.part.mp4'))):
                try:
                    amount = path.stat().st_size; path.unlink()
                    count += 1; size += amount
                except OSError as exc:
                    _log(data_dir/'cleanup.jsonl', 'startup_partial', job.name, 0, 0, [str(exc)])
    if count:
        _log(data_dir/'cleanup.jsonl', 'startup_partial', 'all_jobs', count, size, [])
    return count, size


def cleanup_orphan_job(folder: Path, data_dir: Path) -> tuple[int, int]:
    root = Path(data_dir)/'job_temp'
    folder = Path(folder)
    if (folder.parent != root or not folder.is_dir() or not _inside(folder, root) or
            not (folder/'.assembler_job_temp').is_file() or _protected_tree(folder)):
        return 0, 0
    files = [p for p in folder.rglob('*') if p.is_file()]
    if any(not _inside(p, root) for p in files):
        return 0, 0
    amount = sum(p.stat().st_size for p in files)
    try:
        shutil.rmtree(folder)
    except OSError as exc:
        _log(Path(data_dir)/'cleanup.jsonl', 'orphan_job', folder.name, 0, 0, [str(exc)])
        return 0, 0
    _log(Path(data_dir)/'cleanup.jsonl', 'orphan_job', folder.name, len(files), amount, [])
    return len(files), amount


def cleanup_test_runs(data_dir: Path, *, dry_run: bool = False,
                      keep: Path | None = None) -> tuple[int, int]:
    root = Path(data_dir)/'test_runs'
    if not root.is_dir():
        return 0, 0
    count = size = 0
    for folder in root.iterdir():
        if (folder == keep or not folder.is_dir() or not _inside(folder, root) or
                not (folder/'.assembler_test_temp').is_file() or
                _protected_tree(folder)):
            continue
        files = [p for p in folder.rglob('*') if p.is_file()]
        if any(not _inside(p, root) for p in files):
            continue
        amount = sum(p.stat().st_size for p in files)
        if not dry_run:
            try:
                shutil.rmtree(folder)
            except OSError as exc:
                _log(Path(data_dir)/'cleanup.jsonl', 'test_run', folder.name, 0, 0, [str(exc)])
                continue
            _log(Path(data_dir)/'cleanup.jsonl', 'test_run', folder.name, len(files), amount, [])
        count += len(files); size += amount
    return count, size


def legacy_cache_candidates(data_dir: Path, cache_root: Path) -> list[Path]:
    """Only exact hash-named files created by the old assembler, excluding live plans."""
    root = Path(cache_root)
    if not root.is_dir() or {p.name for p in root.iterdir()} - {'raw', 'processed'}:
        return []
    protected = set()
    for manifest in (Path(data_dir)/'batches').glob('*/batch_manifest.json'):
        try:
            batch = json.loads(manifest.read_text(encoding='utf-8'))
            for item in batch.get('items', []):
                if item.get('status') != 'completed':
                    voice = item.get('voice') or {}
                    protected.update(str(Path(voice[key]).resolve()) for key in
                                     ('voice_raw', 'voice_processed') if voice.get(key))
        except (OSError, ValueError, TypeError):
            continue
    candidates = []
    for kind in ('raw', 'processed'):
        folder = root/kind
        if not folder.is_dir() or folder.is_symlink():
            return []
        for path in folder.iterdir():
            if (not path.is_file() or path.is_symlink() or
                    not re.fullmatch(r'[0-9a-f]{64}\.wav', path.name)):
                return []
            if str(path.resolve()) not in protected:
                candidates.append(path)
    return candidates


def cleanup_legacy_voice_cache(data_dir: Path, cache_root: Path, *,
                               dry_run: bool = False) -> tuple[int, int]:
    candidates = legacy_cache_candidates(data_dir, cache_root)
    if dry_run:
        return len(candidates), sum(p.stat().st_size for p in candidates)
    count = size = 0
    for path in candidates:
        try:
            amount = path.stat().st_size; path.unlink()
            count += 1; size += amount
            _log(Path(data_dir)/'cleanup.jsonl', 'legacy_voice_cache', path.name, 1, amount, [])
        except OSError as exc:
            _log(Path(data_dir)/'cleanup.jsonl', 'legacy_voice_cache', path.name, 0, 0, [str(exc)])
    return count, size


def manual_cleanup(data_dir: Path, *, dry_run: bool = False) -> tuple[int, int]:
    from voice_pipeline import CACHE_ROOT
    from voice_profile_manager import PREVIEW_ROOT
    from voice_diagnostics import DIAGNOSTIC_ROOT
    data_dir = Path(data_dir)
    log_file = data_dir/'cleanup.jsonl'
    preview = Path(PREVIEW_ROOT)
    diagnostic = Path(DIAGNOSTIC_ROOT)
    eligible = []
    for root, names, pattern in ((preview, PREVIEW_NAMES, r'[0-9a-f]{32}'),
                                  (diagnostic, DIAGNOSTIC_NAMES, r'\d{8}_\d{6}_[0-9a-f]{6}')):
        if not root.is_dir():
            continue
        for folder in root.iterdir():
            if (folder.is_dir() and re.fullmatch(pattern, folder.name) and
                    _inside(folder, root)):
                contents = list(folder.iterdir())
                if all(p.is_file() and not p.is_symlink() and p.name in names and
                       p.name not in PROTECTION_NAMES for p in contents):
                    eligible.append((folder, root, names))
    old = legacy_cache_candidates(data_dir, CACHE_ROOT)
    music_preview = data_dir/'job_temp'/'music_preview'/'audio_preview.wav'
    if music_preview.is_file() and _inside(music_preview, data_dir/'job_temp'):
        old.append(music_preview)
    if dry_run:
        files = old + [p for folder, _, _ in eligible for p in folder.iterdir()]
        completed_count, completed_size = cleanup_complete_jobs(data_dir, dry_run=True)
        test_count, test_size = cleanup_test_runs(data_dir, dry_run=True)
        return len(files) + completed_count + test_count, sum(p.stat().st_size for p in files) + completed_size + test_size
    total = [0, 0]
    for folder, root, names in eligible:
        count, size = cleanup_known_folder(folder, root, names,
                                           kind='manual_transient', log_file=log_file)
        total[0] += count; total[1] += size
    for path in old:
        try:
            amount = path.stat().st_size; path.unlink()
            total[0] += 1; total[1] += amount
            _log(log_file, 'legacy_voice_cache_or_preview', path.name, 1, amount, [])
        except OSError as exc:
            _log(log_file, 'legacy_voice_cache_or_preview', path.name, 0, 0, [str(exc)])
    count, size = cleanup_complete_jobs(data_dir)
    total[0] += count; total[1] += size
    count, size = cleanup_test_runs(data_dir)
    total[0] += count; total[1] += size
    return tuple(total)
