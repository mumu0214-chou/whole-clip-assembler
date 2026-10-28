# -*- coding: utf-8 -*-
"""Optional export transforms. No network, platform checks, or original-media writes."""
from __future__ import annotations
import copy
import hashlib
import math
import os
import re
from pathlib import Path
import uuid

PROCESSING_REVISION = 'export_processing_v3_timing_transitions'
UNIQVID_URL = 'https://uniqvid.com/'
SCOPE_VALUES = ('all', 'random_count', 'random_probability', 'positions')
PROCESSING_DEFAULTS = {
    'processing_enabled': False,
    'processing_color': False,
    'processing_brightness': 1.0,
    'processing_contrast': 1.0,
    'processing_saturation': 1.0,
    'processing_color_scope': 'all',
    'processing_color_random_min': 1,
    'processing_color_random_max': 3,
    'processing_color_probability': 0.35,
    'processing_color_positions': '',
    'processing_mirror': False,
    'processing_mirror_scope': 'all',
    'processing_mirror_random_min': 1,
    'processing_mirror_random_max': 3,
    'processing_mirror_probability': 0.35,
    'processing_mirror_positions': '',
    'processing_zoom': False,
    'processing_zoom_min': 1.015,
    'processing_zoom_max': 1.04,
    'processing_zoom_scope': 'random_count',
    'processing_zoom_random_min': 1,
    'processing_zoom_random_max': 3,
    'processing_zoom_probability': 0.35,
    'processing_zoom_positions': '',
    'processing_speed_enabled': False,
    'processing_speed': 1.0,
    'processing_drop': False,
    'processing_drop_every': 30,
    'processing_drop_scope': 'all',
    'processing_drop_random_min': 1,
    'processing_drop_random_max': 2,
    'processing_drop_probability': 0.25,
    'processing_drop_positions': '',
    'processing_fps_enabled': False,
    'processing_fps': 30,
    'processing_hash_refresh': False,
}


# The independent timeline layer owns timing changes. Importing defaults is cheap.
from timeline_processing import TIMELINE_DEFAULTS, validate_timeline_settings
PROCESSING_DEFAULTS.update(TIMELINE_DEFAULTS)


def _validate_scope(cfg: dict, prefix: str) -> None:
    scope = str(cfg.get(f'{prefix}_scope', PROCESSING_DEFAULTS[f'{prefix}_scope']))
    if scope not in SCOPE_VALUES:
        raise ValueError(f'{prefix}_scope无效。')
    mn = int(cfg.get(f'{prefix}_random_min', PROCESSING_DEFAULTS[f'{prefix}_random_min']))
    mx = int(cfg.get(f'{prefix}_random_max', PROCESSING_DEFAULTS[f'{prefix}_random_max']))
    if mn < 0 or mx < mn or mx > 100:
        raise ValueError(f'{prefix}随机片段数量范围无效。')
    prob = float(cfg.get(f'{prefix}_probability', PROCESSING_DEFAULTS[f'{prefix}_probability']))
    if not math.isfinite(prob) or not 0 <= prob <= 1:
        raise ValueError(f'{prefix}随机比例必须在0～1之间。')
    parse_positions(str(cfg.get(f'{prefix}_positions', '')))


def validate_processing(cfg: dict) -> None:
    validate_timeline_settings(cfg)
    for k in ('processing_enabled', 'processing_color', 'processing_mirror', 'processing_zoom',
              'processing_speed_enabled', 'processing_drop', 'processing_fps_enabled',
              'processing_hash_refresh'):
        if not isinstance(cfg.get(k, PROCESSING_DEFAULTS[k]), bool):
            raise ValueError(f'{k}必须是布尔值，请通过界面勾选。')
    for k, low, high in (('processing_brightness', .5, 1.5),
                          ('processing_contrast', .5, 1.5),
                          ('processing_saturation', 0., 1.5),
                          ('processing_speed', .5, 2.0),
                          ('processing_zoom_min', 1.0, 1.25),
                          ('processing_zoom_max', 1.0, 1.25)):
        v = float(cfg.get(k, PROCESSING_DEFAULTS[k]))
        if not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f'{k}须在{low}～{high}之间。')
    if float(cfg.get('processing_zoom_min', 1.0)) > float(cfg.get('processing_zoom_max', 1.0)):
        raise ValueError('动态缩放最小值不能大于最大值。')
    n = cfg.get('processing_drop_every', 30)
    if isinstance(n, bool) or int(n) != float(n) or not 2 <= int(n) <= 600:
        raise ValueError('抽帧间隔须为2～600的整数；抽帧可能降低流畅度。')
    if int(cfg.get('processing_fps', 30)) not in (24, 25, 30, 50, 60):
        raise ValueError('输出帧率请选择24、25、30、50、60。')
    for prefix in ('processing_color', 'processing_mirror', 'processing_zoom', 'processing_drop'):
        _validate_scope(cfg, prefix)


