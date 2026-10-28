"""Explicit profile history and protected audition retention; no formal Plan edits."""
from pathlib import Path
from voice_pipeline import VoiceError
import json
import shutil
import sqlite3
import time
import uuid
from hashlib import sha256
from contextlib import closing


def backup_profile(path):
    path=Path(path)
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    folder=path.parent/'backup';folder.mkdir(exist_ok=True)
    version=str(data.get('profile_version','1'))
    version=version.zfill(3) if version.isdigit() else 'legacy'
    output=folder/f'profile_v{version}_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}.json'
    references={}
    for key,field in (('reference_source','reference_audio'),('emotion_reference_source','emotion_reference_audio')):
        name=data.get(field)
        source=path.parent/str(name) if name else None
        if source and source.is_file():
            identity=sha256(source.read_bytes()).hexdigest()
            target=folder/f'{field}_{identity}.wav'
            if not target.exists():shutil.copy2(source,target)
            references[key]=target.name
    output.write_text(json.dumps(dict(data,_history_references=references),ensure_ascii=False,indent=2),encoding='utf-8')
    return output


def voice_id_locked(path,history_dir=None):
    path=Path(path);data=json.loads(path.read_text(encoding='utf-8-sig'))
    # A formal usage record proves the voice was ready before. Saving a draft or
    # disabling it must not silently unlock historical IDs.
    root=Path(history_dir) if history_dir else Path(__file__).parent/'data'
    db=root/'assembly_runtime.sqlite3'
    if not db.is_file():return False
    with closing(sqlite3.connect(f'{db.as_uri()}?mode=ro',uri=True)) as con:
        for row in con.execute('SELECT payload FROM history'):
            item=json.loads(row[0]);p=item.get('script_package') or {}
            if item.get('voice_profile')==data['voice_id'] or p.get('voice_profile')==data['voice_id']:
                return True
    return False


def profile_history(path):
    path=Path(path)
    return sorted([*(path.parent/'backup').glob('profile_*.json'),
                   *path.parent.glob('profile_before_*.json')],reverse=True)


def restore_draft(path,backup):
    path,backup=Path(path).resolve(),Path(backup).resolve()
    if backup not in [p.resolve() for p in profile_history(path)]:
        raise ValueError('历史版本不属于当前音色。')
    from voice_profile_manager import _normalize
    saved=json.loads(backup.read_text(encoding='utf-8-sig'))
    data,errors=_normalize(saved)
    if errors:raise ValueError('；'.join(errors))
    data.update(enabled=False,validation_status='draft',validation_message='从历史版本恢复；尚未保存。')
    for key,name in saved.get('_history_references',{}).items():
        candidate=(backup.parent/name).resolve()
        if key not in ('reference_source','emotion_reference_source') or candidate.parent!=backup.parent:
            raise ValueError('历史参考路径越界。')
        if not candidate.is_file():raise ValueError('历史参考音频已缺失，不能假装恢复。')
        data[key]=str(candidate)
    return data


def clone_values(raw,display_name,voice_id):
    result=dict(raw,display_name=display_name,voice_id=voice_id,profile_version='1',
                enabled=False,validation_status='draft',golden_voice=None)
    return result


def save_golden(profile_path,processed,ffmpeg):
    from voice_pipeline import cache_state,VoiceError
    from voice_profile_manager import _write_json
    path,processed=Path(profile_path),Path(processed)
    trace=json.loads((processed.parent/'preview_log.json').read_text(encoding='utf-8'))
    if str(processed.resolve())!=trace['processed_path'] or cache_state(processed,ffmpeg)!='CACHE_READY':
        raise VoiceError('请选择当前已验证的Processed试听。')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    if trace['voice_id']!=data['voice_id']:raise VoiceError('当前试听属于其他音色。')
    backup_profile(path)
    target=path.parent/'golden_voice.wav'
    if target.exists():
        shutil.copy2(target,path.parent/'backup'/f'golden_voice_{time.time_ns()}.wav')
    shutil.copy2(processed,target)
    (path.parent/'golden_voice.json').write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
    (path.parent/'golden_voice.keep').write_text('用户保存的Golden Voice；禁止自动清理。',encoding='utf-8')
    data['golden_voice']='golden_voice.wav';_write_json(path,data)
    return target


def protect_preview(path,reason='A/B比较中'):
    from file_mutex import file_mutex
    path=Path(path)
    with file_mutex(path.parent.parent/'.lifecycle.lock'):
        if not path.is_file(): raise VoiceError('试听文件已失效，请重新生成：'+str(path))
        path.parent.joinpath('.protected').write_text(reason,encoding='utf-8')


def release_preview(path):
    from file_mutex import file_mutex
    path=Path(path)
    with file_mutex(path.parent.parent/'.lifecycle.lock'):
        path.parent.joinpath('.protected').unlink(missing_ok=True)
