# -*- coding: utf-8 -*-
"""Create and validate local Voice Profiles without touching formal voice caches."""
from __future__ import annotations
from mac_paths import path as _portable_path
import runtime_diagnostics as diag

from pathlib import Path
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
import wave

from voice_pipeline import (CACHE_ROOT, PROFILE_ROOT, VOICE_ROOT, VoiceError,
                            IndexTTS25Provider, PRESENCE_DEFAULTS, postprocess, cache_state)
from voice_tuning import TUNING_DEFAULTS, STANDARD_PREVIEW_TEXT
from voice_profile_history import backup_profile, voice_id_locked


PROFILE_DEFAULTS = {
    'voice_id': '', 'display_name': '', 'profile_version': '1',
    'reference_audio': 'reference.wav', 'reference_text': '',
    'reference_language': 'zh', 'target_language': 'zh',
    'engine': 'indextts25', 'speed': 1.0, 'pause': 0.15,
    'emotion_mode': 'speaker', 'emotion_reference_audio': '', 'emotion_alpha': .5,
    'emotion_text': '', 'emotion_vector': [0.0]*8, 'postprocess_enabled': True,
    'bass_gain': 0, 'bass_frequency': 220, 'treble_gain': 0,
    'treble_frequency': 7500, 'compressor_threshold': -18,
    'compressor_ratio': 2.5, 'compressor_attack': 10,
    'compressor_release': 120, 'compressor_makeup': 2,
    'target_lufs': -16, 'true_peak': -1.5, 'sample_rate': 48000,
    'enabled': False, 'validation_status': 'draft', 'validation_message': '',
    **PRESENCE_DEFAULTS,
    **TUNING_DEFAULTS,
}
INT_FIELDS = ('sample_rate',)
FLOAT_FIELDS = ('pitch_semitones','deesser_intensity','deesser_max','deesser_frequency','speed', 'pause', 'emotion_alpha', 'bass_gain',
                'bass_frequency', 'treble_gain', 'treble_frequency',
                'presence_gain_db', 'presence_frequency_hz', 'presence_q',
                'compressor_threshold', 'compressor_ratio', 'compressor_attack',
                'compressor_release', 'compressor_makeup', 'target_lufs', 'true_peak')
ALLOWED_LANGUAGES = ('zh', 'en', 'ja', 'es', 'ar', 'yue', 'ko')
SUPPORTED_AUDIO = ('.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wma')
PREVIEW_ROOT = Path(_portable_path(r'D:\GPTData\temp\assembler_voice_previews'))


def _run(args: list, timeout: float = 90) -> str:
    import runtime_diagnostics as diag
    if diag.hub():
        try:return diag.process([str(x) for x in args],timeout=timeout,stage_name=Path(args[0]).name)
        except (diag.ProcessFailure,OSError) as exc:raise VoiceError(str(exc)) from exc
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        result = subprocess.run([str(x) for x in args], capture_output=True,
                                timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise VoiceError(f'参考音频处理超过{timeout:g}秒：{args[-1]}') from exc
    if result.returncode:
        raise VoiceError(result.stderr.decode('utf-8', 'replace')[-800:] or
                         f'音频工具退出码{result.returncode}')
    return result.stdout.decode('utf-8', 'replace')


def _folder_name(display_name: str) -> str:
    name = str(display_name).strip()
    if (not name or name in ('.', '..') or name.endswith(('.', ' ')) or
            re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or
            name.upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)),
                             *(f'LPT{i}' for i in range(1, 10))}):
        raise VoiceError('音色名称不能作为Windows文件夹名；请填写普通名称。')
    return name


def _profile_folder(root: Path, name: str) -> Path:
    target = (Path(root) / _folder_name(name)).resolve()
    if target.parent != Path(root).resolve():
        raise VoiceError('音色目录必须位于当前音色库内。')
    return target


def _existing_profile(root: Path, path: Path | None) -> Path | None:
    if path is None:
        return None
    candidate = Path(path).resolve()
    if candidate.name != 'profile.json' or candidate.parent.parent != Path(root).resolve():
        raise VoiceError('只能编辑当前音色库中的profile.json。')
    if not candidate.is_file():
        raise VoiceError(f'原音色配置不存在：{candidate}')
    return candidate


