"""Shared PCM ownership across a batch and its pending parameter revisions."""
from pathlib import Path


def paths(items,exclude=None):
    result=set()
    for item in items:
        if item.get('id')==exclude or item.get('status') in ('completed','DONE'):continue
        voice=item.get('voice') or {}
        result.update(p for p in (voice.get('voice_raw'),voice.get('voice_processed'),
                                  voice.get('speed_source',{}).get('path')) if p)
    return sorted(result)


def job_roots(items,root):
    root=Path(root).resolve();result=set()
    for raw in paths(items):
        path=Path(raw).resolve()
        if path.is_relative_to(root):
            parts=path.relative_to(root).parts
            if len(parts)>1:result.add(str(root/parts[0]))
    return sorted(result)
