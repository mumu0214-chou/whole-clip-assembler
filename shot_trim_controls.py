"""Opt-in shared shot removal controls. Old manifests retain old settings/RNG."""
import math

REVISION = 'shot_trim_controls_v2'
DEFAULTS = dict(shot_trim_revision=REVISION, shot_trim_amount_mode='choices',
                shot_trim_range_min=.25, shot_trim_range_max=2.,
                shot_trim_voice_enabled=True, processing_min_kept_enabled=True)


def active(cfg):
    return cfg.get('shot_trim_revision') == REVISION


def minimum(cfg):
    if active(cfg) and not cfg.get('processing_min_kept_enabled', True):
        return 0.
    return float(cfg.get('processing_min_kept_seconds', .8))


def settings(cfg):
    if not active(cfg):
        return None
    from timeline_processing import micro_trim_values
    return dict(revision=REVISION, mode=cfg.get('shot_trim_amount_mode', 'choices'),
                choices=micro_trim_values(cfg),
                range_min=float(cfg.get('shot_trim_range_min', .25)),
                range_max=float(cfg.get('shot_trim_range_max', 2.)),
                voice_enabled=bool(cfg.get('shot_trim_voice_enabled', True)),
                minimum_enabled=bool(cfg.get('processing_min_kept_enabled', True)),
                side=cfg.get('processing_micro_trim_mode', 'random_side'))


def validate(cfg):
    if not active(cfg):
        return
    s = settings(cfg)
    if s['mode'] not in ('choices', 'range'):
        raise ValueError('裁剪镜头请选择候选秒数或随机区间。')
    if not all(math.isfinite(v) for v in (s['range_min'], s['range_max'])) or not 0 < s['range_min'] <= s['range_max'] <= 5:
        raise ValueError('随机裁剪区间须大于0、不超过5秒，最小值不能大于最大值。')
    for key in ('shot_trim_voice_enabled', 'processing_min_kept_enabled'):
        if not isinstance(cfg.get(key, True), bool):
            raise ValueError('裁剪镜头和最短镜头开关应为勾选值。')


def sample(cfg, rng):
    from timeline_processing import micro_trim_values
    if active(cfg) and cfg.get('shot_trim_amount_mode') == 'range':
        return round(rng.uniform(float(cfg.get('shot_trim_range_min', .25)),
                                 float(cfg.get('shot_trim_range_max', 2.))), 6)
    return rng.choice(micro_trim_values(cfg))


def maximum(cfg):
    from timeline_processing import micro_trim_values
    return (float(cfg.get('shot_trim_range_max', 2.))
            if cfg.get('shot_trim_amount_mode') == 'range' else max(micro_trim_values(cfg)))