def parse_positions(text: str) -> set[int]:
    """Parse 1-based positions such as '2,4,6-8'. Empty means no positions."""
    text = str(text or '').strip()
    if not text:
        return set()
    result: set[int] = set()
    for part in re.split(r'[,，\s]+', text):
        if not part:
            continue
        if '-' in part:
            bits = part.split('-', 1)
            if len(bits) != 2 or not bits[0].isdigit() or not bits[1].isdigit():
                raise ValueError(f'片段位置格式错误：{part}')
            a, b = int(bits[0]), int(bits[1])
            if a < 1 or b < a or b > 999:
                raise ValueError(f'片段位置范围错误：{part}')
            result.update(range(a, b + 1))
        else:
            if not part.isdigit() or not 1 <= int(part) <= 999:
                raise ValueError(f'片段位置格式错误：{part}')
            result.add(int(part))
    return result


def processing_settings(cfg: dict) -> dict:
    from visual_controls import active, validate, snapshot
    validate(cfg)
    validate_processing(cfg)
    enabled = bool(cfg.get('processing_enabled', False))
    color = enabled and bool(cfg.get('processing_color', False))
    speed = (float(cfg.get('processing_speed', 1.0))
             if enabled and cfg.get('processing_speed_enabled', False) else 1.0)
    result = {
        'revision': PROCESSING_REVISION, 'enabled': enabled,
        'color': color,
        'brightness': float(cfg.get('processing_brightness', 1.0)) if color else 1.0,
        'contrast': float(cfg.get('processing_contrast', 1.0)) if color else 1.0,
        'saturation': float(cfg.get('processing_saturation', 1.0)) if color else 1.0,
        'mirror': enabled and bool(cfg.get('processing_mirror', False)),
        'zoom': enabled and bool(cfg.get('processing_zoom', False)),
        'zoom_min': float(cfg.get('processing_zoom_min', 1.015)),
        'zoom_max': float(cfg.get('processing_zoom_max', 1.04)),
        'speed': speed,
        'drop': enabled and bool(cfg.get('processing_drop', False)),
        'drop_every': int(cfg.get('processing_drop_every', 30)),
        'fps': int(cfg.get('processing_fps', 30) if enabled and cfg.get('processing_fps_enabled', False)
                   else cfg.get('fps', 30)),
        'hash_refresh': enabled and bool(cfg.get('processing_hash_refresh', False)),
    }
    for name in ('color', 'mirror', 'zoom', 'drop'):
        prefix = f'processing_{name}'
        result[f'{name}_scope'] = str(cfg.get(f'{prefix}_scope', PROCESSING_DEFAULTS[f'{prefix}_scope']))
        result[f'{name}_random_min'] = int(cfg.get(f'{prefix}_random_min', PROCESSING_DEFAULTS[f'{prefix}_random_min']))
        result[f'{name}_random_max'] = int(cfg.get(f'{prefix}_random_max', PROCESSING_DEFAULTS[f'{prefix}_random_max']))
        result[f'{name}_probability'] = float(cfg.get(f'{prefix}_probability', PROCESSING_DEFAULTS[f'{prefix}_probability']))
        result[f'{name}_positions'] = str(cfg.get(f'{prefix}_positions', PROCESSING_DEFAULTS[f'{prefix}_positions']))
    from timeline_processing import timeline_settings
    result['timeline'] = timeline_settings(cfg)
    from ball_pacing import active as ball_pacing_active
    if active(cfg):
        result['visual_controls'] = snapshot(cfg)
        custom = cfg['picture_effect_mode'] == 'custom'
        result.update(color=custom and bool(cfg.get('processing_color')),
                      zoom=custom and bool(cfg.get('processing_zoom')))
        for key in ('brightness','contrast','saturation'):
            result[key] = float(cfg.get('processing_'+key,1.0)) if result['color'] else 1.0
    elif ball_pacing_active(cfg):
        result['ball_visual_effect'] = cfg.get('ball_visual_effect','original')
    return result


