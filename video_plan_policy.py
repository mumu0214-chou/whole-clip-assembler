# -*- coding: utf-8 -*-
"""Frozen mode, duration and top-text decisions for new five-stage plans."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import random
import copy


LEGACY_REVISION = 'mode_top_duration_v1'
PREVIOUS_REVISION = 'mode_top_duration_v2'
REVISION = 'mode_top_duration_v3'
MODES = ('纯画面+BGM', '字幕+BGM', '配音+BGM', '配音+字幕+BGM')
MODE_LABELS = {MODES[0]:MODES[0], MODES[1]:'底部字幕+BGM',
               MODES[2]:MODES[2], MODES[3]:'配音+底部字幕+BGM'}
NARRATED = frozenset(MODES[2:])


def _percent(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError('比例必须填写0～100的数字。') from exc
    if not result.is_finite() or not 0 <= result <= 100:
        raise ValueError('比例必须填写0～100的数字。')
    return result


def apportion(count: int, percentages: list, labels: list[str] | tuple[str, ...]) -> list[int]:
    """Largest remainder with input order as the stable tie breaker."""
    values = [_percent(value) for value in percentages]
    if sum(values) != 100:
        raise ValueError('视频模式比例合计必须为100%。' if tuple(labels) == MODES else '比例合计必须为100%。')
    quotas = [Decimal(count) * value / 100 for value in values]
    integers = [int(quota) for quota in quotas]
    remaining = count - sum(integers)
    for index in sorted(range(len(values)), key=lambda i: (-(quotas[i] - integers[i]), i))[:remaining]:
        integers[index] += 1
    return integers


def mode_counts(cfg: dict) -> dict[str, int]:
    ratios = cfg['video_mode_percentages']
    return dict(zip(MODES, apportion(int(cfg['count']), [ratios.get(mode, 0) for mode in MODES], MODES)))


def _build_schedule_v1(cfg: dict, natural_duration_frames: list[int] | None = None) -> list[dict]:
    """Run once before TTS; the saved schedule is the source of truth on resume."""
    from core import AssemblyError

    try:
        counts = mode_counts(cfg)
        count = int(cfg['count'])
        rng = random.Random(str(cfg['seed']) + ':mode-top-duration-v1')
        modes = [mode for mode in MODES for _ in range(counts[mode])]
        rng.shuffle(modes)
        low = math.ceil(float(cfg['preferred_min']) * cfg['fps'] - 1e-7)
        high = math.floor(float(cfg['preferred_max']) * cfg['fps'] + 1e-7)
        natural_choices = []
        if cfg.get('music_mode') == 'natural' and natural_duration_frames is not None:
            if isinstance(natural_duration_frames, dict):
                all_choices = natural_duration_frames.get('all', [])
            else:
                all_choices = natural_duration_frames
            natural_choices = sorted(set(int(value) for value in all_choices))
            if not natural_choices:
                raise ValueError('没有满足当前时长范围的已确认音乐自然区间。')
            preferred_choices = [value for value in natural_choices if low <= value <= high]
            natural_choices = preferred_choices or natural_choices
        schedule = []
        for mode in modes:
            choices = natural_choices
            if (cfg.get('bgm_rule_version') and mode in NARRATED and
                    isinstance(natural_duration_frames, dict)):
                choices = sorted(set(int(value) for value in natural_duration_frames.get('instrumental', [])))
                choices = [value for value in choices if low <= value <= high] or choices
                if not choices:
                    raise ValueError('NO_SUITABLE_INSTRUMENTAL_BGM：当前视频包含配音，但纯音乐库中没有符合条件的BGM。')
            schedule.append({'video_mode':mode,
                             'planned_video_frames':rng.choice(choices) if choices else rng.randint(low, high)})
        narrated = [i for i, entry in enumerate(schedule) if entry['video_mode'] in NARRATED]
        post_policy = cfg['post_voice_policy']
        post_percent = _percent(cfg['post_voice_top_percent'])
        if post_policy == 'top':
            post_top_count = len(narrated)
        elif post_policy == 'plain':
            post_top_count = 0
        else:
            post_top_count = apportion(len(narrated), [post_percent, 100-post_percent], ['顶部文案', '纯画面'])[0]
        timing = cfg['top_text_timing']
        top_policy = cfg['top_text_use_policy']
        if top_policy == 'none':
            top_count = 0
            post_top_count = 0
        elif top_policy == 'all':
            top_count = count
        else:
            top_percent = _percent(cfg['top_text_use_percent'])
            top_count = apportion(count, [top_percent, 100-top_percent], ['顶部文案', '无顶部文案'])[0]
        if top_count == 0:
            post_top_count = 0
        rng.shuffle(narrated)
        post_top = set(narrated[:post_top_count]) if timing == 'auto' else set()
        if timing == 'after_voice':
            eligible = set(range(count))
            mandatory = set()
        elif timing == 'auto':
            # A voice+subtitle plan with a plain post-voice phase has no safe
            # automatic top-text window. Never count it as visually enabled.
            eligible = {i for i, entry in enumerate(schedule)
                        if entry['video_mode'] != '配音+字幕+BGM'} | post_top
            mandatory = post_top
        else:
            eligible = set(range(count))
            mandatory = set()
        if top_count < len(mandatory) or top_count > len(eligible):
            raise ValueError(f'顶部文案目标{top_count}条与配音后分配不兼容；当前结构可显示'
                             f'{len(mandatory)}～{len(eligible)}条。请调整顶部使用比例或配音后比例。')
        optional = list(eligible - mandatory)
        rng.shuffle(optional)
        top_enabled = mandatory | set(optional[:top_count-len(mandatory)])
        if timing in ('after_voice', 'full'):
            post_top = top_enabled & set(narrated)
        for index, entry in enumerate(schedule):
            entry['top_text_allocated'] = index in top_enabled
            entry['post_voice_mode'] = '顶部文案+BGM' if index in post_top else '纯画面+BGM'
            entry['top_text_timing'] = timing
            entry['planned_video_duration'] = entry['planned_video_frames'] / cfg['fps']
        return schedule
    except ValueError as exc:
        raise AssemblyError(str(exc)) from exc


def _apply_item_timing_v1(item: dict, cfg: dict) -> None:
    """Resolve the frozen decisions against the actual selected video length."""
    if item.get('duration_policy_version') != LEGACY_REVISION:
        return
    fps = cfg['fps']
    total = item['total_frames']
    narrated = bool(item.get('narration_enabled'))
    voice = item.get('voice') or {}
    effective = voice.get('effective_voice_duration') if narrated else None
    end_frame = min(total, math.ceil(float(effective) * fps - 1e-7)) if effective is not None else None
    item['effective_voice_duration'] = effective
    item['final_video_duration'] = item['duration']
    item['narration_start'] = 0.0 if narrated else None
    item['narration_end'] = end_frame / fps if end_frame is not None else None
    item['post_voice_start'] = end_frame / fps if end_frame is not None and end_frame < total else None
    item['post_voice_end'] = item['duration'] if item['post_voice_start'] is not None else None
    windows = []
    top = item.get('top_text') or {}
    allocated = bool(item.get('top_text_allocated')) and bool(top.get('text'))
    timing = item.get('top_text_timing', 'auto')
    mode = item['video_mode']
    intro = float(cfg.get('intro_top_text_duration', 2.5))
    post_on = False
    if allocated:
        if timing == 'full':
            windows.append((0, total))
        elif timing == 'start' or (timing == 'auto' and mode != '配音+字幕+BGM'):
            intro_frames = min(total, max(1, round(intro * fps)))
            windows.append((0, intro_frames))
        elif timing == 'after_voice' and not narrated:
            intro_frames = min(total, max(1, round(intro * fps)))
            windows.append((0, intro_frames))
        if (timing in ('auto', 'after_voice') and narrated and end_frame is not None
                and end_frame < total and item.get('post_voice_mode') == '顶部文案+BGM'):
            windows.append((end_frame, total))
            post_on = True
    # Full-time is an explicit override. The post-voice label records that the
    # text is visible there, even though there is one continuous window.
    if allocated and timing == 'full' and narrated and end_frame is not None and end_frame < total:
        post_on = True
    item['intro_top_text_duration'] = (min(total, round(intro * fps)) / fps
                                       if windows and windows[0][0] == 0 and timing != 'full' else 0.0)
    item['post_voice_top_text_enabled'] = post_on
    item['top_text_enabled'] = bool(windows)
    top['enabled'] = bool(windows)
    top['windows'] = [{'start_frame':start, 'end_frame':end} for start, end in windows]
    item['top_text'] = top


def _validate_frozen_item_v1(item: dict, cfg: dict) -> None:
    from core import AssemblyError
    from voice_pipeline import MODES as VOICE_MODES

    if item.get('duration_policy_version') != LEGACY_REVISION:
        return
    mode = item.get('video_mode')
    if mode not in MODES or (bool(item.get('narration_enabled')),
                             bool(item.get('subtitle_enabled')),
                             bool(item.get('bgm_enabled', True))) != VOICE_MODES[mode]:
        raise AssemblyError('已冻结视频模式与实际音轨/字幕开关不一致。')
    if item.get('planned_video_frames') != round(item.get('planned_video_duration', -1)*cfg['fps']):
        raise AssemblyError('计划视频时长快照不一致。')
    if abs(item.get('final_video_duration', -1)-item['duration']) > 1e-8:
        raise AssemblyError('最终视频时长快照不一致。')
    if item.get('narration_enabled') and item.get('voice'):
        if item['voice']['effective_voice_duration'] > item['duration'] + 1/cfg['fps']:
            raise AssemblyError('最终画面不能容纳完整配音。')
        if item.get('subtitle_enabled') and any(
                card['end_frame'] > math.ceil(item['voice']['effective_voice_duration']*cfg['fps']-1e-7)
                for card in item.get('subtitles', [])):
            raise AssemblyError('底部口播字幕延伸到配音区间以外。')
    if 'top_text' not in item:
        return
    expected = copy.deepcopy(item)
    _apply_item_timing_v1(expected, cfg)
    fields = ('effective_voice_duration', 'final_video_duration', 'narration_start',
              'narration_end', 'post_voice_start', 'post_voice_end',
              'intro_top_text_duration', 'post_voice_top_text_enabled', 'top_text_enabled')
    if any(item.get(key) != expected.get(key) for key in fields) or item['top_text'] != expected['top_text']:
        raise AssemblyError('顶部文案时间窗或配音后阶段与已冻结方案不一致。')


def _build_schedule_v2(cfg: dict, natural_duration_frames: list[int] | dict | None = None) -> list[dict]:
    """Freeze the four mode ratios and one top-text allocation for new plans."""
    if cfg.get('video_policy_version') == LEGACY_REVISION:
        return _build_schedule_v1(cfg, natural_duration_frames)
    from core import AssemblyError
    try:
        counts = mode_counts(cfg)
        count = int(cfg['count'])
        rng = random.Random(str(cfg['seed']) + ':mode-top-duration-v2')
        modes = [mode for mode in MODES for _ in range(counts[mode])]
        rng.shuffle(modes)
        low = math.ceil(float(cfg['preferred_min']) * cfg['fps'] - 1e-7)
        high = math.floor(float(cfg['preferred_max']) * cfg['fps'] + 1e-7)
        if cfg.get('music_mode') == 'natural' and natural_duration_frames is not None:
            pool = (natural_duration_frames.get('all', []) if isinstance(natural_duration_frames, dict)
                    else natural_duration_frames)
            all_choices = sorted(set(int(value) for value in pool))
            if not all_choices:
                raise ValueError('没有满足当前时长范围的已确认音乐自然区间。')
            all_choices = [value for value in all_choices if low <= value <= high] or all_choices
        else:
            all_choices = None
        schedule = []
        for mode in modes:
            choices = all_choices
            if (cfg.get('bgm_rule_version') and mode in NARRATED and
                    isinstance(natural_duration_frames, dict)):
                choices = sorted(set(int(value) for value in natural_duration_frames.get('instrumental', [])))
                choices = [value for value in choices if low <= value <= high] or choices
                if not choices:
                    raise ValueError('NO_SUITABLE_INSTRUMENTAL_BGM：当前视频包含配音，但纯音乐库中没有符合条件的BGM。')
            frames = rng.choice(choices) if choices else rng.randint(low, high)
            schedule.append({'video_mode':mode, 'planned_video_frames':frames,
                             'planned_video_duration':frames/cfg['fps']})
        policy = cfg['top_text_use_policy']
        if policy == 'none':
            top_count = 0
        elif policy == 'all':
            top_count = count
        else:
            percent = _percent(cfg['top_text_use_percent'])
            top_count = apportion(count, [percent, 100-percent], ['顶部文案', '无顶部文案'])[0]
        positions = list(range(count))
        rng.shuffle(positions)
        enabled = set(positions[:top_count])
        timing = cfg['top_text_timing']
        for index, entry in enumerate(schedule):
            entry['top_text_enabled'] = index in enabled
            # No narration exists in the two unvoiced modes.
            entry['top_text_timing'] = ('auto' if timing == 'after_voice' and
                                        entry['video_mode'] not in NARRATED else timing)
        return schedule
    except ValueError as exc:
        raise AssemblyError(str(exc)) from exc


def _apply_item_timing_v2(item: dict, cfg: dict) -> None:
    if item.get('duration_policy_version') == LEGACY_REVISION:
        _apply_item_timing_v1(item, cfg)
        return
    if item.get('duration_policy_version') != PREVIOUS_REVISION:
        return
    fps = cfg['fps']
    total = item['total_frames']
    narrated = bool(item.get('narration_enabled'))
    voice = item.get('voice') or {}
    effective = voice.get('effective_voice_duration') if narrated else None
    end_frame = min(total, math.ceil(float(effective)*fps-1e-7)) if effective is not None else None
    item['effective_voice_duration'] = effective
    item['final_video_duration'] = item['duration']
    item['narration_start'] = 0.0 if narrated else None
    item['narration_end'] = end_frame/fps if end_frame is not None else None
    # Keep descriptive phase fields for the current render manifest reader.
    item['post_voice_start'] = end_frame/fps if end_frame is not None and end_frame < total else None
    item['post_voice_end'] = item['duration'] if item['post_voice_start'] is not None else None
    timing = item.get('top_text_timing', 'auto')
    if timing == 'after_voice' and not narrated:
        timing = 'auto'
        item['top_text_timing'] = timing
    enabled = bool(item.get('top_text_enabled'))
    top = item.get('top_text') or {}
    windows = []
    intro = float(cfg.get('intro_top_text_duration', 2.5))
    if enabled:
        if timing == 'full':
            windows = [(0, total)]
        elif timing == 'start' or (timing == 'auto' and item['video_mode'] != '配音+字幕+BGM'):
            auto_seconds = 2.0 if timing == 'auto' and item['video_mode'] == '字幕+BGM' else intro
            windows = [(0, min(total, max(1, round(auto_seconds*fps))))]
        elif end_frame is not None and end_frame < total:
            windows = [(end_frame, total)]
    item['intro_top_text_duration'] = ((windows[0][1]-windows[0][0])/fps
                                       if windows and windows[0][0] == 0 and timing != 'full' else 0.0)
    top['enabled'] = enabled
    top['windows'] = [{'start_frame':start, 'end_frame':end} for start,end in windows]
    item['top_text'] = top


def _validate_frozen_item_v2(item: dict, cfg: dict) -> None:
    if item.get('duration_policy_version') == LEGACY_REVISION:
        _validate_frozen_item_v1(item, cfg)
        return
    if item.get('duration_policy_version') != PREVIOUS_REVISION:
        return
    from core import AssemblyError
    from voice_pipeline import MODES as VOICE_MODES
    mode = item.get('video_mode')
    if mode not in MODES or (bool(item.get('narration_enabled')),
                             bool(item.get('subtitle_enabled')),
                             bool(item.get('bgm_enabled', True))) != VOICE_MODES[mode]:
        raise AssemblyError('已冻结视频模式与实际音轨/字幕开关不一致。')
    if item.get('planned_video_frames') != round(item.get('planned_video_duration', -1)*cfg['fps']):
        raise AssemblyError('计划视频时长快照不一致。')
    if abs(item.get('final_video_duration', -1)-item['duration']) > 1e-8:
        raise AssemblyError('最终视频时长快照不一致。')
    if item.get('narration_enabled') and item.get('voice'):
        if item['voice']['effective_voice_duration'] > item['duration'] + 1/cfg['fps']:
            raise AssemblyError('最终画面不能容纳完整配音。')
        if item.get('subtitle_enabled') and any(
                card['end_frame'] > math.ceil(item['voice']['effective_voice_duration']*cfg['fps']-1e-7)
                for card in item.get('subtitles', [])):
            raise AssemblyError('底部口播字幕延伸到配音区间以外。')
    if 'top_text' not in item:
        return
    if bool(item['top_text_enabled']) != bool(item['top_text'].get('text')):
        raise AssemblyError('顶部文案启用状态与文案内容不一致。')
    expected = copy.deepcopy(item)
    _apply_item_timing_v2(expected, cfg)
    fields = ('effective_voice_duration', 'final_video_duration', 'narration_start',
              'narration_end', 'post_voice_start', 'post_voice_end',
              'intro_top_text_duration', 'top_text_enabled')
    if any(item.get(key) != expected.get(key) for key in fields) or item['top_text'] != expected['top_text']:
        raise AssemblyError('顶部文案时间窗与已冻结方案不一致。')


MIN_POST_VOICE_TOP_SECONDS = 2.0


def build_schedule(cfg: dict, natural_duration_frames: list[int] | dict | None = None) -> list[dict]:
    if cfg.get('video_policy_version') == LEGACY_REVISION:
        return _build_schedule_v1(cfg, natural_duration_frames)
    schedule = _build_schedule_v2(cfg, natural_duration_frames)
    if cfg.get('video_policy_version') == REVISION:
        for entry in schedule:
            entry['top_text_policy'] = cfg['top_text_timing']
            entry['top_text_timing'] = cfg['top_text_timing']
    return schedule


def intro_duration_used(cfg: dict) -> bool:
    """Only validate/use the intro control when a selected mode can consume it."""
    policy = cfg.get('top_text_use_policy')
    if policy == 'none' or (policy == 'ratio' and float(cfg.get('top_text_use_percent', 0)) == 0):
        return False
    counts = mode_counts(cfg)
    timing = cfg.get('top_text_timing', 'auto')
    return (timing == 'start' or
            (timing == 'auto' and bool(counts[MODES[1]] + counts[MODES[2]])) or
            (timing == 'after_voice' and bool(counts[MODES[1]])))


def _retime_bottom_subtitles(item: dict, start_frame: int, end_frame: int, fps: int) -> None:
    cards = item.get('subtitles') or []
    if not cards:
        return
    if cards[0].get('timestamp_source')=='whisper_known_text_dtw_v1':
        if min(c['start_frame'] for c in cards)<start_frame or max(c['end_frame'] for c in cards)>end_frame:
            from core import AssemblyError
            raise AssemblyError('智能字幕真实语音时间与顶部文案时间窗冲突，请选择 Auto/配音后或关闭顶部文案；不会压缩或延后口播字幕。')
        return
    baseline = item.setdefault('bottom_subtitle_base_frames',
                               [[card['start_frame'], card['end_frame']] for card in cards])
    if len(baseline) != len(cards):
        from core import AssemblyError
        raise AssemblyError('字幕卡数量与冻结的原时间轴不一致。')
    original_end = max(end for _, end in baseline)
    if original_end <= 0 or end_frame-start_frame < len(cards):
        from core import AssemblyError
        raise AssemblyError('底部字幕可用时间不足，无法按卡片顺序显示。')
    last = start_frame
    for index, (card, (original_start, original_stop)) in enumerate(zip(cards, baseline)):
        remaining = len(cards)-index-1
        mapped_start = start_frame + round(original_start*(end_frame-start_frame)/original_end)
        mapped_stop = start_frame + round(original_stop*(end_frame-start_frame)/original_end)
        mapped_start = min(end_frame-remaining-1, max(last, mapped_start))
        mapped_stop = min(end_frame-remaining, max(mapped_start+1, mapped_stop))
        if index == len(cards)-1:
            mapped_stop = end_frame
        card['start_frame'], card['end_frame'] = mapped_start, mapped_stop
        card['startMs'] = round(mapped_start*1000/fps)
        card['endMs'] = round(mapped_stop*1000/fps)
        if card.get('revision')=='smart_caption_v1':
            from subtitle_settings import budget
            card['animation']=budget(item['subtitle_profile']['style']['animation'],mapped_stop-mapped_start,fps)
        last = mapped_stop


def apply_item_timing(item: dict, cfg: dict) -> None:
    from visual_layout import freeze as freeze_visual_layout
    freeze_visual_layout(item,cfg)
    from product_catalog import REVISION as SPEC_REVISION
    if cfg.get('product_spec_revision')==SPEC_REVISION:
        item['product_spec_snapshot']={k:cfg[k] for k in ('product_spec_revision','product_spec_id','product_spec_name','editing_profile_id','editing_profile_name')}
    if item.get('voice_match_revision'):
        from voice_duration_matching import apply
        apply(item,cfg)
        return
    revision = item.get('duration_policy_version')
    if revision == LEGACY_REVISION:
        _apply_item_timing_v1(item, cfg)
        return
    if revision == PREVIOUS_REVISION:
        _apply_item_timing_v2(item, cfg)
        return
    if revision != REVISION:
        return
    fps, total = cfg['fps'], item['total_frames']
    narrated = bool(item.get('narration_enabled'))
    effective = (item.get('voice') or {}).get('effective_voice_duration') if narrated else None
    end_frame = min(total, math.ceil(float(effective)*fps-1e-7)) if effective is not None else None
    item['effective_voice_duration'] = effective
    item['final_video_duration'] = item['duration']
    item['narration_start'] = 0.0 if narrated else None
    item['narration_end'] = end_frame/fps if end_frame is not None else None
    item['post_voice_start'] = end_frame/fps if end_frame is not None and end_frame < total else None
    item['post_voice_end'] = item['duration'] if item['post_voice_start'] is not None else None
    policy = item.get('top_text_policy', item.get('top_text_timing', 'auto'))
    item['top_text_policy'] = policy
    item['top_text_timing'] = policy
    effective_policy = 'auto' if policy == 'after_voice' and not narrated else policy
    from voice_duration_matching import post_subtitle_tail_frames, TEXT_TIMING_REVISION
    sequential_top=bool(post_subtitle_tail_frames(cfg,item))
    if sequential_top:
        effective_policy='after_voice'
        item['text_layer_timing_revision']=TEXT_TIMING_REVISION
    enabled = bool(item.get('top_text_enabled'))
    mode = item['video_mode']
    resolved = 'none'
    windows = []
    if enabled:
        if effective_policy == 'full' or (effective_policy == 'auto' and mode == MODES[0]):
            resolved, windows = 'full', [(0, total)]
        elif effective_policy == 'start' or (effective_policy == 'auto' and mode in (MODES[1], MODES[2])):
            intro = max(1, round(float(cfg.get('intro_top_text_duration', 2.5))*fps))
            intro_frames = min(total, min(intro, round(2*fps)) if effective_policy == 'auto' and mode == MODES[1] else intro)
            resolved, windows = 'intro', [(0, intro_frames)]
        elif end_frame is not None and end_frame < total:
            resolved, windows = 'post_narration', [(end_frame, total)]
    item['resolved_top_text_timing'] = resolved
    item['top_text_start'] = windows[0][0]/fps if windows else None
    item['top_text_end'] = windows[0][1]/fps if windows else None
    item['intro_top_text_duration'] = ((windows[0][1]-windows[0][0])/fps
                                       if resolved == 'intro' else 0.0)
    top = item.get('top_text') or {}
    top['enabled'] = enabled
    top['windows'] = [{'start_frame':start, 'end_frame':stop} for start, stop in windows]
    item['top_text'] = top
    # Keep the existing card text and page order; adjust only the display frames.
    subtitle_end = end_frame if narrated and end_frame is not None else total
    subtitle_start = (windows[0][1] if windows and mode in (MODES[1], MODES[3])
                      and resolved == 'intro' else 0)
    if item.get('subtitle_enabled') and item.get('subtitles') and not sequential_top:
        _retime_bottom_subtitles(item, subtitle_start, subtitle_end, fps)
    cards = item.get('subtitles') or []
    item['bottom_subtitle_enabled'] = bool(item.get('subtitle_enabled'))
    item['bottom_subtitle_start'] = min((card['start_frame'] for card in cards), default=None)
    item['bottom_subtitle_end'] = max((card['end_frame'] for card in cards), default=None)


def validate_text_layers(item: dict, cfg: dict) -> None:
    if item.get('voice_match_revision'):
        from voice_duration_matching import validate_overlay
        validate_overlay(item,cfg)
        return
    from core import AssemblyError
    windows = (item.get('top_text') or {}).get('windows') or []
    cards = item.get('subtitles') or []
    for window in windows:
        start, end = window['start_frame'], window['end_frame']
        if not 0 <= start < end <= item['total_frames']:
            raise AssemblyError('TEXT_LAYER_OVERLAP：顶部文案时间窗无效。')
        if any(start < card['end_frame'] and card['start_frame'] < end for card in cards):
            raise AssemblyError('TEXT_LAYER_OVERLAP：顶部文案与底部字幕不能同时显示。')
    if item.get('duration_policy_version') == REVISION and 'top_text' in item:
        mode = item['video_mode']
        enabled = bool(item.get('top_text_enabled'))
        policy = item.get('top_text_policy')
        resolved = item.get('resolved_top_text_timing')
        from voice_duration_matching import post_subtitle_tail_frames
        if post_subtitle_tail_frames(cfg,item) and resolved != 'post_narration':
            raise AssemblyError('顶部文案必须在配音字幕结束后显示。')
        if enabled and policy == 'auto' and mode == MODES[2] and resolved != 'intro':
            raise AssemblyError('顶部文案时机错误：配音+BGM的Auto必须在开头显示。')
        if enabled and policy == 'auto' and mode == MODES[3] and resolved != 'post_narration':
            raise AssemblyError('顶部文案时机错误：配音+底部字幕+BGM的Auto必须在配音后显示。')
        if enabled and resolved == 'post_narration' and (
                not windows or windows[0]['end_frame']-windows[0]['start_frame'] <
                math.ceil(MIN_POST_VOICE_TOP_SECONDS*cfg['fps']-1e-7)):
            raise AssemblyError('POST_VOICE_TOP_TOO_SHORT：配音后顶部文案必须至少显示2秒，请重新规划。')


def validate_frozen_item(item: dict, cfg: dict) -> None:
    if item.get('voice_match_revision'):
        from voice_duration_matching import validate
        validate(item,cfg)
        validate_text_layers(item,cfg)
        return
    revision = item.get('duration_policy_version')
    if revision == LEGACY_REVISION:
        _validate_frozen_item_v1(item, cfg)
        return
    if revision == PREVIOUS_REVISION:
        _validate_frozen_item_v2(item, cfg)
        return
    if revision != REVISION:
        return
    from core import AssemblyError
    from voice_pipeline import MODES as VOICE_MODES
    mode = item.get('video_mode')
    if mode not in MODES or (bool(item.get('narration_enabled')),
                             bool(item.get('subtitle_enabled')),
                             bool(item.get('bgm_enabled', True))) != VOICE_MODES[mode]:
        raise AssemblyError('已冻结视频模式与实际音轨/字幕开关不一致。')
    if item.get('planned_video_frames') != round(item.get('planned_video_duration', -1)*cfg['fps']):
        raise AssemblyError('计划视频时长快照不一致。')
    if abs(item.get('final_video_duration', -1)-item['duration']) > 1e-8:
        raise AssemblyError('最终视频时长快照不一致。')
    if item.get('narration_enabled') and item.get('voice'):
        if item['voice']['effective_voice_duration'] > item['duration'] + 1/cfg['fps']:
            raise AssemblyError('最终画面不能容纳完整配音。')
        if item.get('subtitle_enabled') and any(
                card['end_frame'] > math.ceil(item['voice']['effective_voice_duration']*cfg['fps']-1e-7)
                for card in item.get('subtitles', [])):
            raise AssemblyError('底部字幕延伸到配音区间以外。')
    if 'top_text' not in item:
        return
    if bool(item.get('top_text_enabled')) != bool((item.get('top_text') or {}).get('text')):
        raise AssemblyError('顶部文案启用状态与文案内容不一致。')
    expected = copy.deepcopy(item)
    apply_item_timing(expected, cfg)
    keys = ('effective_voice_duration', 'final_video_duration', 'narration_start',
            'narration_end', 'post_voice_start', 'post_voice_end', 'top_text_policy',
            'resolved_top_text_timing', 'top_text_start', 'top_text_end',
            'intro_top_text_duration', 'bottom_subtitle_enabled',
            'bottom_subtitle_start', 'bottom_subtitle_end', 'text_layer_timing_revision')
    if any(item.get(key) != expected.get(key) for key in keys) or (
            item.get('top_text') != expected.get('top_text') or
            item.get('subtitles') != expected.get('subtitles')):
        raise AssemblyError('文字时间轴与已冻结方案不一致。')
    validate_text_layers(item, cfg)
