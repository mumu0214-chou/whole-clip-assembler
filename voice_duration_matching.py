"""Opt-in shared strict voice duration and explicit, auditable safe window fitting."""
from copy import deepcopy
import math

REVISION = 'strict_voice_frames_v1'
TEXT_TIMING_REVISION = 'subtitle_then_top_v1'


def post_subtitle_tail_frames(cfg, entry, package=None):
    """New grouped plans reserve real footage after the complete spoken captions."""
    package = entry if package is None else package
    from duration_tail_policy import active
    if active(cfg) and entry.get('top_text_enabled') and package.get('narration_enabled'):
        return max(1, math.ceil(2 * cfg['fps'] - 1e-7))
    if (cfg.get('text_layer_timing_revision') == TEXT_TIMING_REVISION
            and entry.get('top_text_enabled')
            and package.get('narration_enabled') and package.get('subtitle_enabled')):
        return max(1, math.ceil(2 * cfg['fps'] - 1e-7))
    return 0


def target_frames(voice, fps):
    from core import AssemblyError
    duration=float(voice.get('effective_voice_duration',0))
    if not math.isfinite(duration) or duration<=0:
        raise AssemblyError('VOICE_DURATION_INVALID：缺少实际配音时长')
    return math.ceil(duration*fps-1e-7)


def resolve_schedule(cfg,packages):
    from duration_tail_policy import active, narrated_target
    if (not cfg.get('match_video_to_voice') and not active(cfg)) or packages is None:
        return cfg
    from video_plan_policy import build_schedule
    cfg=deepcopy(cfg)
    schedule=deepcopy(cfg.get('video_mode_schedule') or build_schedule(cfg))
    for entry,package in zip(schedule,packages):
        if package and package.get('narration_enabled') and package.get('voice'):
            # Invalid short/long voices fail per item inside the planner, not
            # here: one bad recording must not abort every other batch item.
            n=(narrated_target(cfg,entry,package,validate=False) if active(cfg) else
               target_frames(package['voice'],cfg['fps']) + post_subtitle_tail_frames(cfg,entry,package))
            entry.update(planned_video_frames=n,planned_video_duration=n/cfg['fps'],
                         strict_voice_frames=n)
    cfg['video_mode_schedule']=schedule
    return cfg


def fit_windows(seq,cfg,seed,blocks,*,edge_fit=False):
    """Use bake_timeline; trim only reviewed safe windows, never loop/freeze/stretch."""
    from timeline_processing import bake_timeline
    from core import AssemblyError
    from editing_profiles import grouped
    if edge_fit:
        from voice_edge_fitting import fit
        return fit(seq,cfg,seed,blocks)
    seq=deepcopy(seq)
    for name,target in blocks:
        if target<1:
            raise AssemblyError('VOICE_TARGET_UNSATISFIABLE：时间槽不足一帧')
        for _ in range(len(seq)*4+8):
            out,transitions,total=bake_timeline(seq,cfg,seed)
            indices=[i for i,c in enumerate(seq) if c.get('planning_block','all')==name]
            if not indices:
                raise AssemblyError('CANDIDATES_INSUFFICIENT：时间槽没有素材')
            actual=sum(out[i]['duration_frames'] for i in indices)
            actual-=sum(t['frames'] for i,t in enumerate(transitions)
                        if i in indices and i+1 in indices)
            excess=actual-target
            if excess==0:break
            if excess<0:
                raise AssemblyError('VIDEO_TARGET_UNSATISFIABLE：有效素材不足，不补帧、不循环')
            minimum={i:min(out[i]['duration_frames'],max(1,math.ceil(float(cfg.get('processing_min_kept_seconds',.8))*cfg['fps']-1e-7))) for i in indices}
            from duration_tail_policy import active as common_duration
            usable=[i for i in reversed(indices)
                    if (not grouped(cfg) or seq[i].get('safe_cut_confirmed'))
                    and not (common_duration(cfg) and i == len(seq)-1
                             and cfg.get('processing_micro_trim_protect_last',True))
                    and out[i]['duration_frames']>minimum[i]]
            if not usable:
                if grouped(cfg) and not any(seq[i].get('safe_cut_confirmed') for i in indices):
                    raise AssemblyError('SAFE_CUT_REQUIRED：没有已确认可安全裁切的画面满足精确时长')
                raise AssemblyError('VIDEO_TARGET_UNSATISFIABLE：已达到现有最短保留时长，无法继续安全裁切')
            i=usable[0];clip=out[i]
            frames=clip['duration_frames']-min(excess,clip['duration_frames']-minimum[i])
            # Keep the original computed window as a validator baseline.
            base=seq[i].get('planned_source_window')
            seq[i]['planned_source_window']=dict(
                base_in=base['base_in'] if base else clip['source_in_seconds'],
                base_out=base['base_out'] if base else clip['source_out_seconds'],
                start=clip['source_in_seconds'],
                end=round(clip['source_in_seconds']+frames*clip['playback_rate']/cfg['fps'],9),
                frames=frames,proof_hash=seq[i]['hash'],
                reason='已明确启用配音/锁定时间槽匹配；仅安全画面裁尾')
        else:
            raise AssemblyError('VIDEO_TARGET_UNSATISFIABLE：转场量化与安全裁切未收敛')
    out,transitions,total=bake_timeline(seq,cfg,seed)
    return seq,out,transitions,total


