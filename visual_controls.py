"""One explicit visual policy for newly collected plans; legacy plans stay frozen."""
from copy import deepcopy

REVISION = 'unified_visual_controls_v1'
TRANSITIONS = {'直接切换': 'none', '自然淡化': 'natural', '柔和淡暗': 'gentle', '自定义转场': 'custom'}
EFFECTS = {'原画（关闭画面特效）': 'original', '轻推近': 'gentle_push', '清亮': 'fresh',
           '质感': 'texture', '自定义画面特效': 'custom'}


def active(cfg):
    return cfg.get('visual_controls_revision') == REVISION


def validate(cfg):
    revision = cfg.get('visual_controls_revision')
    if revision is None:
        return
    if revision != REVISION:
        raise ValueError('转场与画面特效设置版本无效')
    if cfg.get('transition_mode') not in TRANSITIONS.values():
        raise ValueError('请选择有效的转场方式')
    if cfg.get('picture_effect_mode') not in EFFECTS.values():
        raise ValueError('请选择有效的画面特效')


def migrate_for_editor(cfg):
    """Called only by editable profile loading, never by manifest rendering."""
    result = deepcopy(cfg)
    if 'visual_controls_revision' in result:
        validate(result)
        return result
    from ball_pacing import active as ball_active
    if ball_active(result):
        transition = result.get('ball_transition_preset', 'natural')
        effect = result.get('ball_visual_effect', 'original')
    else:
        enabled = result.get('processing_enabled', False)
        transition = 'custom' if enabled and result.get('processing_transition') else 'none'
        effect = 'custom' if enabled and any(result.get('processing_'+k) for k in ('fx','color','zoom')) else 'original'
    result.update(visual_controls_revision=REVISION, transition_mode=transition, picture_effect_mode=effect)
    return result


def snapshot(cfg):
    validate(cfg)
    return {key: cfg[key] for key in ('visual_controls_revision','transition_mode','picture_effect_mode')}


def label(mapping, value):
    return next(key for key, code in mapping.items() if code == value)


def summary(cfg):
    return '转场：'+label(TRANSITIONS,cfg['transition_mode'])+'；画面特效：'+label(EFFECTS,cfg['picture_effect_mode'])
