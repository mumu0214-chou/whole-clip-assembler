# -*- coding: utf-8 -*-
"""Explicit per-clip timing and overlapping transitions; no original-media writes.

Profiles are sampled once during planning, then saved and checked on resume. The
renderer receives exact frame schedules, not a random configuration to reroll.
"""
from __future__ import annotations
import copy
import math
import random
import time
import uuid
from collections import Counter
from datetime import datetime
from pathlib import Path

TIMELINE_REVISION = 'timing_v1_trim_speed_transitions'
AB_DURATION_REVISION = 'ab_playback_window_v1'
MICRO_TRIM_REVISION = 'random_edge_trim_v1'
MICRO_TRIM_FIELDS = ('micro_trim_selected','micro_trim_applied','micro_trim_side',
                     'micro_trim_requested_seconds','micro_trim_removed_seconds',
                     'micro_trim_protection_reason')
TRANSITION_TYPES = ('fade', 'slide_left', 'slide_right', 'wipe_left', 'wipe_right', 'blur_fade', 'fade_black')
SCOPES = ('all', 'random_count', 'random_probability', 'positions')
TIMELINE_DEFAULTS = {
    'processing_speed_values': 'fixed',
    'processing_speed_min': .95, 'processing_speed_max': 1.05,
    'processing_trim': False, 'processing_trim_mode': 'head',
    'processing_trim_values': 'fixed', 'processing_trim_head': .5, 'processing_trim_tail': .5,
    'processing_trim_head_min': .3, 'processing_trim_head_max': .5,
    'processing_trim_tail_min': .3, 'processing_trim_tail_max': .5,
    'processing_trim_protect_first': True, 'processing_trim_protect_last': True,
    'processing_min_kept_seconds': .8,
    'processing_transition': False, 'processing_transition_type': 'fade',
    'processing_transition_seconds': .25,
    'processing_transition_values': 'fixed',
    'processing_transition_min': .15, 'processing_transition_max': .3,
    'processing_fx': False, 'processing_fx_type': 'vignette',
    'processing_fx_strength': .15,
    'processing_ab_duration_enabled': False,
    'processing_ab_duration_values': '3,4,5',
    'processing_ab_duration_scope': 'all',
    'processing_ab_duration_random_min': 1,
    'processing_ab_duration_random_max': 2,
    'processing_ab_duration_probability': .35,
    'processing_ab_duration_positions': '',
    'processing_ab_duration_mode': 'front',
    'processing_ab_duration_protect_first': True,
    'processing_micro_trim_enabled': False,
    'processing_micro_trim_values': '0.25,0.5,1',
    'processing_micro_trim_mode': 'random_side',
    'processing_micro_trim_scope': 'random_count',
    'processing_micro_trim_random_min': 1,
    'processing_micro_trim_random_max': 1,
    'processing_micro_trim_probability': .35,
    'processing_micro_trim_positions': '',
    'processing_micro_trim_protect_first': True,
    'processing_micro_trim_protect_last': True,
}
for _name in ('speed', 'trim', 'transition', 'fx'):
    _p = 'processing_' + _name
    TIMELINE_DEFAULTS.update({
        _p+'_scope': 'all' if _name == 'speed' else 'random_count',
        _p+'_random_min': 1, _p+'_random_max': 2,
        _p+'_probability': .35, _p+'_positions': '',
    })


def _val(cfg, key):
    return cfg.get(key, TIMELINE_DEFAULTS[key])


def validate_timeline_settings(cfg):
    from visual_controls import validate
    validate(cfg)
    from ball_pacing import validate_settings as validate_ball_pacing
    validate_ball_pacing(cfg)
    from shot_trim_controls import validate as validate_shot_trim
    validate_shot_trim(cfg)
    from video_processing import parse_positions
    for name in ('speed', 'trim', 'transition', 'fx', 'micro_trim'):
        p = 'processing_' + name
        if _val(cfg, p+'_scope') not in SCOPES:
            raise ValueError(f'{p}作用范围无效。')
        mn, mx = float(_val(cfg, p+'_random_min')), float(_val(cfg, p+'_random_max'))
        if not all(math.isfinite(x) and x.is_integer() for x in (mn,mx)) or not 0 <= mn <= mx <= 100:
            raise ValueError(f'{p}随机片段/切点数量须为0～100的整数范围。')
        prob = float(_val(cfg, p+'_probability'))
        if not math.isfinite(prob) or not 0 <= prob <= 1:
            raise ValueError('随机比例须在0～1。')
        parse_positions(_val(cfg, p+'_positions'))
    for key in ('processing_micro_trim_enabled','processing_micro_trim_protect_first',
                'processing_micro_trim_protect_last'):
        if not isinstance(_val(cfg,key),bool):raise ValueError('随机微裁开关应为勾选值。')
    if _val(cfg,'processing_micro_trim_mode') not in ('head','tail','random_side'):
        raise ValueError('随机微裁部位无效。')
    micro_trim_values(cfg)
    if not isinstance(_val(cfg, 'processing_ab_duration_enabled'), bool) or not isinstance(_val(cfg, 'processing_ab_duration_protect_first'), bool):
        raise ValueError('A/B随机时长开关应为勾选值。')
    if _val(cfg, 'processing_ab_duration_enabled'):
        if _val(cfg, 'processing_ab_duration_scope') not in SCOPES:
            raise ValueError('A/B随机时长作用范围无效。')
        parse_positions(_val(cfg, 'processing_ab_duration_positions'))
        mn = int(_val(cfg, 'processing_ab_duration_random_min'))
        mx = int(_val(cfg, 'processing_ab_duration_random_max'))
        if not 0 <= mn <= mx <= 100:
            raise ValueError('A/B随机数量范围须为0～100。')
        probability = float(_val(cfg, 'processing_ab_duration_probability'))
        if not math.isfinite(probability) or not 0 <= probability <= 1:
            raise ValueError('A/B随机比例须在0～1。')
        if _val(cfg, 'processing_ab_duration_mode') not in ('front', 'back', 'center', 'random'):
            raise ValueError('A/B取段方式无效。')
        duration_values(cfg)
    for k in ('processing_trim','processing_transition','processing_fx',
              'processing_trim_protect_first','processing_trim_protect_last'):
        if not isinstance(_val(cfg,k), bool): raise ValueError(f'{k}应为勾选值。')
    if _val(cfg,'processing_trim_mode') not in ('head','tail','both','random_side'):
        raise ValueError('修剪部位无效。')
    for p in ('speed','trim','transition'):
        if _val(cfg, f'processing_{p}_values') not in ('fixed','random'):
            raise ValueError('数值方式只能为固定或随机范围。')
    for k, low, high in [
        ('processing_speed_min',.5,2),('processing_speed_max',.5,2),
        ('processing_trim_head',0,5),('processing_trim_tail',0,5),
        ('processing_trim_head_min',0,5),('processing_trim_head_max',0,5),
        ('processing_trim_tail_min',0,5),('processing_trim_tail_max',0,5),
        ('processing_min_kept_seconds',.1,5),('processing_transition_seconds',.03,2),
        ('processing_transition_min',.03,2),('processing_transition_max',.03,2),
        ('processing_fx_strength',0,.6)]:
        v=float(_val(cfg,k))
        if not math.isfinite(v) or not low<=v<=high: raise ValueError(f'{k}须在{low}～{high}。')
    for p in ('speed','trim_head','trim_tail','transition'):
        if float(_val(cfg,'processing_'+p+'_min'))>float(_val(cfg,'processing_'+p+'_max')):
            raise ValueError(f'{p}最小值不能大于最大值。')
    if _val(cfg,'processing_transition_type') not in TRANSITION_TYPES + ('random',):
        raise ValueError('转场类型无效。')
    if _val(cfg,'processing_fx_type') not in ('vignette','soft_light','random'):
        raise ValueError('片段特效类型无效。')


