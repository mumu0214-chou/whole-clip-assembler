"""Per-speaker pitch-preserving tempo, before shot fitting and caption alignment."""
from mac_paths import path as _portable_path
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import math
import os
import subprocess
import uuid
import wave

REVISION = 'voice_rate_v1'
DEFAULTS = dict(voice_speed_revision=REVISION, voice_speed_enabled=False,
                voice_speed_factor=1.15, voice_speed_selected=[])


def settings(cfg):
    factor = float(cfg.get('voice_speed_factor', 1.15))
    pitch = float(cfg.get('voice_pitch_semitones', 0))
    if not math.isfinite(pitch) or not -6 <= pitch <= 6:
        raise ValueError('配音音调须在-6～+6半音之间；0保持原音调')
    selected = cfg.get('voice_speed_selected', [])
    if (cfg.get('voice_speed_revision', REVISION) != REVISION or
            not math.isfinite(factor) or not 1 <= factor <= 2 or
            not isinstance(selected, list) or any(not isinstance(k, str) or
            not k.startswith(('external:', 'local:')) for k in selected)):
        raise ValueError('配音加速设置无效：倍速须为1～2倍，并选择需要加速的音色')
    if type(cfg.get('voice_speed_enabled', False)) is not bool:
        raise ValueError('配音加速开关无效')
    return dict(enabled=cfg.get('voice_speed_enabled', False), factor=factor,
                selected=sorted(set(selected)))


def decision(cfg, key):
    opts = settings(cfg)
    factor = opts['factor'] if opts['enabled'] and key in opts['selected'] else 1.
    result = dict(revision=REVISION, speaker_key=key, factor=factor)
    if 'voice_pitch_semitones' in cfg:
        result['pitch_semitones'] = float(cfg['voice_pitch_semitones'])
    return result


def speaker_key(package):
    if package.get('voice_source')=='external_files':
        name=package.get('external_voice_name')
        return 'external:'+name if name else 'external:version:'+str(package.get('external_version_id',''))
    return 'local:'+package.get('voice_profile','')


def digest(path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''): h.update(data)
    return h.hexdigest()


def duration(path):
    with wave.open(str(path)) as audio:
        return audio.getnframes() / audio.getframerate()