def apply(item,cfg):
    fps=cfg['fps'];n=target_frames(item['voice'],fps)
    tail=post_subtitle_tail_frames(cfg,item)
    total=n+tail
    from duration_tail_policy import active, narrated_target, REVISION as TAIL_REVISION
    common=active(cfg)
    if common:
        total=narrated_target(cfg,item,item)
        tail=total-n
        item['duration_tail_revision']=TAIL_REVISION
    item.update(voice_match_revision=REVISION,voice_target_frames=n,
                planned_video_frames=total,planned_video_duration=total/fps,
                effective_voice_duration=item['voice']['effective_voice_duration'],
                final_video_duration=item['duration'],narration_start=0.,
                narration_end=item['voice']['effective_voice_duration'],
                post_voice_start=None,post_voice_end=None,
                top_text_policy=item.get('top_text_policy',cfg.get('top_text_timing','auto')))
    top=item.get('top_text') or {'enabled':False,'text':''}
    enabled=bool(item.get('top_text_enabled') and top.get('text'))
    policy=item['top_text_policy']
    if not enabled:windows=[];resolved='none'
    elif tail:
        windows=[(n,total)];resolved='post_narration'
    elif policy=='full':windows=[(0,n)];resolved='full'
    elif policy=='start':
        windows=[(0,min(n,max(1,round(cfg.get('intro_top_text_duration',2)*fps))))];resolved='intro'
    else:
        windows=[(max(0,n-round(2*fps)),n)];resolved='before_voice_end'
    top.update(enabled=enabled,windows=[dict(start_frame=a,end_frame=b) for a,b in windows])
    item.update(top_text=top,top_text_enabled=enabled,top_text_timing=policy,
                resolved_top_text_timing=resolved,top_text_start=windows[0][0]/fps if windows else None,
                top_text_end=windows[0][1]/fps if windows else None,
                intro_top_text_duration=(windows[0][1]-windows[0][0])/fps if resolved=='intro' else 0.,
                bottom_subtitle_enabled=bool(item.get('subtitle_enabled')),
                bottom_subtitle_start=min((c['start_frame'] for c in item.get('subtitles',[])),default=None),
                bottom_subtitle_end=max((c['end_frame'] for c in item.get('subtitles',[])),default=None))
    item['voice_match_snapshot']=dict(revision=REVISION,frames=n,
        effective_voice_seconds=item['voice']['effective_voice_duration'],
        wav_seconds=item['voice'].get('voice_duration'),fps=fps,
        frame_quantization_seconds=n/fps-item['voice']['effective_voice_duration'],
        tail_hold_applied=False,top_resolved=resolved)
    if tail:
        item.update(text_layer_timing_revision=TEXT_TIMING_REVISION,
                    post_voice_start=n/fps,post_voice_end=total/fps)
        item['voice_match_snapshot'].update(text_layer_timing_revision=TEXT_TIMING_REVISION,
            video_target_frames=total,post_subtitle_frames=tail)