def timeline_settings(cfg):
    result = {k: copy.deepcopy(cfg.get(k,v)) for k,v in TIMELINE_DEFAULTS.items()}
    from shot_trim_controls import settings as shot_settings
    if shot_settings(cfg) is not None: result['shot_trim'] = shot_settings(cfg)
    return result


def compatible_settings(saved,current):
    """V1.0.3/4 plans keep the exact saved controls; no new transforms retrofitted."""
    if not isinstance(saved,dict) or not isinstance(current,dict):return False
    if current.get('timeline',{}).get('processing_micro_trim_enabled',False) and not saved.get('timeline',{}).get('processing_micro_trim_enabled',False):
        return False
    if saved.get('revision') == current.get('revision'):
        current=copy.deepcopy(current)
        # Missing new controls mean disabled on old frozen plans. Enabling the
        # feature must still require a new plan; never retrofit a random cut.
        if not current.get('timeline',{}).get('processing_micro_trim_enabled',False):
            for key in TIMELINE_DEFAULTS:
                if key.startswith('processing_micro_trim_') and key not in saved.get('timeline',{}):
                    current.get('timeline',{}).pop(key,None)
        return saved == current
    if saved.get('revision') not in ('export_processing_v1','export_processing_v2_clip_scope'):
        return False
    return all(k == 'revision' or current.get(k) == v for k,v in saved.items())


def needs_advanced_planner(cfg):
    from visual_controls import active
    if active(cfg) and (cfg['transition_mode'] != 'none' or cfg['picture_effect_mode'] == 'custom'):
        return True
    if not cfg.get('processing_enabled',False): return False
    return (any(cfg.get(k,False) for k in ('processing_trim','processing_transition','processing_fx','processing_ab_duration_enabled','processing_micro_trim_enabled'))
            or cfg.get('processing_speed_enabled',False))


def timing_warnings(cfg):
    if not cfg.get('processing_enabled'): return []
    result=[]
    if cfg.get('processing_trim'):
        result.append('头尾修剪不是抽帧：秒数按原片时间计。仅修剪新输出，原文件不变；短片段或首尾保护触发时会跳过并记录，不能自动保证动作完整。')
    if cfg.get('processing_speed_enabled'):
        result.append('变速可指定或随机选片段，先重算各段时长再选音乐；BGM自身不变速。')
    if cfg.get('processing_transition'):
        result.append('转场位置按“第N段之后”编号，不是片段编号；重叠转场会缩短总时间，并叠加相邻画面，不恢复已裁掉的内容。')
    if cfg.get('processing_fx'):
        result.append('特效只影响视频层；顶部文字不跟随变色、镜像或转场。')
    if cfg.get('processing_ab_duration_enabled'):
        result.append('A/B随机时长是按长度取段，不是AI动作完整性判断；随机起点可能截断动作，原文件不修改。')
    if cfg.get('processing_micro_trim_enabled'):
        result.append('随机微裁从已选片段的片头或片尾减去候选秒数，在3/4/5秒取段之后执行；按原片秒数计。首尾保护、最少保留时长与G素材安全裁切权限优先。')
    return result


def duration_values(cfg):
    raw = str(_val(cfg, 'processing_ab_duration_values')).replace('，', ',')
    try:
        values = [float(v.strip()) for v in raw.split(',')]
    except ValueError as exc:
        raise ValueError('A/B可选时长请填写正数，以逗号分隔。') from exc
    if not values or len(values) > 30 or any(not math.isfinite(v) or v < 1/int(cfg['fps']) or v > 120 for v in values):
        raise ValueError('A/B可选时长须为至少1帧且不超过120秒的正数，最多30个。')
    return list(dict.fromkeys(values))


def micro_trim_values(cfg):
    raw=str(_val(cfg,'processing_micro_trim_values')).replace('，',',')
    if not raw.strip() and cfg.get('shot_trim_revision')=='shot_trim_controls_v2' and cfg.get('shot_trim_amount_mode')=='range':
        return [.25,.5,1.,1.5,2.]
    try:values=[float(v.strip()) for v in raw.split(',')]
    except ValueError as exc:raise ValueError('随机微裁秒数请用逗号分隔，例如0.25,0.5,1。') from exc
    if not values or len(values)>30 or any(not math.isfinite(v) or not 0<v<=5 for v in values):
        raise ValueError('随机微裁秒数须大于0且不超过5秒，最多30个。')
    return list(dict.fromkeys(values))


def freeze_micro_trim(item,cfg):
    if item.get('voice_fit_revision'):return
    if cfg.get('processing_enabled') and cfg.get('processing_micro_trim_enabled'):
        item.update(micro_trim_revision=MICRO_TRIM_REVISION,micro_trim_seed=item['profile_seed'],
                    micro_trim_parameters={k:cfg.get(k,v) for k,v in TIMELINE_DEFAULTS.items()
                                           if k.startswith('processing_micro_trim_')})


def _ab_selected(eligible, cfg, rng):
    from video_processing import parse_positions
    if not cfg.get('processing_enabled') or not _val(cfg, 'processing_ab_duration_enabled'):
        return set()
    scope = _val(cfg, 'processing_ab_duration_scope')
    if scope == 'all': return set(eligible)
    if scope == 'positions':
        return {p-1 for p in parse_positions(_val(cfg, 'processing_ab_duration_positions')) if p-1 in eligible}
    if scope == 'random_probability':
        return {i for i in sorted(eligible) if rng.random() < float(_val(cfg, 'processing_ab_duration_probability'))}
    lo = min(len(eligible), int(_val(cfg, 'processing_ab_duration_random_min')))
    hi = min(len(eligible), int(_val(cfg, 'processing_ab_duration_random_max')))
    return set(rng.sample(sorted(eligible), rng.randint(lo, hi)))


