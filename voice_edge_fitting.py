"""Bounded, target-driven edge fitting; randomize location, never required time."""
from copy import deepcopy
import math
import random

REVISION='voice_difference_edges_v1'
MODE='random_edges_v1'
CAPACITY_REVISION='voice_fit_capacity_v2'


def capacity_enabled(cfg):
    """New plans opt in explicitly; old manifests keep their sampled limits."""
    return cfg.get('voice_fit_capacity_revision') == CAPACITY_REVISION


def enabled(cfg):
    return bool(cfg.get('match_video_to_voice') and cfg.get('voice_fit_mode')==MODE)


def settings(cfg):
    s = dict(mode=cfg.get('voice_fit_mode'),
        max_trim_seconds=float(cfg.get('voice_fit_max_trim_seconds',1.)),
        max_excess_seconds=float(cfg.get('voice_fit_max_excess_seconds',3.)),
        minimum_seconds=float(cfg.get('processing_min_kept_seconds',.8)),
        protect_first=bool(cfg.get('processing_micro_trim_protect_first',True)),
        protect_last=bool(cfg.get('processing_micro_trim_protect_last',True)))
    from shot_trim_controls import active, settings as shot_settings, minimum, maximum
    if active(cfg):
        s.update(shot_trim=shot_settings(cfg),minimum_seconds=minimum(cfg),max_trim_seconds=maximum(cfg))
    if capacity_enabled(cfg):
        s['capacity_revision']=CAPACITY_REVISION
    return s


def validate_settings(cfg):
    if 'voice_fit_mode' not in cfg:return
    if cfg['voice_fit_mode'] not in (MODE,'legacy_tail'):
        raise ValueError('配音差额裁切模式无效。')
    s=settings(cfg)
    for key,upper in [('max_trim_seconds',5),('max_excess_seconds',15)]:
        if not math.isfinite(s[key]) or not 0<s[key]<=upper:
            raise ValueError('配音差额裁切上限须为正数；单段最多5秒，整条最多15秒。')


def prepare(seq):
    result=deepcopy(seq)
    for c in result:c['voice_fit_revision']=REVISION
    return result


def active(seq):
    return bool(seq) and all(c.get('voice_fit_revision')==REVISION for c in seq)


def _block_frames(out,transitions,indices):
    return sum(out[i]['duration_frames'] for i in indices)-sum(
        t['frames'] for i,t in enumerate(transitions) if i in indices and i+1 in indices)