def normalize_processing_config(cfg: dict) -> dict:
    result = copy.deepcopy(cfg)
    s = processing_settings(result)
    result['fps'] = s['fps']
    return result


def _scope_label(scope: str) -> str:
    return {'all': '整条成片', 'random_count': '随机几个片段',
            'random_probability': '随机比例', 'positions': '指定片段位置'}.get(scope, scope)


def processing_warnings(cfg: dict) -> list[str]:
    s = processing_settings(cfg)
    from visual_controls import active, summary
    visual_notes = [summary(cfg)+'；仅用于新方案，旧批次保持。'] if active(cfg) else []
    if not s['enabled']:
        return visual_notes
    notes = visual_notes + ['导出处理已启用；不更改原素材，处理参数随方案保存。文件差异不代表原创性或平台审核结果。']
    if s['mirror']:
        notes.append(f"镜像作用范围：{_scope_label(s['mirror_scope'])}；新增顶部文案不反向，源画面自带文字/招牌仍会反向。")
    if s['color']:
        notes.append(f"调色作用范围：{_scope_label(s['color_scope'])}；请确认肉色及脂肪细节没有失真。")
    if s['zoom']:
        notes.append(f"动态缩放作用范围：{_scope_label(s['zoom_scope'])}；缩放范围{s['zoom_min']:.3f}～{s['zoom_max']:.3f}。")
    if s['speed'] != 1 and cfg.get('processing_speed_scope', 'all') == 'all' and cfg.get('processing_speed_values', 'fixed') == 'fixed':
        notes.append(f"整条画面按{s['speed']:g}倍速完整经过原时间区间，成片时长和BGM匹配会重新计算；BGM自身不变速。")
    if s['drop']:
        notes.append(f"抽帧作用范围：{_scope_label(s['drop_scope'])}；仅改变画面时间采样，不缩短片段时长，可能卡顿或降低流畅度。")
    if s['hash_refresh']:
        notes.append('文件指纹变更通过写入导出标识实现；会记录实际MD5，不伪造拍摄设备/日期，不改变原素材去重身份。')
    from timeline_processing import timing_warnings
    notes.extend(timing_warnings(cfg))
    return notes


def _select_indices(n: int, settings: dict, name: str, rng) -> set[int]:
    if n <= 0 or not settings.get(name):
        return set()
    scope = settings.get(f'{name}_scope', 'all')
    all_idx = list(range(n))
    if scope == 'all':
        return set(all_idx)
    if scope == 'positions':
        return {p - 1 for p in parse_positions(settings.get(f'{name}_positions', '')) if 1 <= p <= n}
    if scope == 'random_probability':
        prob = float(settings.get(f'{name}_probability', 0.35))
        return {i for i in all_idx if rng.random() < prob}
    mn = max(0, min(n, int(settings.get(f'{name}_random_min', 1))))
    mx = max(mn, min(n, int(settings.get(f'{name}_random_max', mn))))
    count = rng.randint(mn, mx) if mx else 0
    return set(rng.sample(all_idx, count)) if count else set()