def _selected(n,cfg,name,rng):
    from video_processing import parse_positions
    enabled = cfg.get('processing_enabled') and cfg.get('processing_'+name+'_enabled' if name in ('speed','micro_trim') else 'processing_'+name)
    from visual_controls import active
    if active(cfg) and name in ('transition','fx'):
        mode = cfg['transition_mode'] if name == 'transition' else cfg['picture_effect_mode']
        enabled = mode == 'custom' and cfg.get('processing_'+name, False)
    if not enabled: return set()
    p='processing_'+name; scope=_val(cfg,p+'_scope')
    if scope=='all': return set(range(n))
    if scope=='positions': return {x-1 for x in parse_positions(_val(cfg,p+'_positions')) if 1<=x<=n}
    if scope=='random_probability': return {i for i in range(n) if rng.random()<float(_val(cfg,p+'_probability'))}
    lo=min(n,int(_val(cfg,p+'_random_min')));hi=min(n,int(_val(cfg,p+'_random_max')))
    return set(rng.sample(range(n),rng.randint(lo,hi)))


def build_slot_profiles(n,cfg,seed,eligible_positions=None,*,voice_fitting=False):
    """Independent position profiles exist before choosing files: no timing reroll."""
    rng=random.Random(str(seed)); profiles=[{'speed':1.,'head':0.,'tail':0.,'fx':'none','strength':0.} for _ in range(n)]
    eligible = set(range(n)) if eligible_positions is None else set(eligible_positions)
    if voice_fitting:
        for profile in profiles:profile['voice_fitting']=True
    chosen = set() if voice_fitting else _ab_selected(eligible, cfg, rng)
    for i in sorted(chosen):
        values = duration_values(cfg)
        rng.shuffle(values)
        profiles[i].update(ab_duration=True, ab_duration_choices=values,
                           ab_window_fraction=rng.random())
    for i in sorted(_selected(n,cfg,'speed',rng)):
        value=(rng.uniform(float(_val(cfg,'processing_speed_min')),float(_val(cfg,'processing_speed_max')))
               if _val(cfg,'processing_speed_values')=='random' else float(cfg.get('processing_speed',1)))
        profiles[i]['speed']=round(value,6)
    for i in sorted(set() if voice_fitting else _selected(n,cfg,'trim',rng)):
        mode=_val(cfg,'processing_trim_mode')
        if mode=='random_side':mode=rng.choice(('head','tail'))
        for side in ('head','tail'):
            if mode not in (side,'both'):continue
            if side=='head' and i==0 and _val(cfg,'processing_trim_protect_first'):continue
            if side=='tail' and i==n-1 and _val(cfg,'processing_trim_protect_last'):continue
            value=(rng.uniform(float(_val(cfg,'processing_trim_'+side+'_min')),float(_val(cfg,'processing_trim_'+side+'_max')))
                   if _val(cfg,'processing_trim_values')=='random' else float(_val(cfg,'processing_trim_'+side)))
            profiles[i][side]=round(value,6)
    if cfg.get('processing_enabled') and _val(cfg, 'processing_ab_duration_enabled') and _val(cfg, 'processing_ab_duration_protect_first'):
        profiles[0]['head']=0.
    for i in sorted(_selected(n,cfg,'fx',rng)):
        kind=_val(cfg,'processing_fx_type')
        if kind=='random':kind=rng.choice(('vignette','soft_light'))
        profiles[i].update(fx=kind,strength=float(_val(cfg,'processing_fx_strength')))
    transitions=[{'type':'none','requested_frames':0} for _ in range(max(0,n-1))]
    for i in sorted(_selected(n-1,cfg,'transition',rng)):
        kind=_val(cfg,'processing_transition_type')
        if kind=='random':kind=rng.choice(TRANSITION_TYPES[:-1])  # frozen historical random pool
        seconds=(rng.uniform(float(_val(cfg,'processing_transition_min')),float(_val(cfg,'processing_transition_max')))
                 if _val(cfg,'processing_transition_values')=='random' else float(_val(cfg,'processing_transition_seconds')))
        transitions[i]={'type':kind,'requested_frames':max(1,round(seconds*cfg['fps']))}
    from ball_pacing import active as ball_pacing_active
    from visual_controls import active as unified_visuals
    unified = unified_visuals(cfg)
    if unified and cfg['transition_mode'] != 'custom':
        transitions=[{'type':'none','requested_frames':0} for _ in range(max(0,n-1))]
        if cfg['transition_mode'] != 'none':
            for i in range(2,n-1,3):
                transitions[i]={'type':'fade_black' if cfg['transition_mode']=='gentle' else 'fade',
                                'requested_frames':max(1,round(cfg['fps']*.16))}
    if ball_pacing_active(cfg):
        preset=cfg['transition_mode'] if unified else cfg.get('ball_transition_preset','natural')
        if not unified and preset!='none' and not any(t['type']!='none' for t in transitions):
            for i in range(2,n-1,3):
                transitions[i]={'type':'fade','requested_frames':max(1,round(cfg['fps']*.16))}
        # Do not spend the opening on transitions. Limit later transitions to
        # separated long-shot boundaries, regardless of the old random draw.
        kept=[]
        for i, request in enumerate(transitions):
            if preset=='none' or i<2 or i-2 in kept or i-1 in kept:
                transitions[i]={'type':'none','requested_frames':0};continue
            if request['type']=='none':continue
            if preset != 'custom':
                request['type']='fade_black' if preset=='gentle' else 'fade'
            request['requested_frames']=min(request['requested_frames'],
                                            max(1,round(cfg['fps']*.20)))
            kept.append(i)
    # A separate seeded stream leaves all existing random decisions unchanged.
    micro_rng=random.Random(str(seed)+':'+MICRO_TRIM_REVISION)
    for i in sorted(set() if voice_fitting else _selected(n,cfg,'micro_trim',micro_rng)):
        side=_val(cfg,'processing_micro_trim_mode')
        if side=='random_side':side=micro_rng.choice(('head','tail'))
        protection=''
        if side=='head' and i==0 and (_val(cfg,'processing_micro_trim_protect_first') or
                (_val(cfg,'processing_ab_duration_enabled') and _val(cfg,'processing_ab_duration_protect_first'))):
            protection='首镜片头保护'
        if side=='tail' and i==n-1 and _val(cfg,'processing_micro_trim_protect_last'):
            protection='最后片尾保护'
        from shot_trim_controls import sample
        profiles[i].update(micro_trim_side=side,micro_trim_seconds=sample(cfg,micro_rng),
                           micro_trim_protection_reason=protection)
    return profiles,transitions


