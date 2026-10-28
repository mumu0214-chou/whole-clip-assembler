"""Small versioned profiles over the existing settings document; no second runtime."""
from mac_paths import path as _portable_path
from copy import deepcopy

REVISION = 'editing_profiles_v2'
LEGACY_REVISION = 'editing_profiles_v1'
FRESH = 'fresh_beef_standard'
BALL = 'beef_ball_standard'
SHANTOU_BALL = 'shantou_beef_ball_standard'
BALL_PROFILES = frozenset((BALL, SHANTOU_BALL))
NAMES = {FRESH: '鲜切牛肉带货', BALL: '49.9十包牛肉丸', SHANTOU_BALL: '汕头牛肉丸'}
SCRIPT_NAMES = {FRESH: '牛肉文案', BALL: '49.9十包牛肉丸文案', SHANTOU_BALL: '汕头牛肉丸文案'}
SCRIPT_BALANCE_REVISION = 'script_balance_rotation_v2'
GROUPED_FORMAT = 'grouped_combo_independent'
PACKAGE_MATCHING_REVISION = 'package_matching_removed_v1'
BALL_MUSIC_ROOT = _portable_path(r'D:\DY video\音乐库\牛肉丸')
BALL_MUSIC_REVISION = 'beef_ball_dedicated_bgm_v1'
GLOBAL_KEYS = frozenset(('ffmpeg', 'ffprobe', 'node', 'browser', 'remotion_project',
                         'width', 'height', 'fps', 'crf', 'concurrency', 'diagnostic_settings',
                         'visual_layout_revision','visual_layout','voice_speed_revision','voice_speed_enabled',
                         'voice_speed_factor','voice_speed_selected','voice_pitch_semitones','review_first_enabled',
                         'edit_style','edit_style_audio','edit_style_push'))
TRANSIENT = frozenset(('editing_profiles', 'video_mode_schedule', 'voice_job_temp_dir',
                       'task_snapshot', 'voice_force_regenerate','grouped_coverage_snapshot','clip_sfx_bindings_snapshot',
                       'ball_scene_bindings_snapshot','editorial_snapshot','voice_referenced_job_dirs'))


def grouped(cfg):
    return cfg.get('material_format') in (GROUPED_FORMAT, 'sk_diverse')


def ball_profile(cfg):
    return (cfg.get('editing_profile_id') if isinstance(cfg, dict) else cfg) in BALL_PROFILES


def new_plan_config(cfg):
    """Retire package matching for new G plans, retaining saved legacy fields."""
    result = deepcopy(cfg)
    from duration_tail_policy import REVISION as DURATION_TAIL_REVISION
    from video_plan_policy import REVISION as VIDEO_POLICY_REVISION
    if result.get('video_policy_version') == VIDEO_POLICY_REVISION:
        result.setdefault('duration_tail_revision', DURATION_TAIL_REVISION)
    from sk_materials import active as sk_active
    if sk_active(result):
        result.update(material_format='sk_diverse', selection_rule_version='sk_diverse_v1')
    if ball_profile(result) and grouped(result):
        result['ball_scene_selection_revision'] = 'ball_scene_diversity_v1'
    if ball_profile(result) and not result.get('ball_music_revision'):
        result.update(music_root=BALL_MUSIC_ROOT, selected_music='',
                      ball_music_revision=BALL_MUSIC_REVISION)
    if grouped(result):
        from voice_duration_matching import TEXT_TIMING_REVISION
        result.update(package_count_matching_enabled=False,
                      package_matching_revision=PACKAGE_MATCHING_REVISION,
                      text_layer_timing_revision=TEXT_TIMING_REVISION)
    else:
        result.pop('text_layer_timing_revision',None)
    return result


def dedicated_ball_music(cfg):
    return (ball_profile(cfg) and
            cfg.get('ball_music_revision') == BALL_MUSIC_REVISION)


def clean(cfg):
    return {k: deepcopy(v) for k, v in cfg.items() if k not in TRANSIENT}