def apply(voice, snapshot, ffmpeg, folder=None):
    """Keep raw and original processed media; derive a keyed PCM copy atomically."""
    from file_mutex import file_mutex
    from subtitle_styles import atomic
    from voice_pipeline import VoiceError
    pitch = float(snapshot.get('pitch_semitones', 0))
    if snapshot['revision'] != REVISION or not 1 <= snapshot['factor'] <= 2 or not math.isfinite(pitch) or not -6 <= pitch <= 6:
        raise VoiceError('配音语速快照无效')
    result = deepcopy(voice)
    if result.get('speed_snapshot'):
        if result['speed_snapshot'] != snapshot: raise VoiceError('不能对已冻结配音重复改变语速')
        verify_voice(result, snapshot)
        return result
    source = Path(voice['voice_processed'])
    result['speed_snapshot'] = deepcopy(snapshot)
    if snapshot['factor'] == 1 and pitch == 0: return result
    source_sha = digest(source)
    original = dict(path=str(source), sha256=source_sha, duration=duration(source),
                    voice_cache_key=voice['voice_cache_key'])
    key = sha256(json.dumps(dict(source=source_sha, snapshot=snapshot), sort_keys=True).encode()).hexdigest()
    folder = Path(folder) if folder else Path(_portable_path(r'D:\GPTData\cache\assembler_voice_speed'))
    dest = folder / key / 'audio.wav'; dest.parent.mkdir(parents=True, exist_ok=True)
    info_path = dest.with_suffix('.json')
    with file_mutex(dest.parent/'tempo.lock', timeout=180):
        if dest.exists():
            info = json.loads(info_path.read_text(encoding='utf-8'))
            if info['source_sha256'] != source_sha or info['sha256'] != digest(dest):
                raise VoiceError('配音加速副本校验失败，保留现场等待检查')
        else:
            part = dest.with_name(uuid.uuid4().hex+'.part.wav')
            try:
                filters = ([f'rubberband=pitch={2 ** (pitch / 12):.10f}'] if pitch else [])
                filters.append(f"atempo={snapshot['factor']:.8f}")
                command = [str(ffmpeg), '-v', 'error', '-nostdin', '-xerror', '-y', '-i', str(source),
                           '-map', '0:a:0', '-af', ','.join(filters), '-c:a', 'pcm_s16le', str(part)]
                proc = subprocess.run(command, capture_output=True, text=True, timeout=180,
                                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                if proc.returncode: raise VoiceError('配音加速失败：'+proc.stderr[-800:])
                measured = duration(part)
                if measured < .2 or abs(measured-original['duration']/snapshot['factor']) > .15:
                    raise VoiceError('配音加速后的实际时长异常')
                actual = digest(part)
                atomic(info_path, dict(source_sha256=source_sha, sha256=actual, duration=measured, snapshot=snapshot))
                os.replace(part, dest)
            finally: part.unlink(missing_ok=True)
        measured = duration(dest)
    # External recordings retain their complete span; local postprocessing has
    # an existing silence window, which is remeasured on the derived waveform.
    start, end = 0., measured
    if voice.get('source') != 'external_files':
        from voice_pipeline import effective_voice_range
        start, end = effective_voice_range(dest, str(ffmpeg))
    result.update(voice_processed=str(dest), voice_cache_key=key, sha256=digest(dest),
                  voice_duration=measured, effective_voice_start=start,
                  effective_voice_end=end, effective_voice_duration=end-start,
                  speed_source=original)
    result.pop('external_alignment', None); result.pop('alignment_identity', None)
    return result


def verify_voice(voice, snapshot):
    if voice.get('speed_snapshot') != snapshot:
        raise ValueError('配音语速与冻结快照不一致')
    if snapshot['factor'] != 1 or snapshot.get('pitch_semitones', 0):
        source = voice.get('speed_source') or {}
        if (not Path(source.get('path', '')).is_file() or digest(source['path']) != source.get('sha256') or
                not Path(voice.get('voice_processed', '')).is_file() or digest(voice['voice_processed']) != voice.get('sha256')):
            raise ValueError('配音加速源文件或冻结副本已改变')


def restore_missing(voice,ffmpeg):
    """Recover only a missing derived PCM, proving the identical frozen hash."""
    source=voice.get('speed_source');snapshot=voice.get('speed_snapshot')
    if not source or not snapshot or Path(voice.get('voice_processed','')).is_file():return False
    if not Path(source['path']).is_file() or digest(source['path'])!=source['sha256']:
        raise ValueError('原配音内容已改变，不能恢复派生配音')
    original=deepcopy(voice)
    original.pop('speed_source',None);original.pop('speed_snapshot',None)
    original.update(voice_processed=source['path'],voice_cache_key=source['voice_cache_key'],sha256=source['sha256'],
                    voice_duration=source['duration'],effective_voice_start=0.,effective_voice_end=source['duration'],effective_voice_duration=source['duration'])
    folder=Path(voice['voice_processed']).parent.parent
    restored=apply(original,snapshot,ffmpeg,folder)
    if restored['sha256']!=voice['sha256'] or restored['voice_cache_key']!=voice['voice_cache_key'] or abs(restored['voice_duration']-voice['voice_duration'])>1e-6:
        raise ValueError('恢复副本与冻结配音不一致，未修改原任务')
    return True


def validate(item, cfg):
    if not item.get('narration_enabled') or 'voice_speed_revision' not in cfg: return
    p = item.get('script_package') or {}
    key = speaker_key(dict(p,voice_source=item.get('voice_source')))
    expected = decision(cfg, key)
    if (expected['factor']==1 and not expected.get('pitch_semitones',0) and not item.get('voice_speed_snapshot') and
            not p.get('voice_speed_snapshot') and not (item.get('voice') or {}).get('speed_snapshot')):
        return  # Legacy/direct callers which never requested tempo processing.
    if item.get('voice_speed_snapshot') != expected or p.get('voice_speed_snapshot') != expected:
        raise ValueError('音色语速选择与已冻结批次不一致')
    verify_voice(item.get('voice') or {}, expected)