def _fit_legacy(seq,cfg,seed,blocks):
    from core import AssemblyError
    from editing_profiles import grouped
    from timeline_processing import bake_timeline
    validate_settings(cfg);s=settings(cfg);fps=cfg['fps']
    seq=prepare(seq)
    before,transitions,total_before=bake_timeline(seq,cfg,seed)
    names=[name for name,_ in blocks]
    if len(names)!=len(set(names)):
        raise AssemblyError('VOICE_FIT_BLOCK_INVALID：时间槽重复')
    if any(c.get('planning_block','all') not in names for c in seq):
        raise AssemblyError('VOICE_FIT_BLOCK_INVALID：时间槽未覆盖全部片段')
    initial={}
    for name,target in blocks:
        indices={i for i,c in enumerate(seq) if c.get('planning_block','all')==name}
        if not indices or target<1:
            raise AssemblyError('CANDIDATES_INSUFFICIENT：时间槽没有完整候选画面')
        initial[name]=_block_frames(before,transitions,indices)
        if initial[name]<target:
            raise AssemblyError('VIDEO_TARGET_UNSATISFIABLE：画面比配音目标短，不补帧、不循环')
    excess_total=sum(initial[name]-target for name,target in blocks)
    if excess_total>math.floor(s['max_excess_seconds']*fps+1e-7):
        raise AssemblyError(f'VOICE_FIT_EXCESS_TOO_LARGE：候选多出{excess_total/fps:.3f}秒，超过整条裁短上限{s["max_excess_seconds"]:g}秒，请重选组合')
    if total_before-sum(initial.values()):
        raise AssemblyError('VOICE_FIT_BLOCK_INVALID：锁定时间槽之间存在未隔离的重叠转场')
    for name,target in blocks:
        for c in seq:
            if c.get('planning_block','all')==name:
                c['voice_fit_block_target_frames']=target
                c['voice_fit_block_before_frames']=initial[name]
        indices=[i for i,c in enumerate(seq) if c.get('planning_block','all')==name]
        rng=random.Random(f'{seed}:{REVISION}:{name}')
        order=list(indices);rng.shuffle(order)
        sides={}
        limits={}
        from shot_trim_controls import active as shot_active, sample as shot_sample
        control_rng=random.Random(f'{seed}:shot_trim_controls_v2:{name}')
        for i in order:
            choices=['head','tail']
            if shot_active(cfg):
                mode=cfg.get('processing_micro_trim_mode','random_side')
                if mode in ('head','tail'): choices=[mode]
                limits[i]=shot_sample(cfg,control_rng) if cfg.get('shot_trim_voice_enabled',True) else 0.
            if i==0 and s['protect_first'] and 'head' in choices:choices.remove('head')
            if i==len(seq)-1 and s['protect_last'] and 'tail' in choices:choices.remove('tail')
            if choices:sides[i]=rng.choice(choices)
        for _ in range(len(seq)*6+12):
            out,transitions,_=bake_timeline(seq,cfg,seed)
            excess=_block_frames(out,transitions,set(indices))-target
            if excess==0:break
            if excess<0:
                raise AssemblyError('VOICE_FIT_OVERRUN：差额裁切越过目标，候选未采用')
            usable=[]
            for i in order:
                if i not in sides or (grouped(cfg) and not seq[i].get('safe_cut_confirmed')):continue
                from ball_pacing import active as paced, minimum_frames
                minimum=min(before[i]['duration_frames'],max(1,math.ceil(s['minimum_seconds']*fps-1e-7),
                    minimum_frames(cfg,i,before[i]['duration_frames']) if paced(cfg) else 1))
                removed=before[i]['duration_frames']-out[i]['duration_frames']
                base_span=before[i]['source_out_seconds']-before[i]['source_in_seconds']
                trim_limit=limits.get(i,s['max_trim_seconds'])
                min_cap_frames=max(1,math.ceil((base_span-trim_limit)/out[i]['playback_rate']*fps-1e-7))
                limit=before[i]['duration_frames']-min_cap_frames
                capacity=min(limit-removed,out[i]['duration_frames']-minimum)
                if capacity>0:usable.append((i,capacity))
            if not usable:
                reason=('未确认安全裁切的G素材保持完整；' if grouped(cfg) else '')
                raise AssemblyError('VOICE_FIT_CAPACITY_INSUFFICIENT：'+reason+'首尾保护、单段上限或最短保留限制下无法裁掉剩余差额，请重选组合')
            i,capacity=usable[0];clip=out[i]
            frames=clip['duration_frames']-min(excess,capacity)
            span=frames*clip['playback_rate']/fps
            side=sides[i]
            start=clip['source_in_seconds'] if side=='tail' else clip['source_out_seconds']-span
            end=start+span
            seq[i]['planned_source_window']=dict(base_in=before[i]['source_in_seconds'],
                base_out=before[i]['source_out_seconds'],start=round(start,9),end=round(end,9),
                frames=frames,proof_hash=seq[i]['hash'],fit_revision=REVISION,side=side,
                removed_output_frames=before[i]['duration_frames']-frames,
                reason=f'按配音实际差额裁{"片头" if side=="head" else "片尾"}；随机仅决定位置，裁量由目标帧数决定')
        else:
            raise AssemblyError('VOICE_FIT_NOT_CONVERGED：转场帧量化未在有限预算内收敛')
    out,transitions,total=bake_timeline(seq,cfg,seed)
    if total!=sum(target for _,target in blocks):
        raise AssemblyError('VOICE_FRAME_MISMATCH：差额裁切后总帧数不等于配音目标')
    return seq,out,transitions,total


_BLOCK_LABELS={'all':'整条画面','pre':'包装前画面','package':'包装画面','post':'包装后画面'}


def _timeline_frames(frames,requests,indices):
    """Use the same integer overlap rule as the final baked timeline."""
    from timeline_processing import transition_frames
    members=set(indices)
    return sum(frames[i] for i in indices)-sum(
        transition_frames(requests[i],frames[i],frames[i+1])
        for i in indices if i+1 in members)