def _normalize(raw: dict) -> tuple[dict, list[str]]:
    result = dict(PROFILE_DEFAULTS)
    result.update({k: v for k, v in raw.items() if k in PROFILE_DEFAULTS})
    errors = []
    result['display_name'] = str(result['display_name']).strip()
    result['voice_id'] = str(result['voice_id']).strip()
    result['profile_version'] = str(result['profile_version']).strip()
    result['reference_text'] = str(result['reference_text']).strip()
    result['reference_language'] = str(result['reference_language']).strip().lower()
    result['target_language'] = str(result['target_language']).strip().lower()
    result['postprocess_enabled'] = result['postprocess_enabled'] in (True, 'True', 'true', '1', 1)
    result['presence_enabled'] = result['presence_enabled'] in (True, 'True', 'true', '1', 1)
    for key in ('pitch_enabled','deesser_enabled'):
        result[key]=result[key] in (True,'True','true','1',1)
    result['standard_preview_text']=str(result.get('standard_preview_text') or STANDARD_PREVIEW_TEXT).strip()
    if not 1<=len(result['standard_preview_text'])<=200:errors.append('标准试听文本须为1～200字。')
    if result.get('preview_seed') not in (None,'','None'):
        try:
            result['preview_seed']=int(result['preview_seed'])
            if not 0<=result['preview_seed']<=2**32-1:raise ValueError
        except (ValueError,TypeError):errors.append('候选种子须为0～4294967295的整数。')
    else:result['preview_seed']=None
    golden=result.get('golden_voice')
    if golden and (Path(str(golden)).name!=str(golden) or str(golden)!='golden_voice.wav'):
        errors.append('Golden Voice必须为音色目录内的golden_voice.wav。')
    result['engine'] = 'indextts25'
    if result['emotion_mode'] not in ('speaker', 'audio', 'text', 'vector'):
        errors.append('请选择有效的语气控制方式。')
    if isinstance(result['emotion_vector'], str):
        try:
            value = result['emotion_vector'].strip()
            result['emotion_vector'] = json.loads(value if value.startswith('[') else '['+value+']')
        except ValueError:
            errors.append('情绪向量须为8个逗号分隔的数字。')
    vector = result['emotion_vector']
    if not isinstance(vector, list) or len(vector) != 8 or any(not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1 for v in vector):
        errors.append('情绪向量须包含8个0～1之间的数字。')
    if result['target_language'] not in ('zh', 'en', 'ja', 'es', 'ar'):
        errors.append('IndexTTS-2.5目标语言可选中文、英语、日语、西班牙语、阿拉伯语；原语言字段仍保留在归档中。')
    if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', result['voice_id']):
        errors.append('voice_id只允许小写英文字母开头，后接小写字母、数字、_或-。')
    if not result['profile_version']:
        errors.append('profile_version不能为空。')
    if result['reference_language'] not in ALLOWED_LANGUAGES:
        errors.append('参考语言不支持；当前可选zh/en/ja/yue/ko。')
    if result['target_language'] not in ALLOWED_LANGUAGES:
        errors.append('目标语言不支持；当前可选zh/en/ja/yue/ko。')
    for key in INT_FIELDS:
        try:
            value = float(result[key])
            if not math.isfinite(value) or not value.is_integer():
                raise ValueError
            result[key] = int(value)
        except (ValueError, TypeError, OverflowError):
            errors.append(f'{key}必须是整数。')
    for key in FLOAT_FIELDS:
        try:
            value = float(result[key])
            if not math.isfinite(value):
                raise ValueError
            result[key] = value
        except (ValueError, TypeError, OverflowError):
            errors.append(f'{key}必须是有限数字。')
    limits = {
        'pitch_semitones':(-3,3),'deesser_intensity':(0,1),'deesser_max':(0,1),'deesser_frequency':(0,1),
        'speed': (0.5, 2.0), 'pause': (0, 5), 'emotion_alpha': (0, 1),
        'bass_gain': (-18, 18), 'treble_gain': (-18, 18),
        'bass_frequency': (20, 20000), 'treble_frequency': (20, 20000),
        'presence_gain_db': (-6, 6), 'presence_frequency_hz': (2500, 6000), 'presence_q': (.5, 2),
        'compressor_threshold': (-60, -1), 'compressor_ratio': (1, 20),
        'compressor_attack': (1, 200), 'compressor_release': (10, 2000),
        'compressor_makeup': (-12, 12), 'target_lufs': (-30, -10),
        'true_peak': (-6, -0.5), 'sample_rate': (48000, 48000),
    }
    for key, (low, high) in limits.items():
        if isinstance(result[key], (int, float)) and not low <= result[key] <= high:
            errors.append(f'{key}须在{low:g}～{high:g}之间。')
    return result, errors