def initialize(cfg):
    """In-memory migration. Old settings and all old manifests retain their values."""
    current = deepcopy(cfg)
    saved = current.get('editing_profiles')
    if saved:
        keys = set(saved.get('items', {}))
        legacy = saved.get('revision') == LEGACY_REVISION and keys == {FRESH, BALL}
        if not legacy and (saved.get('revision') != REVISION or keys != set(NAMES)):
            raise ValueError('剪辑方案配置版本或方案列表无效，原文件未覆盖。')
        state = deepcopy(saved)
    else:
        fresh = clean(current)
        fresh.update(editing_profile_id=FRESH, editing_profile_name=NAMES[FRESH],
                     material_format='five_stage', match_video_to_voice=False,
                     package_count_matching_enabled=False)
        ball = deepcopy(fresh)
        ball.update(editing_profile_id=BALL, editing_profile_name=NAMES[BALL],
                    material_format=GROUPED_FORMAT,
                    material_root=_portable_path(r'D:\DY video\人工处理素材\牛肉丸'),
                    output_root=_portable_path(r'D:\DY video\牛肉丸组合成片'),
                    match_video_to_voice=True, coverage_dedup_enabled=True,
                    package_count_matching_enabled=False, package_count_clips=[],package_anchor_margin_seconds=.2,
                    script_library_profile=BALL, opening_c_min_per_batch=0,
                    processing_ab_duration_enabled=False, processing_trim=False,
                    processing_speed_enabled=False, top_text_timing='full',
                    top_text_library='', top_text_use_policy='none', top_text_enabled=False,
                    attempts_per_video=max(int(fresh.get('attempts_per_video',160)),3000),
                    sequence_strategy='random', special_first_shot_enabled=False)
        fresh.setdefault('script_library_profile', FRESH)
        state = {'revision': REVISION, 'active': FRESH, 'items': {FRESH: fresh, BALL: ball}}
    # Add a product identity, not a second planner. Never copy scripts, voices,
    # usage/reservations, price drafts or frozen task state into the new product.
    if SHANTOU_BALL not in state['items']:
        shantou = clean(state['items'][BALL])
        shantou.update(editing_profile_id=SHANTOU_BALL, editing_profile_name=NAMES[SHANTOU_BALL],
                       script_library_profile=SHANTOU_BALL, script_draft_text='', seed='',
                       output_root=_portable_path(r'D:\DY video\汕头牛肉丸组合成片'),
                       external_voice_selected_names=None)
        for key in ('external_voice_version_overrides', 'script_overrides'):
            shantou.pop(key, None)
        state['items'][SHANTOU_BALL] = shantou
    state['revision'] = REVISION
    for key in BALL_PROFILES:
        item = new_plan_config(state['items'][key])
        if item.get('script_balance_revision') != SCRIPT_BALANCE_REVISION:
            item.update(script_library_strategy='低使用次数优先', script_balance_revision=SCRIPT_BALANCE_REVISION)
        item.update(editing_profile_id=key, editing_profile_name=NAMES[key], script_library_profile=key)
        state['items'][key] = item
    active = state.get('active', FRESH)
    return load(state, active, current), state


def save(state, cfg):
    result = deepcopy(state)
    key = cfg.get('editing_profile_id', FRESH)
    if key not in NAMES:
        raise ValueError('未知剪辑方案')
    result['items'][key] = clean(new_plan_config(cfg))
    result['active'] = key
    return result


def load(state, key, technical):
    if key not in NAMES:
        raise ValueError('未知剪辑方案')
    result = new_plan_config(state['items'][key])
    result.setdefault('product_spec_id','default')
    from timeline_processing import TIMELINE_DEFAULTS
    for name,value in TIMELINE_DEFAULTS.items():
        if name.startswith('processing_micro_trim_'):result.setdefault(name,deepcopy(value))
    if grouped(result):result.setdefault('package_anchor_margin_seconds',.2)
    if key == FRESH:
        # The user moved the existing A/B/C/D/E pool into the 牛肉 child
        # folder. Resolve only an absent old pool; never merge the G pool or
        # alter saved manifests. Persist only through the existing Save action.
        from pathlib import Path
        root=Path(result.get('material_root',''))
        names=list(result.get('group_folders',dict(A='A',B='B',C='C',D='D',E='E')).values())
        nested=root/'牛肉'
        if names and not any((root/name).is_dir() for name in names) and all((nested/name).is_dir() for name in names):
            result['material_root']=str(nested)
    from sk_materials import active as sk_active
    canvas_isolated = sk_active(result) or sk_active(technical)
    result.update({k: deepcopy(technical[k]) for k in GLOBAL_KEYS if k in technical
                   and not (canvas_isolated and k in ('width', 'height'))})
    result.update(editing_profile_id=key, editing_profile_name=NAMES[key])
    return result


def document(state, cfg):
    result = clean(new_plan_config(cfg))
    result['editing_profiles'] = save(state, cfg)
    return result