def _minimum_block(frames,lower,requests,indices):
    """Find the true minimum, including 1/3 overlap frame quantization.

    Overlap depends only on floor(duration / 3), capped by its requested
    length. Within each such interval only its shortest duration can improve
    the minimum. This keeps the chain search small even for long sources.
    Simply setting every clip to its shortest permitted length is incorrect:
    losing two quantized overlaps can make a timeline one frame *longer*.
    """
    from timeline_processing import transition_frames
    members=set(indices);states={};previous=None;chosen={};total=0
    for i in indices:
        caps=[requests[j]['requested_frames'] for j in (i-1,i)
              if 0<=j<len(requests) and j in members and j+1 in members
              and requests[j]['type']!='none']
        options={lower[i]}
        for value in range(((lower[i]+2)//3)*3,min(frames[i],3*max(caps,default=0))+1,3):
            options.add(value)
        if previous is None or previous+1!=i:
            if states:
                cost,path=min(states.values());total+=cost;chosen.update(path)
            states={value:(value,((i,value),)) for value in sorted(options)}
        else:
            next_states={}
            for value in sorted(options):
                next_states[value]=min((cost+value-transition_frames(requests[previous],old,value),
                                        path+((i,value),))
                                       for old,(cost,path) in states.items())
            states=next_states
        previous=i
    if states:
        cost,path=min(states.values());total+=cost;chosen.update(path)
    return total,chosen


def _capacity_analysis(seq,before,transitions,cfg,blocks,s):
    from editing_profiles import grouped
    from shot_trim_controls import active as shot_active
    fps=cfg['fps'];frames=[c['duration_frames'] for c in before]
    requests=[dict(type=t['type'],requested_frames=t['requested_frames']) for t in transitions]
    sides={};lower=list(frames);clip_details=[]
    cut_enabled=not shot_active(cfg) or bool(cfg.get('shot_trim_voice_enabled',True))
    for i,c in enumerate(before):
        choices=['head','tail']
        if shot_active(cfg) and cfg.get('processing_micro_trim_mode') in ('head','tail'):
            choices=[cfg['processing_micro_trim_mode']]
        if i==0 and s['protect_first'] and 'head' in choices:choices.remove('head')
        if i==len(seq)-1 and s['protect_last'] and 'tail' in choices:choices.remove('tail')
        unsafe=grouped(cfg) and not seq[i].get('safe_cut_confirmed')
        action_slack=None
        core=c.get('picture_annotation',{}).get('action_core') if cfg.get('editorial_enabled') else None
        if core and choices:
            slack={'head':max(0,core[0]-c['source_in_seconds']),
                   'tail':max(0,c['source_out_seconds']-core[1])}
            side=max(choices,key=lambda side:slack[side]);choices=[side];action_slack=slack[side]
        if choices and not unsafe and cut_enabled:
            sides[i]=choices
            source_span=c['source_out_seconds']-c['source_in_seconds']
            cap_min=max(1,math.ceil((source_span-s['max_trim_seconds'])/c['playback_rate']*fps-1e-7))
            from ball_pacing import active as paced, minimum_frames
            keep_min=min(frames[i],max(1,math.ceil(s['minimum_seconds']*fps-1e-7),
                minimum_frames(cfg,i,frames[i]) if paced(cfg) else 1))
            lower[i]=min(frames[i],max(cap_min,keep_min))
            if action_slack is not None:
                lower[i]=max(lower[i],frames[i]-math.floor(action_slack/c['playback_rate']*fps+1e-7))
        clip_details.append(dict(position=i+1,path=c.get('relative_path',c['path']),hash=c['hash'],
            candidate_frames=frames[i],minimum_clip_frames=lower[i],
            maximum_clip_reduction_frames=frames[i]-lower[i],
            maximum_source_reduction_seconds=(round(c['source_out_seconds']-c['source_in_seconds']-
                lower[i]*c['playback_rate']/fps,9) if lower[i]<frames[i] else 0.),
            safe_cut_confirmed=not unsafe,allowed_sides=choices,cut_enabled=cut_enabled,
            playback_rate=c['playback_rate']))
    diagnostic=dict(revision=CAPACITY_REVISION,fps=fps,settings=s,blocks=[],
                    candidate_frames=sum(frames)-sum(t['frames'] for t in transitions),
                    target_frames=sum(target for _,target in blocks))
    witness={}
    for name,target in blocks:
        indices=[i for i,c in enumerate(seq) if c.get('planning_block','all')==name]
        initial=_timeline_frames(frames,requests,indices)
        minimum,chosen=_minimum_block(frames,lower,requests,indices);witness.update(chosen)
        capacity=initial-minimum;required=initial-target
        entries=[dict(clip_details[i],minimum_timeline_witness_frames=chosen[i]) for i in indices]
        diagnostic['blocks'].append(dict(name=name,label=_BLOCK_LABELS.get(name,name),
            candidate_frames=initial,target_frames=target,required_reduction_frames=required,
            minimum_timeline_frames=minimum,maximum_reduction_frames=capacity,
            required_reduction_seconds=required/fps,maximum_reduction_seconds=capacity/fps,
            missing_reduction_frames=max(0,required-capacity),
            missing_reduction_seconds=max(0,required-capacity)/fps,
            maximum_source_reduction_seconds=sum(e['maximum_source_reduction_seconds'] for e in entries),
            unsafe_clip_count=sum(not e['safe_cut_confirmed'] for e in entries),
            protected_clip_count=sum(not e['allowed_sides'] for e in entries),
            cuttable_clip_count=sum(e['maximum_clip_reduction_frames']>0 for e in entries),
            feasible=0<=required<=capacity,clips=entries))
    diagnostic['excess_frames']=sum(b['required_reduction_frames'] for b in diagnostic['blocks'])
    diagnostic['excess_seconds']=diagnostic['excess_frames']/fps
    diagnostic['maximum_excess_frames']=math.floor(s['max_excess_seconds']*fps+1e-7)
    diagnostic['feasible']=(all(b['feasible'] for b in diagnostic['blocks']) and
                            diagnostic['excess_frames']<=diagnostic['maximum_excess_frames'])
    return diagnostic,requests,frames,sides,witness


def _capacity_error(code,text,diagnostic,block=None):
    from core import AssemblyError
    detail=deepcopy(diagnostic);detail['failure_code']=code
    if block is not None:detail['failed_block']=block['name']
    exc=AssemblyError(code+'：'+text);exc.fit_diagnostic=detail
    raise exc


def _fit_capacity(seq,cfg,seed,blocks):
    from timeline_processing import bake_timeline
    validate_settings(cfg);s=settings(cfg);fps=cfg['fps'];seq=prepare(seq)
    for c in seq:
        c.pop('voice_fit_capacity_proof',None)
        c['voice_fit_capacity_revision']=CAPACITY_REVISION
    before,transitions,total_before=bake_timeline(seq,cfg,seed)
    names=[name for name,_ in blocks]
    if len(names)!=len(set(names)) or any(c.get('planning_block','all') not in names for c in seq):
        _capacity_error('VOICE_FIT_BLOCK_INVALID','时间槽重复或未覆盖全部片段',dict(revision=CAPACITY_REVISION))
    if any(target<1 or not any(c.get('planning_block','all')==name for c in seq) for name,target in blocks):
        _capacity_error('CANDIDATES_INSUFFICIENT','时间槽没有完整候选画面',dict(revision=CAPACITY_REVISION))
    diagnostic,requests,frames,sides,witness=_capacity_analysis(seq,before,transitions,cfg,blocks,s)
    if total_before!=sum(b['candidate_frames'] for b in diagnostic['blocks']):
        _capacity_error('VOICE_FIT_BLOCK_INVALID','锁定时间槽之间存在未隔离的重叠转场',diagnostic)
    for b in diagnostic['blocks']:
        if b['required_reduction_frames']<0:
            _capacity_error('VIDEO_TARGET_UNSATISFIABLE',
                f'{b["label"]}比配音目标短{-b["required_reduction_seconds"]:.3f}秒，不补帧、不循环',diagnostic,b)
    if diagnostic['excess_frames']>diagnostic['maximum_excess_frames']:
        _capacity_error('VOICE_FIT_EXCESS_TOO_LARGE',
            f'候选多出{diagnostic["excess_seconds"]:.3f}秒，超过整条裁短上限{s["max_excess_seconds"]:g}秒，请重选组合',diagnostic)
    for b in diagnostic['blocks']:
        if not b['feasible']:
            reason=(f'；{b["unsafe_clip_count"]}个镜头未确认安全裁切，保持完整' if b['unsafe_clip_count'] else '')
            reason+=(f'；{b["protected_clip_count"]}个镜头被首尾方向保护阻断' if b['protected_clip_count'] else '')
            if not cfg.get('shot_trim_voice_enabled',True):reason+='；配音裁剪开关已关闭'
            _capacity_error('VOICE_FIT_CAPACITY_INSUFFICIENT',
                f'{b["label"]}需要裁短{b["required_reduction_seconds"]:.3f}秒，'
                f'最多可合法裁短{b["maximum_reduction_seconds"]:.3f}秒，还缺{b["missing_reduction_seconds"]:.3f}秒'
                f'；单镜头最多裁{s["max_trim_seconds"]:g}秒原片，最短保留{s["minimum_seconds"]:g}秒'
                +reason+'，请重选组合',diagnostic,b)
    for b in diagnostic['blocks']:
        name=b['name'];target=b['target_frames']
        indices=[i for i,c in enumerate(seq) if c.get('planning_block','all')==name]
        for i in indices:
            seq[i].update(voice_fit_block_target_frames=target,voice_fit_block_before_frames=b['candidate_frames'])
        rng=random.Random(f'{seed}:{CAPACITY_REVISION}:{name}')
        order=list(indices);rng.shuffle(order)
        from ball_pacing import active as paced
        if paced(cfg):
            # Long clips absorb narration differences before short clips.
            order.sort(key=lambda i:frames[i]-witness[i],reverse=True)
        selected_sides={i:rng.choice(sides[i]) for i in order if i in sides}
        # Each step removes <= remaining excess clip frames; shrinking overlaps
        # can only reduce that removal, so the timeline can never undershoot.
        budget=sum(frames[i]-witness[i] for i in indices)+1
        for _ in range(budget):
            excess=_timeline_frames(frames,requests,indices)-target
            if excess==0:break
            i=next((j for j in order if j in selected_sides and frames[j]>witness[j]),None)
            if i is None:
                _capacity_error('VOICE_FIT_NOT_CONVERGED','容量证明与实际裁量不一致',diagnostic,b)
            frames[i]-=min(excess,frames[i]-witness[i]);clip=before[i]
            span=frames[i]*clip['playback_rate']/fps;side=selected_sides[i]
            start=clip['source_in_seconds'] if side=='tail' else clip['source_out_seconds']-span
            seq[i]['planned_source_window']=dict(base_in=clip['source_in_seconds'],base_out=clip['source_out_seconds'],
                start=round(start,9),end=round(start+span,9),frames=frames[i],proof_hash=seq[i]['hash'],
                fit_revision=REVISION,capacity_revision=CAPACITY_REVISION,side=side,
                removed_output_frames=clip['duration_frames']-frames[i],
                reason=f'按时间槽配音差额裁{"片头" if side=="head" else "片尾"}；裁量由合法容量分配，随机仅决定镜头和方向')
        else:
            _capacity_error('VOICE_FIT_NOT_CONVERGED','转场帧量化未在容量预算内收敛',diagnostic,b)
        b.update(actual_removed_timeline_frames=b['required_reduction_frames'],
                 actual_removed_clip_frames=sum(before[i]['duration_frames']-frames[i] for i in indices),
                 remaining_reduction_frames=0)
    seq[0]['voice_fit_capacity_proof']=diagnostic
    out,transitions,total=bake_timeline(seq,cfg,seed)
    if total!=sum(target for _,target in blocks) or any(
            _block_frames(out,transitions,{i for i,c in enumerate(seq) if c.get('planning_block','all')==b['name']})!=b['target_frames']
            for b in diagnostic['blocks']):
        _capacity_error('VOICE_FRAME_MISMATCH','差额裁切后时间槽帧数不等于配音目标',diagnostic)
    return seq,out,transitions,total


def fit(seq,cfg,seed,blocks):
    if capacity_enabled(cfg):return _fit_capacity(seq,cfg,seed,blocks)
    return _fit_legacy(seq,cfg,seed,blocks)


def evaluate_capacity(seq,cfg,seed,blocks):
    """Non-mutating fit trial. Reuse ``result`` rather than fitting twice.

    Returns feasible/result/diagnostic/error. Failure diagnostics describe
    every slot; settings errors still propagate to the configuration UI.
    """
    from core import AssemblyError
    try:
        result=fit(seq,cfg,seed,blocks)
        return dict(feasible=True,result=result,
                    diagnostic=deepcopy(result[0][0].get('voice_fit_capacity_proof',{})),error=None)
    except AssemblyError as exc:
        return dict(feasible=False,result=None,diagnostic=deepcopy(getattr(exc,'fit_diagnostic',{})),error=str(exc))


def freeze(item,cfg):
    if not active(item['clips']):return
    blocks={}
    for c in item['clips']:
        name=c.get('planning_block','all')
        blocks[name]=(c['voice_fit_block_before_frames'],c['voice_fit_block_target_frames'])
    decisions=[]
    for i,c in enumerate(item['clips']):
        w=c.get('planned_source_window')
        if w and w.get('fit_revision')==REVISION:
            decisions.append(dict(position=i+1,path=c.get('relative_path',c['path']),hash=c['hash'],
                side=w['side'],before_start=w['base_in'],before_end=w['base_out'],
                start=w['start'],end=w['end'],removed_frames=w['removed_output_frames'],
                removed_source_seconds=round((w['base_out']-w['base_in'])-(w['end']-w['start']),9),
                removed_playback_seconds=w['removed_output_frames']/cfg['fps']))
    item.update(voice_fit_revision=REVISION,voice_fit_snapshot=dict(revision=REVISION,
        settings=settings(cfg),seed=item['profile_seed'],
        candidate_frames=sum(a for a,_ in blocks.values()),target_frames=item['total_frames'],
        removed_timeline_frames=sum(a-b for a,b in blocks.values()),
        blocks=[dict(name=name,before_frames=a,target_frames=b) for name,(a,b) in blocks.items()],
        decisions=decisions,extra_random_cut_suppressed=True))
    if capacity_enabled(cfg):
        item['voice_fit_snapshot']['capacity_proof']=deepcopy(item['clips'][0].get('voice_fit_capacity_proof',{}))


def validate(item,cfg):
    from core import AssemblyError
    if not item.get('voice_fit_revision'):
        if any(c.get('voice_fit_revision') for c in item['clips']):
            raise AssemblyError('VOICE_FIT_SNAPSHOT_CHANGED：缺少差额裁切快照')
        return
    snapshot=item.get('voice_fit_snapshot') or {}
    if not enabled(cfg) or item['voice_fit_revision']!=REVISION or snapshot.get('settings')!=settings(cfg):
        raise AssemblyError('VOICE_FIT_SNAPSHOT_CHANGED：差额裁切设置或版本不一致')
    seq=deepcopy(item['clips'])
    for c in seq:
        c.pop('planned_source_window',None)
        c.pop('voice_fit_block_target_frames',None);c.pop('voice_fit_block_before_frames',None)
    try:
        _,out,transitions,total=fit(seq,cfg,item['profile_seed'],
            [(b['name'],b['target_frames']) for b in snapshot['blocks']])
        expected=dict(clips=out,total_frames=total,profile_seed=item['profile_seed'])
        freeze(expected,cfg)
    except (KeyError,ValueError,TypeError,AssemblyError) as exc:
        raise AssemblyError('VOICE_FIT_SNAPSHOT_CHANGED：无法复核冻结差额裁切') from exc
    if (expected['voice_fit_snapshot']!=snapshot or transitions!=item['transitions'] or
            total!=item['total_frames'] or any(a.get('planned_source_window')!=b.get('planned_source_window')
                for a,b in zip(item['clips'],out))):
        raise AssemblyError('VOICE_FIT_SNAPSHOT_CHANGED：随机位置/实际裁量/时间槽已被改动')