def validate(item,cfg):
    from core import AssemblyError
    from voice_pipeline import MODES
    mode=item.get('video_mode')
    if mode not in MODES or (bool(item.get('narration_enabled')),bool(item.get('subtitle_enabled')),
                            bool(item.get('bgm_enabled',True)))!=MODES[mode] or not item.get('narration_enabled'):
        raise AssemblyError('VOICE_MATCH_MODE_INVALID：严格配音快照音轨/字幕模式不一致')
    if item.get('source_audio')!='discard':
        raise AssemblyError('原素材音频必须排除')
    n=target_frames(item['voice'],cfg['fps'])
    total=n+post_subtitle_tail_frames(cfg,item)
    from duration_tail_policy import active, narrated_target, validate_item
    validate_item(item,cfg)
    if active(cfg):
        total=narrated_target(cfg,item,item)
    if item.get('total_frames')!=total or abs(item['duration']-total/cfg['fps'])>1e-8:
        raise AssemblyError('VOICE_FRAME_MISMATCH：视频帧数不等于实际配音目标')
    if item.get('voice_target_frames')!=n or item.get('voice_match_revision')!=REVISION:
        raise AssemblyError('VOICE_MATCH_SNAPSHOT_CHANGED：配音目标快照不一致')
    if any(not 0<=c['start_frame']<c['end_frame']<=n for c in item.get('subtitles',[])):
        raise AssemblyError('VOICE_SUBTITLE_BOUNDS：字幕超出完整配音画面')
    expected=deepcopy(item);apply(expected,cfg)
    for key in ('voice_match_snapshot','planned_video_frames','planned_video_duration',
                'final_video_duration','narration_start','narration_end','post_voice_start',
                'post_voice_end','top_text','resolved_top_text_timing','top_text_start',
                'top_text_end','bottom_subtitle_start','bottom_subtitle_end','bottom_subtitle_enabled',
                'effective_voice_duration','top_text_enabled','intro_top_text_duration'):
        if item.get(key)!=expected.get(key):
            raise AssemblyError('VOICE_MATCH_SNAPSHOT_CHANGED：'+key)
    if item.get('text_layer_timing_revision')!=expected.get('text_layer_timing_revision'):
        raise AssemblyError('VOICE_MATCH_SNAPSHOT_CHANGED：text_layer_timing_revision')


def validate_overlay(item,cfg):
    """Opt-in simultaneous top/bottom requires distinct spatial regions."""
    from core import AssemblyError
    windows=(item.get('top_text') or {}).get('windows',[])
    for w in windows:
        if not 0<=w['start_frame']<w['end_frame']<=item['total_frames']:
            raise AssemblyError('TEXT_LAYER_OVERLAP：顶部文案时间窗无效')
    cards=item.get('subtitles') or []
    if not windows or not cards:return
    from duration_tail_policy import active
    if active(cfg) or cfg.get('text_layer_timing_revision') == TEXT_TIMING_REVISION:
        for w in windows:
            if any(w['start_frame'] < c['end_frame'] and c['start_frame'] < w['end_frame'] for c in cards):
                raise AssemblyError('TEXT_LAYER_OVERLAP：顶部文案必须在配音字幕结束后显示，请重新规划')
        return
    style=(item.get('subtitle_profile') or {}).get('style',{})
    height=cfg['height'];size=float(style.get('font_size',58))
    max_lines=max(len(c.get('lines') or [c['text']]) for c in cards)
    bottom_top=float(style.get('y',.72))*height-max_lines*size*float(style.get('line_spacing',1.22))/2-32
    text=item['top_text']['text'];font=float(cfg.get('top_text_font_size',60))
    # Conservative layout bound; does not alter fonts, subtitle timing or rendering.
    width=cfg['width']*float(cfg.get('top_text_max_width',.78))
    lines=sum(max(1,math.ceil(len(line)*font/max(1,width))) for line in text.split('\n'))
    top_end=float(cfg.get('top_text_top',250))+lines*font*1.18+16
    if top_end>=bottom_top:
        raise AssemblyError('TEXT_LAYER_OVERLAP：严格跟随允许上下叠加，但当前位置可能相撞，请调整位置或关闭顶文')