def build_clip_effects(clips: list[dict], cfg: dict, rng) -> list[dict]:
    """Freeze per-clip visual/frame-sampling decisions into the plan manifest."""
    s = processing_settings(cfg)
    effects = [{
        'mirror': False, 'drop': False, 'drop_every': s['drop_every'],
        'color': False, 'brightness': 1.0, 'contrast': 1.0, 'saturation': 1.0,
        'zoom': False, 'zoom_start': 1.0, 'zoom_end': 1.0,
    } for _ in clips]
    from visual_controls import active as unified_visuals
    if s['enabled'] or unified_visuals(cfg):
        selections = {name: _select_indices(len(clips), s, name, rng)
                      for name in ('mirror', 'drop', 'color', 'zoom')}
        for i, eff in enumerate(effects):
            if i in selections['mirror']:
                eff['mirror'] = True
            if i in selections['drop']:
                eff['drop'] = True
            if i in selections['color']:
                eff.update(color=True, brightness=s['brightness'], contrast=s['contrast'], saturation=s['saturation'])
            if i in selections['zoom']:
                amount = rng.uniform(s['zoom_min'], s['zoom_max'])
                eff['zoom'] = True
                if rng.random() < 0.5:
                    eff['zoom_start'], eff['zoom_end'] = 1.0, amount
                else:
                    eff['zoom_start'], eff['zoom_end'] = amount, 1.0
    from ball_pacing import active as ball_pacing_active
    if unified_visuals(cfg) or ball_pacing_active(cfg):
        effect=cfg['picture_effect_mode'] if unified_visuals(cfg) else cfg.get('ball_visual_effect','original')
        for i, eff in enumerate(effects):
            if effect=='gentle_push' and float(clips[i]['duration'])>=2.5 and i%3==0:
                eff.update(zoom=True,zoom_start=1.0,zoom_end=1.025,zoom_ease=True)
            elif effect in ('fresh','texture'):
                brightness,contrast,saturation=((1.025,1.015,1.0) if effect=='fresh'
                                                else (1.0,1.045,1.025))
                eff.update(color=True,brightness=brightness,contrast=contrast,saturation=saturation)
    return effects


