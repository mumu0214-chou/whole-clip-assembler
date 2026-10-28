"""New beef-ball plans: readable opening and bounded short-shot runs.

The original history, safety-cut and voice-frame checks remain authoritative.
"""
import math

REVISION = 'ball_pacing_v1'
TRANSITIONS = ('natural', 'gentle', 'none')
VISUAL_EFFECTS = ('original', 'gentle_push', 'fresh', 'texture')


def active(cfg):
    from editing_profiles import ball_profile, grouped
    return ball_profile(cfg) and grouped(cfg) and cfg.get('ball_pacing_revision') == REVISION


def minimum_frames(cfg, position, available=None):
    fps = cfg['fps']
    seconds = 2.0 if position == 0 else 1.8 if position < 3 else 1.5
    floor = math.ceil(seconds * fps - 1e-7)
    # An originally shorter clip is legal later in the sequence, but cannot
    # be further shortened simply to balance a narration frame count.
    return floor if position < 3 or available is None else min(available, floor)


def validate_settings(cfg):
    if not active(cfg):
        return
    if cfg.get('ball_transition_preset', 'natural') not in TRANSITIONS:
        raise ValueError('牛肉丸转场方式无效')
    if cfg.get('ball_visual_effect', 'original') not in VISUAL_EFFECTS:
        raise ValueError('牛肉丸画面特效无效')


def validate_timeline(clips, cfg):
    if not active(cfg):
        return
    fps = cfg['fps']
    if len(clips)>=6:
        total=clips[-1]['timeline_start_frame']+clips[-1]['duration_frames']
        if total<len(clips)*math.ceil(1.9*fps-1e-7):
            raise ValueError('BALL_PACING_AVERAGE_SHORT：整条平均每镜不足1.9秒，请减少镜头数')
    for i, clip in enumerate(clips):
        frames = clip['duration_frames']
        if i < 3:
            stable = frames - clip['incoming_transition']['frames'] - clip['outgoing_transition']['frames']
            if stable < minimum_frames(cfg, i):
                raise ValueError(f'BALL_PACING_OPENING_SHORT：第{i+1}镜清晰展示不足')
        elif i and frames < math.ceil(1.8*fps-1e-7) and clips[i-1]['duration_frames'] < math.ceil(1.8*fps-1e-7):
            raise ValueError('BALL_PACING_SHORT_RUN：不能连续使用短镜头')
        if i and clip['incoming_transition']['type'] != 'none':
            left = clips[i-1]
            if min(left['duration_frames'], frames) < math.ceil(1.8*fps-1e-7):
                raise ValueError('BALL_PACING_TRANSITION_SHORT：短镜头之间不加转场')
