"""Human opening exclusions are independent of activation and crop permission."""
from editing_profiles import ball_profile

REVISION='ball_opening_v1'


def allowed(clip,cfg):
    if cfg.get('material_format') == 'sk_diverse':
        from sk_materials import opening_allowed
        return opening_allowed(clip,cfg)
    return not (ball_profile(cfg) and clip.get('opening_excluded'))


def validate(clips,cfg):
    if clips and not allowed(clips[0],cfg):
        raise ValueError('BALL_OPENING_EXCLUDED：首镜被人工标记为禁止作开头，请重新组合')


def order_candidate(clips,cfg):
    if clips and not allowed(clips[0],cfg):
        index=next((i for i,c in enumerate(clips) if allowed(c,cfg)),None)
        if index is None:
            raise ValueError('BALL_OPENING_UNAVAILABLE：候选片段均禁止作开头，请恢复至少一个可用首镜')
        return [clips[index]]+clips[:index]+clips[index+1:]
    return clips