def timing_for_media(media,profile,cfg):
    from core import clip_frames
    d=float(media['duration']);head=float(profile['head']);tail=float(profile['tail']);rate=float(profile['speed']);notes=[]
    from shot_trim_controls import minimum as shot_minimum
    minimum=shot_minimum(cfg)
    unsafe_grouped=(cfg.get('material_format') in ('grouped_combo_independent','sk_diverse')
                    and not media.get('safe_cut_confirmed'))
    if unsafe_grouped and (head or tail):
        notes.append('完整动作保护：未确认安全裁切，跳过头尾修剪。');head=tail=0.
    # Do not invisibly reduce the requested trim amount. Skip and explain instead.
    if (head or tail) and (d-head-tail<=0 or (d-head-tail)/rate<minimum):
        notes.append('跳过头尾修剪：处理后短于最少保留时长。');head=tail=0.
    kept=d-head-tail
    if rate!=1 and kept/rate<minimum:
        notes.append('跳过变速：处理后短于最少保留时长。');rate=1.
    start,end=head,d-tail
    applied=bool(profile.get('ab_duration') and (media.get('group') in ('A','B')
                 or cfg.get('material_format') in ('grouped_combo_independent','sk_diverse')))
    if unsafe_grouped and applied:
        applied=False;notes.append('完整动作保护：未确认安全裁切，跳过随机3/4/5秒截取。')
    target=None;mode='none';protection=''
    if cfg.get('processing_enabled') and _val(cfg,'processing_ab_duration_enabled') and profile.get('first_slot') and _val(cfg,'processing_ab_duration_protect_first'):
        if head: notes.append('首镜保护优先：跳过去片头。')
        start=0.;head=0.;kept=end-start
        protection='首镜从原片0秒开始；跳过去片头。'
    if applied:
        fps=cfg['fps']
        available_frames=math.floor((end-start)/rate*fps+1e-8)
        choices=profile['ab_duration_choices']
        target=next((v for v in choices if math.floor(v*fps+1e-8) <= available_frames),None)
        frames=math.floor(target*fps+1e-8) if target is not None else max(1,available_frames)
        if target is None: notes.append('可用原片不足所有候选时长，保留可用范围，不补时。')
        source_span=min(end-start,frames*rate/fps)
        mode=_val(cfg,'processing_ab_duration_mode')
        fraction={'front':0.,'back':1.,'center':.5,'random':profile['ab_window_fraction']}[mode]
        if profile.get('first_slot') and _val(cfg,'processing_ab_duration_protect_first'):
            fraction=0.;protection='首镜从原片0秒开始；跳过去片头并覆盖取段起点。'
        start=start+(end-start-source_span)*fraction
        end=start+source_span
        if mode=='random': notes.append('随机起点可能截断动作；未修改原文件。')
    micro_active=bool(not profile.get('voice_fitting') and cfg.get('processing_enabled') and _val(cfg,'processing_micro_trim_enabled'))
    micro_selected=micro_active and bool(profile.get('micro_trim_side'))
    micro_side=profile.get('micro_trim_side','none') if micro_selected else 'none'
    micro_seconds=float(profile.get('micro_trim_seconds',0)) if micro_selected else 0.
    micro_reason=profile.get('micro_trim_protection_reason','') if micro_selected else ''
    micro_applied=False
    if micro_selected:
        if unsafe_grouped:micro_reason='完整动作保护：未确认安全裁切'
        if not micro_reason and math.floor((end-start-micro_seconds)/rate*cfg['fps']+1e-8)<max(1,math.ceil(minimum*cfg['fps']-1e-8)):
            micro_reason='处理后短于最少保留时长'
        if micro_reason:notes.append('跳过随机微裁：'+micro_reason+'。')
        else:
            if micro_side=='head':start+=micro_seconds
            else:end-=micro_seconds
            micro_applied=True
            notes.append(f'随机微裁：去{"片头" if micro_side=="head" else "片尾"}{micro_seconds:g}秒（原片时间）。')
    if micro_applied:
        frames=max(1,math.floor((end-start)/rate*cfg['fps']+1e-8))
    elif not applied:
        # New plans must never ask Remotion for a frame past the selected source window.
        # Legacy plans retain their original ceiling rule and manifest fingerprint.
        frames=(max(1, math.floor((end-start)/rate*cfg['fps']+1e-8))
                if cfg.get('processing_enabled') and _val(cfg,'processing_ab_duration_enabled')
                else clip_frames(kept/rate,cfg['fps']))
    # New strict/grouped plans never read the fractional frame beyond the file.
    if cfg.get('match_video_to_voice') or cfg.get('material_format') in ('grouped_combo_independent','sk_diverse'):
        frames=max(1,math.floor((end-start)/rate*cfg['fps']+1e-8))
    new_feature=bool(not profile.get('voice_fitting') and cfg.get('processing_enabled') and _val(cfg,'processing_ab_duration_enabled'))
    precise_window=new_feature or micro_applied
    result={'source_in_seconds':round(start,9) if precise_window else start,
            'source_out_seconds':round(end,9) if precise_window else end,'playback_rate':rate,
            'duration_frames':frames,'kept_source_seconds':round(end-start,9) if precise_window else kept,'edit_notes':notes,
            'trim_head_seconds':head,'trim_tail_seconds':tail}
    if new_feature:
        result.update(random_duration_applied=applied,target_playback_seconds=target,
                      actual_playback_seconds=frames/cfg['fps'],window_mode=mode,
                      protection_reason=protection)
    if micro_active:
        result.update(micro_trim_selected=micro_selected,micro_trim_applied=micro_applied,
                      micro_trim_side=micro_side,micro_trim_requested_seconds=micro_seconds,
                      micro_trim_removed_seconds=micro_seconds if micro_applied else 0.,
                      micro_trim_protection_reason=micro_reason)
    window=media.get('planned_source_window')
    if window:
        from core import AssemblyError
        grouped=cfg.get('material_format') in ('grouped_combo_independent','sk_diverse')
        from duration_tail_policy import active as common_duration
        if not (grouped or cfg.get('match_video_to_voice') or common_duration(cfg)) or (grouped and not media.get('safe_cut_confirmed')):
            raise AssemblyError('SAFE_CUT_UNCONFIRMED：未确认安全裁切')
        if (window.get('proof_hash')!=media['hash'] or
            abs(window['base_in']-result['source_in_seconds'])>1e-7 or
            abs(window['base_out']-result['source_out_seconds'])>1e-7 or
            not result['source_in_seconds']-1e-8<=window['start']<window['end']<=result['source_out_seconds']+1e-8 or
            window['frames']<min(result['duration_frames'],max(1,math.ceil(minimum*cfg['fps']-1e-7))) or
            abs((window['end']-window['start'])/rate*cfg['fps']-window['frames'])>1e-5):
            raise AssemblyError('SOURCE_WINDOW_INVALID：安全窗口/帧数/文件指纹不一致')
        result.update(source_in_seconds=window['start'],source_out_seconds=window['end'],
                      duration_frames=window['frames'],kept_source_seconds=window['end']-window['start'])
        if new_feature:result['actual_playback_seconds']=window['frames']/cfg['fps']
        result['edit_notes'].append(window['reason'])
    from editorial import validate_window
    validate_window(media,result,cfg)
    return result


