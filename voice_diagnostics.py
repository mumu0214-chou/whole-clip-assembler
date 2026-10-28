# -*- coding: utf-8 -*-
"""IndexTTS RAW/postprocessed comparison without formal cache writes."""
from __future__ import annotations

from datetime import datetime
from hashlib import sha256
from pathlib import Path
import json
import re
import shutil
import subprocess
import uuid

from voice_pipeline import (INDEXTTS_ROOT, VOICE_ROOT, IndexTTS25Provider, VoiceError,
                            model_versions, postprocess)
from voice_profile_manager import _normalize, _reference_source, _emotion_source, validate_reference


DIAGNOSTIC_ROOT = VOICE_ROOT / 'diagnostics' / 'voice_compare'


def _digest(path: Path):
    h = sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def _audio_stats(path: Path, ffprobe: str, ffmpeg: str) -> dict:
    info = subprocess.run([ffprobe, '-v', 'error', '-show_streams', '-show_format',
                           '-of', 'json', str(path)], capture_output=True,
                          timeout=30, check=True)
    data = json.loads(info.stdout.decode('utf-8', 'replace'))
    stream = next((x for x in data['streams'] if x.get('codec_type') == 'audio'), None)
    if not stream:
        raise VoiceError(f'诊断音频没有音轨：{path}')
    check = subprocess.run([ffmpeg, '-hide_banner', '-nostdin', '-i', str(path),
                            '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True,
                           timeout=120)
    stderr = check.stderr.decode('utf-8', 'replace')
    if check.returncode:
        raise VoiceError(f'诊断音频不可完整解码：{path}：{stderr[-500:]}')
    mean = re.search(r'mean_volume:\s*([-\d.]+) dB', stderr)
    peak = re.search(r'max_volume:\s*([-\d.]+) dB', stderr)
    from voice_audio_analysis import audio_features
    return {**audio_features(path,ffmpeg),'duration_seconds': float(stream.get('duration') or data['format']['duration']),
            'sample_rate': int(stream['sample_rate']), 'channels': stream['channels'],
            'mean_dbfs': float(mean[1]) if mean else None,
            'peak_dbfs': float(peak[1]) if peak else None,
            'sha256': _digest(path)}


def run_voice_diagnostic(raw_profile: dict, *, target_text: str, ffprobe: str, ffmpeg: str,
                         provider: IndexTTS25Provider | None = None,
                         existing_path: Path | None = None,
                         webui_baseline: Path | None = None,
                         root: Path = DIAGNOSTIC_ROOT) -> Path:
    text = target_text.strip()
    if not text or len(text) > 200:
        raise VoiceError('诊断目标文本应为1～200字。相同文本更便于比较语速。')
    profile, errors = _normalize(raw_profile)
    folder = Path(existing_path).parent if existing_path else Path(raw_profile.get('display_name', ''))
    reference = _reference_source(raw_profile, folder)
    if reference is None:
        errors.append('尚未选择参考音频。')
    else:
        validate_reference(reference, ffprobe, ffmpeg)
    emotion = _emotion_source(raw_profile, folder)
    if profile['emotion_mode'] == 'audio':
        if emotion is None: errors.append('请选择独立语气参考音频。')
        else: validate_reference(emotion, ffprobe, ffmpeg)
    if errors: raise VoiceError('；'.join(errors))
    provider = provider or IndexTTS25Provider()
    profile.update(reference_path=str(reference.resolve()), emotion_reference_path=str(emotion.resolve()) if emotion else '', available=True)
    models = model_versions(getattr(provider, 'root', INDEXTTS_ROOT), profile)
    output = Path(root) / (datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    if getattr(provider,'engine',None)=='indextts25':
        provider.request_context={'video_id':None,'job_id':'diagnostic:'+output.name,'script_id':None}
    baseline = None
    if webui_baseline is not None or reference is not None:
        original = Path(webui_baseline) if webui_baseline else reference
        if not original.is_file(): raise VoiceError(f'基准音频不存在：{original}')
        baseline = output / 'baseline.wav'
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(original), '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', str(baseline)], capture_output=True, timeout=90, check=True)
    raw, processed = output / 'indextts_raw.wav', output / 'indextts_processed.wav'
    snapshot = {'engine':'indextts25', 'profile':profile, 'target_text':text,
                'reference_sha256':_digest(reference), 'model_versions':models,
                'emotion_reference_sha256':_digest(emotion) if emotion else None,
                'duration_factor':1/profile['speed'], 'baseline_provided':bool(webui_baseline),
                'baseline_source':str(original.resolve())}
    try:
        provider.synthesize(text, profile, raw)
        snapshot['reference']=_audio_stats(reference,ffprobe,ffmpeg)
        snapshot['indextts_raw'] = _audio_stats(raw, ffprobe, ffmpeg)
        snapshot.update(postprocess(raw, processed, profile, ffmpeg))
        snapshot['indextts_processed'] = _audio_stats(processed, ffprobe, ffmpeg)
        if baseline: snapshot['baseline'] = _audio_stats(baseline, ffprobe, ffmpeg)
        snapshot['status'] = 'completed'
        from voice_audio_analysis import diagnostic_hints
        (output/'diagnostic_summary.json').write_text(json.dumps(
            {'measurements':{k:snapshot[k] for k in ('reference','baseline','indextts_raw','indextts_processed')},
             'hints':diagnostic_hints(snapshot)},ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception as exc:
        snapshot.update(status='failed', error=str(exc))
        raise
    finally:
        (output / 'params.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    from temp_lifecycle import DIAGNOSTIC_NAMES, prune_transient_runs
    prune_transient_runs(Path(root), DIAGNOSTIC_NAMES, kind='voice_diagnostic', keep=output)
    return output