def file_md5(path: Path) -> str:
    h = hashlib.md5(usedforsecurity=False)
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def frame_sampling_filter(duration: float, fps: int, every: int) -> str:
    """Keep timestamps; retain short edge guards and discard selected interior samples."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('抽帧输入时长无效。')
    end_guard = max(0., duration - max(.1, 2 / fps))
    select = f"select='lt(t,0.1)+gte(t,{end_guard:.9f})+not(eq(mod(n+1,{every}),0))'"
    return (f'setpts=PTS-STARTPTS,{select},fps=fps={fps}:round=up:eof_action=pass,'
            f'pad=ceil(iw/2)*2:ceil(ih/2)*2,setsar=1,format=yuv420p,'
            f'tpad=stop_mode=clone:stop_duration={1/fps:.9f}')


def prepare_sampled_media(media: dict, cfg: dict, cache_dir: Path,
                          ffmpeg: str, ffprobe: str, log=lambda message: None,
                          clip_effects: dict | None = None, cancel=None) -> tuple[Path, bool]:
    """Create/cache a silent derivative only when this clip is selected for frame sampling."""
    from core import AssemblyError, atomic_json, read_json, object_hash, sha256, probe, run_capture, clip_frames, stream_process
    s = processing_settings(cfg)
    eff = clip_effects or {}
    should_drop = bool(eff.get('drop', s['drop'] and s.get('drop_scope') == 'all'))
    original = Path(media['path']).resolve()
    if not should_drop and 'duration' not in media:
        return original, True
    start = float(media.get('source_in_seconds', eff.get('trim_head_seconds', 0)))
    end = float(media.get('source_out_seconds', float(media['duration'])-float(eff.get('trim_tail_seconds', 0))))
    if not math.isfinite(start + end) or not 0 <= start < end <= float(media['duration']) + 1e-6:
        raise AssemblyError('最终原片取窗无效。')
    every = int(eff.get('drop_every', s['drop_every']))
    if not should_drop:
        log(f'直接读取原片取窗（无预编码）：{original} [{start:.3f}, {end:.3f}]')
        return original, True
    kept = end - start
    key = object_hash({'input_hash': media['hash'], 'every': every, 'fps': s['fps'],
                       'revision': PROCESSING_REVISION, 'duration': media['duration'],
                       'window_start': round(start,9), 'window_end': round(end,9), 'drop': should_drop})
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    target, receipt = cache_dir / f'{key}.mp4', cache_dir / f'{key}.json'
    saved = read_json(receipt, {})
    if target.is_file() and saved.get('key') == key and saved.get('output_sha256') == sha256(target):
        log(f"派生缓存命中：{target}；最终取窗[{start:.3f},{end:.3f}]")
        return target, True
    if original == target.resolve():
        raise AssemblyError('拒绝覆盖原素材。')
    if sha256(original) != media['hash']:
        raise AssemblyError(f'原文件已改变，请重新扫描：{original}')
    frames = (max(1,math.floor(kept*s['fps']+1e-8)) if 'random_duration_applied' in media or media.get('micro_trim_applied')
              else clip_frames(kept, s['fps']))
    filters = []
    filters.append(f"trim=start={start:.9f}:end={end:.9f},setpts=PTS-STARTPTS")
    filters.append(frame_sampling_filter(kept, s['fps'], every))
    temp = cache_dir / f'{key}.{uuid.uuid4().hex}.part.mp4'
    try:
        started = __import__('time').monotonic()
        log(f"抽帧缓存编码开始：{original} -> {target}；最终取窗[{start:.3f},{end:.3f}]")
        stream_process([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-progress', 'pipe:1',
                     '-i', str(original), '-map', '0:v:0', '-an', '-sn', '-dn',
                     '-vf', ','.join(filters),
                     '-frames:v', str(frames), '-c:v', 'libx264', '-preset', 'fast', '-crf', '16',
                     '-threads', '2', '-pix_fmt', 'yuv420p', '-map_metadata', '-1',
                     '-movflags', '+faststart', str(temp)], cache_dir / f'{key}.encode.log', log,
                     max(90., float(media['duration']) * 20), stage=f'抽帧缓存编码 {temp}', progress_seconds=kept,
                     cancel=cancel)
        log(f"抽帧缓存编码完成：{temp}；耗时{__import__('time').monotonic()-started:.1f}秒")
        checked_at = __import__('time').monotonic()
        log(f'抽帧缓存媒体检查开始：{temp}')
        meta = probe(temp, ffprobe)
        if meta['has_audio'] or not meta['has_video'] or abs(meta['duration'] - frames / s['fps']) > 1.1 / s['fps']:
            raise AssemblyError('修边/抽帧派生文件时长/音轨检查失败，未采用结果。')
        if meta.get('nb_frames') not in (None, 'N/A') and int(meta['nb_frames']) != frames:
            raise AssemblyError('修边/抽帧派生文件帧数不符合预期，未采用结果。')
        log(f'抽帧缓存媒体检查完成：{temp}；耗时{__import__("time").monotonic()-checked_at:.1f}秒')
        decoded_at = __import__('time').monotonic()
        log(f'抽帧缓存完整解码开始：{temp}')
        stream_process([ffmpeg, '-v', 'error', '-nostdin', '-progress', 'pipe:1', '-i', str(temp),
                        '-map', '0:v:0', '-f', 'null', '-'], cache_dir / f'{key}.decode.log', log,
                       max(180,frames/s['fps']*20), stage=f'抽帧缓存完整解码 {temp}', progress_seconds=frames/s['fps'],
                       cancel=cancel)
        log(f'抽帧缓存完整解码完成：{temp}；耗时{__import__("time").monotonic()-decoded_at:.1f}秒')
        if sha256(original) != media['hash']:
            raise AssemblyError('处理中原文件发生变化，未提交派生文件。')
        os.replace(temp, target)
        atomic_json(receipt, {'key': key, 'input_sha256': media['hash'], 'output_sha256': sha256(target),
                              'duration': meta['duration'], 'frames': frames, 'audio': 'none',
                              'source_in_seconds': start, 'source_out_seconds': end, 'frame_sampling': should_drop,
                              'description': 'explicit trim then timestamp-preserving sampling; originals unchanged'})
        return target, False
    except Exception as exc:
        raise AssemblyError(f'修边/抽帧处理失败：{exc}\n请检查FFmpeg是否包含select/fps/tpad/libx264；未覆盖原文件。') from exc
    finally:
        temp.unlink(missing_ok=True)