def transition_frames(request,left,right):
    # Each end uses at most one third of its clip: adjacent overlaps cannot collide.
    if request['type'] in ('none','fade_black'):return 0
    return max(0,min(int(request['requested_frames']),int(left)//3,int(right)//3))


def bake_timeline(seq,cfg,seed):
    from video_processing import build_clip_effects
    from voice_edge_fitting import active
    profiles,requests=build_slot_profiles(len(seq),cfg,seed,{i for i,c in enumerate(seq)
        if c['group'] in ('A','B') or cfg.get('material_format') in ('grouped_combo_independent','sk_diverse')},voice_fitting=active(seq))
    for i in range(len(requests)):
        if seq[i].get('boundary_no_transition') or seq[i+1].get('boundary_no_transition'):
            requests[i]={'type':'none','requested_frames':0}
        if cfg.get('editorial_enabled') and any(c.get('picture_annotation',{}).get('action_core') for c in (seq[i],seq[i+1])):
            requests[i]={'type':'none','requested_frames':0}
    visual=build_clip_effects(seq,cfg,random.Random(str(seed)+':visual'))
    output=[]
    for i,c in enumerate(seq):
        profiles[i]['first_slot']=(i==0)
        timing=timing_for_media(c,profiles[i],cfg)
        eff=dict(visual[i],trim_head_seconds=timing['trim_head_seconds'],trim_tail_seconds=timing['trim_tail_seconds'],
                 fx_type=profiles[i]['fx'],fx_strength=profiles[i]['strength'])
        output.append(dict(c,**timing,source_audio='discard',effects=eff))
    transitions=[];cursor=0
    for i,c in enumerate(output):
        c['timeline_start_frame']=cursor
        inc=transitions[-1] if i else {'type':'none','frames':0}
        if i<len(output)-1:
            req=requests[i];frames=transition_frames(req,c['duration_frames'],output[i+1]['duration_frames'])
            effect_frames=(min(req['requested_frames'],c['duration_frames']//4,
                               output[i+1]['duration_frames']//4) if req['type']=='fade_black' else 0)
            if cfg.get('ball_pacing_revision')=='ball_pacing_v1':
                from ball_pacing import active as ball_pacing_active
                if ball_pacing_active(cfg) and (i<2 or min(c['duration_frames'],output[i+1]['duration_frames'])<round(1.8*cfg['fps'])):
                    frames=effect_frames=0
            out={'after':i+1,'type':req['type'] if frames or effect_frames else 'none','frames':frames,
                 'requested_frames':req['requested_frames'],
                 'start_frame':cursor+c['duration_frames']-frames}
            if effect_frames:out['effect_frames']=effect_frames
            transitions.append(out)
        else:out={'type':'none','frames':0}
        c['incoming_transition']=dict(inc)
        c['outgoing_transition']=dict(out)
        cursor+=c['duration_frames']-out['frames']
    return output,transitions,cursor


def timing_digest(item):
    from core import object_hash
    fields=[{k:c.get(k) for k in ('identity_key','hash','duration','source_in_seconds','source_out_seconds',
                               'playback_rate','duration_frames','timeline_start_frame','source_audio','effects',
                               'incoming_transition','outgoing_transition')} for c in item['clips']]
    if item.get('random_duration_revision'):
        for field,clip in zip(fields,item['clips']):
            field.update({k:clip.get(k) for k in ('random_duration_applied','target_playback_seconds','actual_playback_seconds','window_mode','protection_reason')})
    if item.get('micro_trim_revision'):
        for field,clip in zip(fields,item['clips']):field.update({k:clip.get(k) for k in MICRO_TRIM_FIELDS})
    if item.get('selection_rule_version') in ('five_stage_v1_20260930','five_stage_c_family_v2_20260930','five_stage_c_numeric_v3_20260930'):
        for field,clip in zip(fields,item['clips']):
            field.update({k:clip.get(k) for k in ('category','can_open','mutex_group','relative_path','timeline_role')})
            if item.get('selection_rule_version') in ('five_stage_c_family_v2_20260930','five_stage_c_numeric_v3_20260930'):
                field['member_number'] = clip.get('member_number')
    if item.get('grouped_revision') or item.get('voice_match_revision') or item.get('voice_fit_revision'):
        for field,clip in zip(fields,item['clips']):
            field.update({k:clip.get(k) for k in ('planned_source_window','safe_cut_confirmed',
                'planning_block','boundary_no_transition','source_group','combo_id','asset_kind','relative_path')})
    if item.get('voice_fit_revision'):
        for field,clip in zip(fields,item['clips']):
            field.update({k:clip.get(k) for k in ('voice_fit_revision','voice_fit_block_target_frames','voice_fit_block_before_frames')})
    frozen={'clips':fields,'transitions':item.get('transitions'),
            'seed':item.get('profile_seed'),'revision':item.get('timeline_revision')}
    if item.get('micro_trim_revision'):
        frozen.update({k:item.get(k) for k in ('micro_trim_revision','micro_trim_seed','micro_trim_parameters')})
    if item.get('voice_fit_revision'):
        frozen.update(voice_fit_revision=item['voice_fit_revision'],voice_fit_snapshot=item.get('voice_fit_snapshot'))
    return object_hash(frozen)


def validate_timing_item(item,cfg):
    if item.get('grouped_revision'):
        from grouped_planner import validate_grouped_item
        return validate_grouped_item(item,cfg)
    from core import AssemblyError,validate_sequence
    from video_processing import processing_settings
    from voice_edge_fitting import validate as validate_voice_fit
    validate_voice_fit(item,cfg)
    validate_sequence(item['clips'],cfg)
    saved_processing=item.get('video_processing')
    current_processing=processing_settings(cfg)
    if not item.get('voice_fit_revision') and cfg.get('processing_enabled') and cfg.get('processing_micro_trim_enabled') and item.get('micro_trim_revision')!=MICRO_TRIM_REVISION:
        raise AssemblyError('随机微裁未冻结，请重新规划。')
    if not item.get('random_duration_revision') and saved_processing:
        current_processing['timeline']={k:v for k,v in current_processing['timeline'].items()
                                        if k in saved_processing.get('timeline',{})}
    if not compatible_settings(saved_processing,current_processing):
        raise AssemblyError('处理设置与已保存方案不同，请重新规划。')
    if item.get('micro_trim_revision') and item['micro_trim_revision']!=MICRO_TRIM_REVISION:
        raise AssemblyError('随机微裁版本无效，请重新规划。')
    expected,transitions,total=bake_timeline(item['clips'],cfg,item['profile_seed'])
    for a,b in zip(item['clips'],expected):
        for k in ('source_in_seconds','source_out_seconds','playback_rate','duration_frames','timeline_start_frame',
                  'source_audio','effects','incoming_transition','outgoing_transition') + (('random_duration_applied','target_playback_seconds','actual_playback_seconds','window_mode','protection_reason') if item.get('random_duration_revision') else ()) + (MICRO_TRIM_FIELDS if item.get('micro_trim_revision') else ()):
            if a.get(k)!=b.get(k):raise AssemblyError(f'片段处理计划已被改动：{k}；请重新规划。')
    if item.get('transitions')!=transitions or item.get('total_frames')!=total:
        raise AssemblyError('转场重叠/总帧数与方案不一致。')
    if item.get('timeline_digest')!=timing_digest(item):raise AssemblyError('处理方案指纹不一致。')
    duration=total/cfg['fps'];bgm=item['bgm']
    if abs(item['duration']-duration)>1e-8:raise AssemblyError('时长与帧数不一致。')
    allowed_max = math.floor(cfg['max_seconds']*cfg['fps'])
    if item.get('duration_policy_version'):
        from video_plan_policy import REVISION, PREVIOUS_REVISION, LEGACY_REVISION, validate_frozen_item
        if item['duration_policy_version'] not in (REVISION, PREVIOUS_REVISION, LEGACY_REVISION):
            raise AssemblyError('未知视频时长策略版本。')
        planned_frames = item['planned_video_frames']
        voice = item.get('voice') or {}
        if item.get('narration_enabled') and voice.get('effective_voice_duration', 0)*cfg['fps'] > planned_frames:
            hold = math.ceil(cfg['voice_end_hold_seconds']*cfg['fps'] - 1e-7)
            allowed_max = max(allowed_max,
                              math.ceil(voice['effective_voice_duration']*cfg['fps'] - 1e-7) + hold)
        if (item['duration_policy_version'] == REVISION and
                item.get('video_mode') == '配音+字幕+BGM' and item.get('top_text_enabled') and
                item.get('top_text_policy') in ('auto', 'after_voice') and voice.get('effective_voice_duration')):
            from video_plan_policy import MIN_POST_VOICE_TOP_SECONDS
            allowed_max = max(allowed_max,
                              math.ceil((voice['effective_voice_duration'] +
                                         MIN_POST_VOICE_TOP_SECONDS)*cfg['fps'] - 1e-7))
        validate_frozen_item(item, cfg)
        from video_plan_policy import validate_text_layers
        validate_text_layers(item, cfg)
    if not item.get('voice_match_revision') and not math.ceil(cfg['min_seconds']*cfg['fps'])<=total<=allowed_max:
        raise AssemblyError('处理后视频时长超出设置范围。')
    if not 0<=bgm['start']<bgm['end']<=min(bgm['duration'],bgm['approved_end_limit'])+1e-6:
        raise AssemblyError('BGM不足以覆盖处理后的画面。')
    if abs(bgm['end']-bgm['start']-duration)>1e-6:raise AssemblyError('BGM与处理后时长不匹配。')
    if item.get('source_audio')!='discard':raise AssemblyError('原声必须排除。')


def _search_slots(pools,n,c_count,profiles,requests,cfg,rng,weight,lo,hi,target,deadline,v2=False,roles_override=None):
    """Bounded beam subset search in final frame space; every slot is a whole selected range."""
    roles=roles_override or (['A']+['AB']*(n-c_count-2)+['C']*c_count+['D'])
    if len(roles)!=n:return None
    options=[]
    for i,g in enumerate(roles):
        candidates=list(pools['A']+pools['B'] if g=='AB' else
                        pools['C_OPEN'] if g=='OPEN_C' else pools['A'] if g=='OPEN_A' else pools[g])
        # De-duplicate files before computing candidates for this slot.
        unique={}
        for c in candidates:unique.setdefault((c['identity_key'],c['hash']),c)
        candidates=list(unique.values())
        candidates.sort(key=lambda c:rng.random()**(1/max(.001,weight(c,i,n)*(
            cfg['a_weight'] if g=='AB' and c['group']=='A' else (1-cfg['a_weight'] if g=='AB' else 1)))),reverse=True)
        values=[(c,timing_for_media(c,profiles[i],cfg)['duration_frames']) for c in candidates[:(50 if v2 else 28)]]
        if not values:return None
        options.append(values)
    min_remaining=[0]*(n+1);max_remaining=[0]*(n+1)
    for i in range(n-1,-1,-1):
        mn=min(x[1] for x in options[i]);mx=max(x[1] for x in options[i])
        overlap=min(int(requests[i-1]['requested_frames']),mn//3) if i else 0
        min_remaining[i]=min_remaining[i+1]+mn-overlap
        max_remaining[i]=max_remaining[i+1]+mx
    # total, selected, ids, hashes, last duration, preference score, occupied mutex groups
    states=[(0,[],frozenset(),frozenset(),0,0.,frozenset())]
    for i,values in enumerate(options):
        generated=[]
        for total,chosen,ids,hashes,last,score,mutexes in states:
            for rank,(c,frames) in enumerate(values):
                if c['identity_key'] in ids or c['hash'] in hashes:continue
                mutex=c.get('mutex_group')
                if mutex and mutex in mutexes:continue
                overlap=transition_frames(requests[i-1],last,frames) if i else 0
                value=total+frames-overlap
                if value+min_remaining[i+1]>hi or value+max_remaining[i+1]<lo:continue
                # Rotation is part of the feasible beam score, not merely its random ordering.
                # Duration bounds above still decide whether a candidate can be used.
                newscore=score+(min(30.,max(0.,1/max(weight(c,i,n),1e-6)-1))*.22 if v2 else rank*.03)
                if chosen and c.get('source_id') and c.get('source_id')==chosen[-1].get('source_id'):newscore+=2
                generated.append((value,chosen+[c],ids|{c['identity_key']},hashes|{c['hash']},frames,newscore,
                                  mutexes|({mutex} if mutex else set())))
        if not generated:return None
        if i==n-1:
            feasible=[x for x in generated if lo<=x[0]<=hi]
            duration_weight=cfg['selection_v2_weights']['duration'] if v2 else 1.
            return min(feasible,key=lambda x:duration_weight*abs(x[0]-target)/cfg['fps']+x[5])[1] if feasible else None
        if time.monotonic()>=deadline:return None
        # Keep multiple ways to reach a frame total (different used identities).
        generated.sort(key=lambda x:abs(x[0]+(min_remaining[i+1]+max_remaining[i+1])/2-target)/cfg['fps']+x[5])
        kept=[];per_sum=Counter()
        for state in generated:
            bucket=(state[0],state[4])
            if per_sum[bucket]>=3:continue
            per_sum[bucket]+=1;kept.append(state)
            if len(kept)>=160:break
        states=kept
    return None


def plan_timeline_batch(clips,music,ranges,cfg,store,log,stop=None):
    from core import (AssemblyError,validate_config,music_candidates,choice_weighted,object_hash,
                      sequence_key,set_key,load_top_texts,now)
    from video_processing import normalize_processing_config,processing_settings,processing_warnings
    from dedup_v2 import REVISION as DEDUP_REVISION, SelectionIndex
    cfg=normalize_processing_config(cfg);validate_config(cfg)
    use_v2=cfg.get('selection_version','v2')=='v2'
    active=[dict(c) for c in clips if c.get('enabled') and (c.get('reviewed') or not use_v2)]
    if any(not c.get('reviewed') for c in active):raise AssemblyError('请先确认启用片段已复核；修边需另外查看本次处理方案。')
    pools={g:[c for c in active if c['group']==g] for g in 'ABCD'}
    if not pools['A'] or not pools['D'] or len(pools['C'])<cfg['c_min']:raise AssemblyError('开头/结尾或必选中间2没有足够素材。')
    tracks=music_candidates(music,ranges,cfg)
    if not tracks:raise AssemblyError('没有满足目标时长的音乐区间；请确认自然区间或显式允许音乐截尾淡出。')
    texts=load_top_texts(cfg['top_text_library']) if cfg.get('top_text_enabled') else []
    fixed=int(cfg.get('top_text_fixed_index',0) or 0)
    if cfg.get('top_text_enabled') and not texts:raise AssemblyError('顶部文案为空。')
    if fixed and not 1<=fixed<=len(texts):raise AssemblyError('固定文案编号超出范围。')
    seed=str(cfg.get('seed') or uuid.uuid4().hex);rng=random.Random(seed);history=store.usage()
    dedup=SelectionIndex(active,history,cfg) if use_v2 else None
    seen={h['sequence_hash'] for h in history};sets={h['set_hash'] for h in history if h.get('set_hash')}
    usage=Counter(c['identity_key'] for h in history for c in h['clips'])
    starts=Counter(h['clips'][0]['identity_key'] for h in history if h['clips'])
    ends=Counter(h['clips'][-1]['identity_key'] for h in history if h['clips'])
    musage=Counter(h.get('bgm',{}).get('hash','') for h in history)
    tusage=Counter(h.get('top_text',{}).get('hash','') for h in history);used_text=set()
    bid='assembly_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    settings=processing_settings(cfg)
    batch={'schema_version':1,'app_version':'1.0.5-ab1-dedup2' if use_v2 else '1.0.5-ab1','id':bid,'created_at':now(),'config':cfg,'seed':seed,
           'source_audio':'discard','scope':'manual_whole_clip_assembly','items':[],
           'warnings':processing_warnings(cfg),'requested_count':cfg['count'],
           'approval_note':'只对新输出执行明确选中的修边/变速/转场，不批准或修改原项目资格。'}
    if use_v2:
        skipped=sum(c.get('enabled') and not c.get('reviewed') for c in clips)
        if skipped: batch['warnings'].append(f'已排除{skipped}条启用但未确认复核的素材。')
    minf=math.ceil(cfg['min_seconds']*cfg['fps']);maxf=math.floor(cfg['max_seconds']*cfg['fps'])
    pref=(cfg['preferred_min']+cfg['preferred_max'])/2*cfg['fps']
    def weight(c,i,n):
        if use_v2:
            role='first' if i==0 else 'last' if i==n-1 else 'ordinary'
            return dedup.rotation_weight(c,role)
        w=(1+usage[c['identity_key']])**-.65
        if i==0:w/=(1+starts[c['identity_key']])**.8
        if i==n-1:w/=(1+ends[c['identity_key']])**.8
        return w
    for index in range(cfg['count']):
        if stop and stop.is_set():break
        deadline=time.monotonic()+cfg['plan_seconds_per_video'];best=None;good=0;rejections=Counter()
        for attempt in range(cfg['attempts_per_video']):
            if time.monotonic()>=deadline or (stop and stop.is_set()):break
            track=choice_weighted(rng,tracks,[1/(1+musage[t['media']['hash']]) for t in tracks])
            lo=hi=track['target_frames'] if track['natural'] else 0
            if not track['natural']:
                lo=minf;hi=min(maxf,math.floor((track['end_limit']-track['start'])*cfg['fps']+1e-7))
            target=min(hi,max(lo,pref))
            cc=rng.randint(cfg['c_min'],min(cfg['c_max'],len(pools['C'])))
            maxn=min(cfg['max_clips'],len(pools['A'])+len(pools['B'])+cc+1)
            ns=list(range(cc+2,maxn+1))
            if not ns:continue
            avg=sum(c['duration'] for c in active)/len(active)
            expected=max(cc+2,target/cfg['fps']/max(.5,avg))
            n=choice_weighted(rng,ns,[1/(1+abs(x-expected))**1.5 for x in ns])
            profile_seed=f'{seed}:{index}:{attempt}'
            profiles,requests=build_slot_profiles(n,cfg,profile_seed,set(range(n-cc-1)))
            profiles[0]['first_slot']=True
            seq=_search_slots(pools,n,cc,profiles,requests,cfg,rng,weight,lo,hi,target,deadline,use_v2)
            if not seq:
                rejections['时长/角色/有限搜索预算']+=1;continue
            if use_v2:
                identities=dedup.keys({'clips':seq})
                sh,sth=object_hash(identities),object_hash(sorted(identities))
            else:
                sh,sth=sequence_key(seq),set_key(seq)
                if sh in seen or (cfg['dedup_mode']=='strict' and sth in sets):continue
            out,transitions,total=bake_timeline(seq,cfg,profile_seed)
            if not lo<=total<=hi:
                rejections['实际取窗后时长或BGM区间']+=1;continue
            keys={c['identity_key'] for c in seq};overlap=0.
            for prev in batch['items']:
                other={c['identity_key'] for c in prev['clips']}
                overlap=max(overlap,len(keys&other)/min(len(keys),len(other)))
            if not use_v2 and cfg['dedup_mode']=='strict' and overlap>cfg['pair_overlap_limit']:continue
            nearest=None;dup_penalty=0.;reason='旧版选片'
            if use_v2:
                accepted,reason,nearest,dup_penalty=dedup.evaluate(
                    {'id':f'{bid}_{index+1:03d}','clips':out,'output_fps':cfg['fps']},
                    cfg['dedup_mode']=='strict')
                if not accepted:
                    rejections[reason]+=1;continue
            duration=total/cfg['fps']
            score=(max(cfg['preferred_min']-duration,0,duration-cfg['preferred_max'])*2+
                   musage[track['media']['hash']]*.5+rng.random()*.1)
            if use_v2:
                w=cfg['selection_v2_weights']
                score+=(dup_penalty+
                        sum(w['batch']*dedup.batch[dedup.identity(c)]+w['total']*dedup.total[dedup.identity(c)]*.05 for c in seq)/len(seq))
                score+=w['role']*(dedup.first[dedup.identity(seq[0])]+dedup.last[dedup.identity(seq[-1])])
                score+=w['batch']*(dedup.batch[dedup.identity(seq[0])]+dedup.batch[dedup.identity(seq[-1])])
            else:
                score+=(overlap*10+sum(usage[c['identity_key']] for c in seq)/len(seq)
                        +starts[seq[0]['identity_key']]*1.2+ends[seq[-1]['identity_key']]*1.1
                        +(5 if sth in sets else 0))
            continuous=sum((c['source_out_seconds']-c['source_in_seconds'])/c['playback_rate'] for c in out)
            overlap_seconds=sum(t['frames'] for t in transitions)/cfg['fps']
            item={'id':f'{bid}_{index+1:03d}','number':index+1,'status':'planned',
                  'sequence_hash':sh,'set_hash':sth,'clips':out,'total_frames':total,'duration':duration,
                  'source_duration_sum':sum(c['duration'] for c in seq),'frame_rounding_seconds':duration+overlap_seconds-continuous,
                  'transition_overlap_seconds':overlap_seconds,'transitions':transitions,
                  'profile_seed':profile_seed,'timeline_revision':TIMELINE_REVISION,'video_processing':settings,
                  'file_export_id':uuid.uuid4().hex if settings['hash_refresh'] else '',
                  'bgm':dict(track['media'],start=track['start'],end=track['start']+duration,
                             approved_end_limit=track['end_limit'],range_id=track['range_id'],natural=track['natural'],note=track['note']),
                  'source_audio':'discard','warnings':[f"第{i+1}段：{note}" for i,c in enumerate(out) for note in c['edit_notes']]}
            if use_v2:
                item.update(output_fps=cfg['fps'],selection_revision=DEDUP_REVISION,
                            target_account=str(cfg.get('selection_v2_target_account','')).strip(),
                            publication_status='not_marked',
                            dedup_detail={'nearest':nearest,'reason':reason,'penalty':round(dup_penalty,4),
                                          'clip_count_overlap_auxiliary':round(overlap,4),
                                          'account_history_status':('未检查账号发布历史' if not cfg.get('selection_v2_target_account') else '仅检查明确标记的同账号已发布记录'),
                                          'interval_limit':cfg['selection_v2_interval_limit'],
                                          'comparison_scope':'本批预留及全部可读历史',
                                          'denominator':'各自片段播放时长之和，未扣转场重叠'})
            if cfg.get('processing_enabled') and cfg.get('processing_ab_duration_enabled'):
                from video_processing import parse_positions
                item['random_duration_revision']=AB_DURATION_REVISION
                item['random_duration_seed']=profile_seed
                item['random_duration_parameters']={k:cfg.get(k,v) for k,v in TIMELINE_DEFAULTS.items() if k.startswith('processing_ab_duration_')}
                if cfg.get('processing_ab_duration_scope')=='positions':
                    for pos in sorted(parse_positions(cfg.get('processing_ab_duration_positions',''))):
                        if pos<=len(out) and out[pos-1]['group'] not in ('A','B'):
                            item['warnings'].append(f'第{pos}段属于{out[pos-1]["group"]}组，跳过A/B随机时长，不替换位置。')
            freeze_micro_trim(item,cfg)
            item['timeline_digest']=timing_digest(item)
            validate_timing_item(item,cfg)
            good+=1
            if best is None or score<best[0]:best=(score,item)
            if good>=5:break
        if best is None:
            main=', '.join(f'{k} {v}次' for k,v in rejections.most_common(3)) or '候选不足'
            batch['warnings'].append(f'限定搜索预算内仅找到{len(batch["items"])}/{cfg["count"]}条；主要瓶颈：{main}。可增加搜索秒数、可用素材或音乐区间；严格区间上限未放宽。')
            break
        item=best[1]
        if texts:
            if fixed:ti=fixed-1
            elif cfg.get('top_text_mode')=='sequence':ti=index%len(texts)
            else:
                choices=[i for i,t in enumerate(texts) if object_hash(t) not in used_text] or list(range(len(texts)))
                ti=choice_weighted(rng,choices,[1/(1+tusage[object_hash(texts[i])]) for i in choices])
            th=object_hash(texts[ti]);used_text.add(th);tusage[th]+=1
            item['top_text']={'enabled':True,'text':texts[ti],'index':ti+1,'hash':th,'source':str(Path(cfg['top_text_library']).resolve())}
        else:item['top_text']={'enabled':False,'text':'','index':0,'hash':'','source':cfg.get('top_text_library','')}
        from visual_layout import freeze as freeze_visual_layout
        freeze_visual_layout(item,cfg)
        batch['items'].append(item);seen.add(item['sequence_hash']);sets.add(item['set_hash'])
        if use_v2:dedup.add(item)
        usage.update(c['identity_key'] for c in item['clips']);starts[item['clips'][0]['identity_key']]+=1
        ends[item['clips'][-1]['identity_key']]+=1;musage[item['bgm']['hash']]+=1
        log(f"方案{index+1:02d}：{item['duration']:.3f}秒 / {len(item['clips'])}段 / 转场重叠{item['transition_overlap_seconds']:.3f}秒 / {item['bgm']['name']}")
    if not batch['items']:raise AssemblyError(batch['warnings'][-1] if batch['warnings'] else '没有找到可用方案。')
    if use_v2:
        batch['selection_revision']=DEDUP_REVISION
        batch['selection_v2_summary']=dedup.summary(batch['items'],pools)
        batch['selection_v2_summary']['bottlenecks']=batch['warnings'][-1] if len(batch['items'])<cfg['count'] else '已达到目标数量；未放宽硬限制。'
    return batch