def validate_reference(source: Path, ffprobe: str, ffmpeg: str) -> float:
    path = Path(source).resolve()
    if not path.is_file():
        raise VoiceError(f'未找到用户选择的参考音频：{path}')
    if path.suffix.lower() not in SUPPORTED_AUDIO:
        raise VoiceError(f'不支持的参考音频格式：{path.suffix}')
    if path.is_relative_to(CACHE_ROOT.resolve()) or path.is_relative_to((VOICE_ROOT / '测试' / '生成结果').resolve()):
        raise VoiceError('所选文件位于已生成配音目录；请选用户确认的原始参考音频。')
    info = json.loads(_run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', path]))
    stream = next((s for s in info.get('streams', []) if s.get('codec_type') == 'audio'), None)
    if stream is None:
        raise VoiceError('参考文件没有可解码的音轨。')
    try:
        duration = float(stream.get('duration') or info.get('format', {}).get('duration'))
    except (TypeError, ValueError) as exc:
        raise VoiceError('无法读取参考音频时长。') from exc
    if not 1.0 <= duration <= 20.0:
        raise VoiceError(f'参考音频长{duration:.2f}秒；请选1～20秒的清晰单段参考录音。')
    _run([ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-xerror',
          '-i', path, '-map', '0:a:0', '-f', 'null', '-'], timeout=90)
    return duration


def _reference_source(raw: dict, folder: Path) -> Path | None:
    selected = str(raw.get('reference_source', '')).strip().strip('"')
    if selected:
        return Path(selected).resolve()
    existing = folder / 'reference.wav'
    return existing if existing.is_file() else None


def _emotion_source(raw: dict, folder: Path) -> Path | None:
    selected = str(raw.get('emotion_reference_source', '')).strip().strip('"')
    if selected:
        return Path(selected).resolve()
    name = str(raw.get('emotion_reference_audio', '')).strip()
    existing = folder / name if name else folder / 'emotion_reference.wav'
    return existing if existing.is_file() else None


def _write_json(path: Path, data: dict) -> None:
    temporary = path.with_name(f'.profile.{uuid.uuid4().hex}.part')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        os.replace(temporary, path)
        saved = json.loads(path.read_text(encoding='utf-8'))
        if saved.get('voice_id') != data['voice_id'] or saved.get('validation_status') != data['validation_status']:
            raise VoiceError(f'保存后读回校验失败：{path}')
    finally:
        temporary.unlink(missing_ok=True)


def _copy_reference(source: Path, destination: Path, ffmpeg: str) -> None:
    if source.resolve() == destination.resolve():
        return
    temporary = destination.with_name(f'.reference.{uuid.uuid4().hex}.part.wav')
    try:
        if source.suffix.lower() == '.wav':
            shutil.copy2(source, temporary)
        else:
            # Container/codec conversion only. Do not EQ, compress, normalize,
            # change channel count, or resample the user's reference audio.
            _run([ffmpeg, '-hide_banner', '-v', 'error', '-nostdin', '-y', '-i', source,
                  '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', temporary], timeout=90)
        if not temporary.is_file() or temporary.stat().st_size <= 44:
            raise VoiceError('参考音频转换结果为空。')
        if destination.is_file():
            stamp = time.strftime('%Y%m%d_%H%M%S')
            backup_dir=destination.parent/'backup';backup_dir.mkdir(exist_ok=True)
            backup = backup_dir/f'{destination.stem}_before_{stamp}_{uuid.uuid4().hex[:6]}.wav'
            shutil.copy2(destination, backup)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def save_profile(raw: dict, *, enable: bool, root: Path = PROFILE_ROOT,
                 existing_path: Path | None = None, ffprobe: str,
                 ffmpeg: str, provider=None, history_dir: Path | None = None) -> dict:
    """Save a draft or validate and enable. Validation failures persist as invalid/disabled."""
    previous = _existing_profile(root, existing_path)
    folder = previous.parent if previous else _profile_folder(root, raw.get('display_name', ''))
    if not previous and folder.exists() and any(folder.iterdir()):
        raise VoiceError(f'音色文件夹已存在，未覆盖：{folder}')
    profile, errors = _normalize(raw)
    _folder_name(profile['display_name'])
    if previous:
        old = json.loads(previous.read_text(encoding='utf-8-sig'))
        if profile['voice_id'] != old.get('voice_id') and voice_id_locked(previous, history_dir):
            raise VoiceError('音色已有正式使用记录，ID已锁定；请另存为新音色。以后修改ID须迁移历史引用。')
    for file in Path(root).glob('*/profile.json'):
        if previous and file.resolve()==previous:continue
        try:used_id=json.loads(file.read_text(encoding='utf-8-sig')).get('voice_id','')
        except (OSError,ValueError):continue
        if used_id.casefold()==profile['voice_id'].casefold():
            raise VoiceError(f'voice_id已由其他音色使用：{file}')
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'profile.json'
    if previous: backup_profile(previous)
    # A probe write catches unreadable or read-only folders before modifying the reference.
    probe = folder / f'.write_probe_{uuid.uuid4().hex}'
    try:
        probe.write_bytes(b'ok')
        if probe.read_bytes() != b'ok':
            raise VoiceError(f'音色目录写入校验失败：{folder}')
    finally:
        probe.unlink(missing_ok=True)
    source = _reference_source(raw, folder)
    profile['reference_audio'] = 'reference.wav'
    source_valid = False
    if source is None:
        errors.append('尚未选择参考音频。')
    else:
        try:
            validate_reference(source, ffprobe, ffmpeg)
            source_valid = True
        except (VoiceError, OSError, ValueError) as exc:
            diag.error(exc,component="voice-profile")
            errors.append(str(exc))
    if source_valid:
        try:
            _copy_reference(source, folder / 'reference.wav', ffmpeg)
            validate_reference(folder / 'reference.wav', ffprobe, ffmpeg)
        except (VoiceError, OSError, ValueError) as exc:
            diag.error(exc,component="voice-profile")
            errors.append(f'参考音频保存失败：{exc}')
    emotion_source = _emotion_source(raw, folder)
    if profile['emotion_mode'] == 'audio' and emotion_source is None:
        errors.append('请选择独立语气参考音频。')
    if emotion_source is not None:
        try:
            validate_reference(emotion_source, ffprobe, ffmpeg)
            _copy_reference(emotion_source, folder / 'emotion_reference.wav', ffmpeg)
            profile['emotion_reference_audio'] = 'emotion_reference.wav'
        except (VoiceError, OSError, ValueError) as exc:
            diag.error(exc,component="voice-profile")
            errors.append(f'语气参考音频保存失败：{exc}')
    if enable and not errors:
        try:
            validated_preview=preview_profile(dict(profile, reference_source=str(folder/'reference.wav'),
                                  emotion_reference_source=str(folder/profile['emotion_reference_audio'])
                                  if profile['emotion_reference_audio'] else ''),
                            ffprobe=ffprobe,ffmpeg=ffmpeg,text=profile['standard_preview_text'],
                            provider=provider,root=root,preview_root=PREVIEW_ROOT)
            from voice_profile_history import release_preview
            release_preview(validated_preview)
        except Exception as exc:
            diag.error(exc,component='voice-profile-validation')
            errors.append('实际TTS/后处理验证失败：'+str(exc))
    if previous:
        try:profile['profile_version']=str(int(old.get('profile_version',1))+1)
        except ValueError:profile['profile_version']=str(old.get('profile_version','1'))+'+1'
    profile['enabled'] = bool(enable and not errors)
    profile['validation_status'] = ('ready' if profile['enabled'] else
                                    'invalid' if errors else 'migrated_pending_review'
                                    if previous and old.get('validation_status') == 'migrated_pending_review' else 'draft')
    profile['validation_message'] = '；'.join(errors)
    _write_json(path, profile)
    profile.update(profile_path=str(path), reference_path=str(folder / 'reference.wav'),
                   emotion_reference_path=str(folder / profile['emotion_reference_audio']) if profile['emotion_reference_audio'] else '',
                   available=profile['enabled'], unavailable_reason=profile['validation_message'])
    return profile


def disable_profile(path: Path, root: Path = PROFILE_ROOT) -> dict:
    file = _existing_profile(root, path)
    profile = json.loads(file.read_text(encoding='utf-8-sig'))
    backup_profile(file)
    profile['enabled'] = False
    profile['validation_status'] = 'disabled'
    profile['validation_message'] = '已由用户禁用。'
    _write_json(file, profile)
    return profile


def preview_profile(raw: dict, *, ffprobe: str, ffmpeg: str,
                    text: str = '你好，这是一段音色试听。',
                    provider: IndexTTS25Provider | None = None,
                    preview_root: Path = PREVIEW_ROOT,
                    existing_path: Path | None = None,
                    root: Path = PROFILE_ROOT, log=lambda _: None) -> Path:
    """A preview never calls prepare_voice and never writes CACHE_ROOT or a Manifest."""
    if not text.strip() or len(text) > 200:
        raise VoiceError('试听文本应为1～200字。')
    previous = _existing_profile(root, existing_path)
    folder = previous.parent if previous else _profile_folder(root, raw.get('display_name', ''))
    # Disk defaults are used only for fields absent from the current UI data.
    values = json.loads(previous.read_text(encoding='utf-8-sig')) if previous else {}
    values.update(raw)
    profile, errors = _normalize(values)
    source = _reference_source(raw, folder)
    if source is None:
        errors.append('尚未选择参考音频。')
    else:
        try:
            validate_reference(source, ffprobe, ffmpeg)
        except (VoiceError, OSError, ValueError) as exc:
            diag.error(exc,component="voice-profile")
            errors.append(str(exc))
    emotion_source = _emotion_source(values, folder)
    if profile['emotion_mode'] == 'audio':
        if emotion_source is None:
            errors.append('请选择独立语气参考音频。')
        else:
            try:
                validate_reference(emotion_source, ffprobe, ffmpeg)
            except (VoiceError, OSError, ValueError) as exc:
                errors.append(str(exc))
    if errors:
        raise VoiceError('；'.join(errors))
    from temp_lifecycle import PREVIEW_NAMES, prune_transient_runs
    task = Path(preview_root) / uuid.uuid4().hex
    from file_mutex import file_mutex
    with file_mutex(Path(preview_root)/'.lifecycle.lock'):
        task.mkdir(parents=True, exist_ok=False)
        (task/'.protected').write_text('生成及试听中',encoding='utf-8')
    # A stable source path lets the worker reuse reference conditioning across auditions.
    profile.update(reference_path=str(source.resolve()), available=True, unavailable_reason='')
    profile['emotion_reference_path'] = str(emotion_source) if emotion_source else ''
    stamp = time.strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:12]
    raw_audio = task / f'preview_raw_{stamp}.wav'
    processed = task / f'preview_processed_{stamp}.wav'
    from hashlib import sha256
    snapshot = {'timestamp':time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'voice_id':profile['voice_id'], 'reference_path':str(source.resolve()),
                'reference_sha256':sha256(source.read_bytes()).hexdigest(),
                'target_text':text.strip(), 'ui_value':dict(raw),
                'executed_value':dict(profile), 'raw_path':str(raw_audio.resolve()),
                'processed_path':str(processed.resolve()),
                'player_path':None, 'actual_ffmpeg_filter_chain':'', 'status':'running'}
    trace = task / 'preview_log.json'
    trace.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    log('UI_VALUE=' + json.dumps(raw, ensure_ascii=False))
    log('EXECUTED_VALUE=' + json.dumps(profile, ensure_ascii=False))
    log('RAW_PATH=' + str(raw_audio.resolve()))
    log('POSTPROCESS=' + ('ON' if profile['postprocess_enabled'] else 'OFF'))
    provider = provider or IndexTTS25Provider()
    if getattr(provider,'engine',None)=='indextts25':
        provider.request_context={'video_id':None,'job_id':'preview:'+task.name,'script_id':None}
    try:
        provider.synthesize(text.strip(), profile, raw_audio)
        snapshot['tts_result']=getattr(provider,'last_result',None)
        if getattr(provider,'engine',None)=='indextts25':
            from voice_pipeline import model_versions
            snapshot['model_versions']=model_versions(provider.root,profile)
        if cache_state(raw_audio, ffmpeg) != 'CACHE_READY':
            raise VoiceError('试听RAW文件不可完整解码。')
        output = processed
        snapshot.update(postprocess(raw_audio, output, profile, ffmpeg, log=log))
        if cache_state(output, ffmpeg) != 'CACHE_READY':
            raise VoiceError('试听播放文件不可完整解码。')
        snapshot.update(status='ready', playback_target=str(output.resolve()),
                        raw_sha256=sha256(raw_audio.read_bytes()).hexdigest(),
                        output_sha256=sha256(output.read_bytes()).hexdigest())
        log('PROCESSED_PATH=' + str(processed.resolve()))
        trace.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        from temp_lifecycle import cleanup_known_folder
        from voice_profile_history import release_preview
        release_preview(processed)
        cleanup_known_folder(task, Path(preview_root), PREVIEW_NAMES, kind='voice_preview_failed')
        raise
    prune_transient_runs(Path(preview_root), PREVIEW_NAMES, kind='voice_preview', keep=task)
    return output


def record_preview_playback(path: Path, log=lambda _: None) -> None:
    """Record the actual successfully loaded source, not an assumed player path."""
    path = Path(path).resolve()
    trace = path.parent / 'preview_log.json'
    snapshot = json.loads(trace.read_text(encoding='utf-8'))
    if str(path) not in (snapshot['processed_path'],snapshot['raw_path']) or not path.is_file():
        raise VoiceError('试听播放器路径与本轮输出不一致。')
    snapshot['player_path'] = str(path)
    snapshot['status'] = 'playing'
    trace.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    log('PLAYER_PATH=' + str(path))
