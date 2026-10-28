"""G-format strategy; shared media/cache, dedup, time windows, voice and renderer."""
from copy import deepcopy
from collections import Counter
from datetime import datetime
import math
import random
import time
import uuid

from grouped_materials import Coverage,GroupedError,conflict,REVISION


def _whole_sets(pool,goal,cfg,snapshot,*,packaging=False,max_slots=None,end_extra_frames=0):
    """Bounded complete-action lookahead; shared bake_timeline still decides timing."""
    if (cfg.get('material_format') == 'sk_diverse' or any(c.get('safe_cut_confirmed') for c in pool) or cfg.get('processing_speed_enabled') or
            (cfg.get('processing_enabled') and cfg.get('processing_ab_duration_enabled')) or len(pool)>18):return None
    max_slots=min(cfg['max_clips'] if max_slots is None else max_slots,len(pool))
    if max_slots<1:return []
    margin=(2*math.floor(float(cfg.get('package_anchor_margin_seconds',.2))*cfg['fps']+1e-7)+end_extra_frames) if packaging else 0
    overlap=(math.ceil(max(float(cfg.get('processing_transition_max',0)),float(cfg.get('processing_transition_seconds',0)))*cfg['fps'])
             if cfg.get('processing_enabled') and cfg.get('processing_transition') else 0)
    limit=goal+(margin if packaging else max(0,max_slots-1)*overlap)
    candidates=[(c,math.floor(c['duration']*cfg['fps']+1e-8)) for c in pool]
    candidates=[(c,n) for c,n in candidates if 0<n<=limit]
    results=[];visited=0
    def visit(start,selected,total):
        nonlocal visited
        visited+=1
        if visited>10000:return
        if total>=goal:
            if total<=goal+(margin if packaging else max(0,len(selected)-1)*overlap):results.append(selected)
            if packaging or all(n>overlap for c,n in candidates):return
        if len(selected)>=max_slots:return
        for i in range(start,len(candidates)):
            c,n=candidates[i]
            if total+n<=limit and not conflict(selected+[c],snapshot):visit(i+1,selected+[c],total+n)
    visit(0,[],0)
    return results if visited<=10000 else None


def _complete_prefix(pool,locked,post,start,blocks,cfg,seed,rng,dedup,snapshot,cache,*,edge_fit=False):
    """Try bounded uncut prefixes through the shared fitter and real dedup checks."""
    from voice_duration_matching import fit_windows
    from core import choice_weighted,AssemblyError
    if not start:return []+locked+post
    available=[c for c in pool if not conflict(locked+post+[c],snapshot)]
    key=(start,cfg['max_clips']-len(locked+post),tuple(sorted(c['relative_path']+'|'+c['hash'] for c in available)))
    if key not in cache:cache[key]=_whole_sets(available,start,cfg,snapshot,max_slots=cfg['max_clips']-len(locked+post))
    sets=cache[key]
    if sets is None:return None
    remaining=list(sets);errors=Counter()
    for _ in range(min(len(remaining),100)):
        chosen=choice_weighted(rng,remaining,[sum(dedup.rotation_weight(c,'ordinary') for c in s)/len(s) for s in remaining])
        remaining.remove(chosen)
        pre=[dict(c,planning_block='pre',boundary_no_transition=False) for c in chosen];rng.shuffle(pre)
        seq=pre+locked+post
        try:
            _,out,_,_=fit_windows(seq,cfg,seed,blocks,edge_fit=edge_fit)
            accepted,reason,_,_=dedup.evaluate(dict(id='candidate',clips=out,output_fps=cfg['fps']),cfg['dedup_mode']=='strict')
            if accepted:return seq
            errors['HISTORY_DEDUP_REJECTED：'+reason]+=1
        except (ValueError,AssemblyError) as exc:errors[str(exc)]+=1
    raise GroupedError('WHOLE_TIMELINE_INSUFFICIENT','；'.join(k for k,v in errors.most_common(3)) or '完整动作候选不足')


def _block(pool,goal,locked,name,cfg,rng,dedup,snapshot,*,exact=True,whole_cache=None,edge_fit=False,deadline=None):
    from core import choice_weighted
    if edge_fit and cfg.get('voice_fit_capacity_revision')=='voice_fit_capacity_v2':
        from grouped_capacity_selection import select_block
        return select_block(pool,goal,locked,name,cfg,rng,dedup,snapshot,deadline=deadline)
    if exact and name!='package' and whole_cache is not None:
        available=[c for c in pool if not conflict(locked+[c],snapshot)]
        key=(goal,cfg['max_clips']-len(locked),tuple(sorted(c['relative_path']+'|'+c['hash'] for c in available)))
        if key not in whole_cache:whole_cache[key]=_whole_sets(available,goal,cfg,snapshot,max_slots=cfg['max_clips']-len(locked))
        sets=whole_cache[key]
        if sets is not None:
            if not sets:raise GroupedError('CANDIDATES_INSUFFICIENT',f'{name}时间槽没有满足完整动作及转场边界的合法组合')
            selected=choice_weighted(rng,sets,[sum(dedup.rotation_weight(c,'ordinary') for c in s)/len(s) for s in sets])
            selected=[dict(c,planning_block=name,boundary_no_transition=False) for c in selected];rng.shuffle(selected)
            return selected
    chosen=[]
    estimate=0
    while estimate<goal:
        candidates=[c for c in pool if not conflict(locked+chosen+[c],snapshot)]
        if cfg.get('material_format') == 'sk_diverse' and not locked and not chosen:
            from sk_materials import opening_allowed
            candidates=[c for c in candidates if opening_allowed(c,cfg)]
        if name!='package' and exact:
            rate=max(float(cfg.get('processing_speed_max',1)),float(cfg.get('processing_speed',1))) if cfg.get('processing_speed_enabled') else 1.
            overlap=(math.ceil(max(float(cfg.get('processing_transition_max',0)),float(cfg.get('processing_transition_seconds',0)))*cfg['fps'])
                     if cfg.get('processing_enabled') and cfg.get('processing_transition') else 0)
            # An uncut action longer than all remaining time plus possible
            # shared transitions cannot become a legal exact-duration solution.
            candidates=[c for c in candidates if c.get('safe_cut_confirmed') or
                math.floor(c['duration']/rate*cfg['fps']+1e-8)<=goal-estimate+len(chosen)*overlap]
        if not candidates or len(chosen)+len(locked)>=cfg['max_clips']:
            if chosen and edge_fit and name!='package':return chosen
            raise GroupedError('CANDIDATES_INSUFFICIENT',f'{name}时间槽素材不足或覆盖冲突')
        # Prefer one complete packaging action that spans the remaining anchor.
        if name=='package':
            margin=math.floor(float(cfg.get('package_anchor_margin_seconds',.2))*cfg['fps']+1e-7)
            candidates=[c for c in candidates if c.get('safe_cut_confirmed') or
                math.floor(c['duration']*cfg['fps']+1e-8)<=goal-estimate+2*margin]
            if not candidates:
                raise GroupedError('PACKAGE_WHOLE_ACTION_TOO_LONG','剩余包装槽无法容纳未允许裁切的完整片段')
            long=[c for c in candidates if math.floor(c['duration']*cfg['fps']+1e-8)>=goal-estimate]
            if long:candidates=long
        weight_hook=getattr(dedup,'candidate_weights',None)
        weights=(weight_hook(candidates,locked+chosen,position=len(locked+chosen)) if weight_hook
                 else [dedup.rotation_weight(c,'ordinary') for c in candidates])
        c=choice_weighted(rng,candidates,weights)
        chosen.append(dict(c,planning_block=name,
                           boundary_no_transition=(name=='package')))
        seconds=c['duration']
        if not edge_fit and cfg.get('processing_enabled') and cfg.get('processing_ab_duration_enabled') and c.get('safe_cut_confirmed'):
            from timeline_processing import duration_values
            seconds=min(seconds,max(duration_values(cfg)))
        if cfg.get('processing_speed_enabled'):
            seconds/=max(float(cfg.get('processing_speed_max',1)),float(cfg.get('processing_speed',1)))
        estimate+=max(1,math.floor(seconds*cfg['fps']+1e-8))
    return chosen


def business_digest(item):
    from core import object_hash
    fields=('editing_profile_id','grouped_revision','coverage_snapshot',
            'package_anchor','voice_match_snapshot','timeline_digest')
    if item.get('package_matching_revision') is not None:fields+=('package_matching_revision',)
    if item.get('ball_scene_snapshot') is not None:fields+=('ball_scene_snapshot',)
    if item.get('product_spec_snapshot') is not None:fields+=('product_spec_snapshot',)
    if item.get('visual_layout_snapshot') is not None:fields+=('visual_layout_snapshot',)
    if item.get('sk_snapshot') is not None:fields+=('sk_snapshot','sk_visual_sequence')
    return object_hash({k:item.get(k) for k in fields})


def validate_grouped_item(item,cfg):
    from duration_tail_policy import validate_item as validate_duration
    validate_duration(item,cfg)
    from core import AssemblyError
    from editing_profiles import grouped,PACKAGE_MATCHING_REVISION
    from grouped_materials import validate_sequence
    from timeline_processing import bake_timeline,timing_digest
    from video_processing import processing_settings
    from voice_edge_fitting import validate as validate_edges
    validate_edges(item,cfg)
    policy=item.get('package_matching_revision')
    if policy is not None or cfg.get('package_matching_revision') is not None:
        if policy!=PACKAGE_MATCHING_REVISION or cfg.get('package_matching_revision')!=policy or cfg.get('package_count_matching_enabled'):
            raise AssemblyError('PACKAGE_POLICY_CHANGED：新方案的包装匹配策略与冻结快照不一致')
        if item.get('package_anchor') or any(c.get('planning_block')=='package' for c in item['clips']):
            raise AssemblyError('PACKAGE_POLICY_CHANGED：新方案不能包含包装锁定环节')
    from video_plan_policy import validate_frozen_item,validate_text_layers
    from ball_scene_rotation import validate as validate_scene_selection
    validate_scene_selection(item,cfg)
    if not grouped(cfg) or item.get('grouped_revision')!=REVISION:
        raise AssemblyError('GROUPED_SNAPSHOT_INVALID：素材格式/冻结版本不一致')
    snapshot=item['coverage_snapshot']
    if snapshot.get('opening_revision'):
        from ball_opening import validate as validate_opening,REVISION as OPENING_REVISION
        if snapshot['opening_revision']!=OPENING_REVISION:
            raise AssemblyError('首镜排除冻结版本无效')
        validate_opening(item['clips'],cfg)
    if snapshot.get('revision')!=REVISION:
        raise AssemblyError('COVERAGE_SNAPSHOT_INVALID：关系版本不一致')
    assets={a['path']:a for a in snapshot['assets']}
    for c in item['clips']:
        asset=assets.get(c['relative_path'])
        if not asset or asset['hash']!=c['hash'] or bool(asset['safe_cut'])!=bool(c.get('safe_cut_confirmed')):
            raise AssemblyError('COVERAGE_FINGERPRINT_CHANGED：'+c['relative_path'])
        if snapshot.get('sk_revision') and (bool(asset.get('can_open'))!=bool(c.get('can_open'))
                or asset.get('scene')!=c.get('sk_scene_group') or asset.get('placement')!=c.get('sk_placement_group')):
            raise AssemblyError('SK分组/首镜资格与冻结快照不一致：'+c['relative_path'])
        if snapshot.get('opening_revision') and bool(asset.get('opening_excluded'))!=bool(c.get('opening_excluded')):
            raise AssemblyError('OPENING_FINGERPRINT_CHANGED：'+c['relative_path'])
    validate_sequence(item['clips'],cfg,snapshot)
    if cfg.get('material_format') == 'sk_diverse':
        from sk_selection import validate as validate_sk
        validate_sk(item,cfg)
    from timeline_processing import compatible_settings,MICRO_TRIM_FIELDS,MICRO_TRIM_REVISION
    if not item.get('voice_fit_revision') and cfg.get('processing_enabled') and cfg.get('processing_micro_trim_enabled') and item.get('micro_trim_revision')!=MICRO_TRIM_REVISION:
        raise AssemblyError('随机微裁未冻结，请重新规划。')
    if not compatible_settings(item['video_processing'],processing_settings(cfg)):
        raise AssemblyError('处理配置与冻结任务不一致')
    if item.get('micro_trim_revision') and item['micro_trim_revision']!=MICRO_TRIM_REVISION:
        raise AssemblyError('随机微裁版本无效')
    expected,transitions,total=bake_timeline(item['clips'],cfg,item['profile_seed'])
    from ball_pacing import validate_timeline as validate_ball_pacing
    validate_ball_pacing(item['clips'],cfg)
    for actual,wanted in zip(item['clips'],expected):
        for key in ('source_in_seconds','source_out_seconds','playback_rate','duration_frames',
                    'timeline_start_frame','source_audio','effects','incoming_transition','outgoing_transition') + (MICRO_TRIM_FIELDS if item.get('micro_trim_revision') else ()):
            if actual.get(key)!=wanted.get(key):
                raise AssemblyError('GROUPED_TIMELINE_CHANGED：'+key)
    if item['total_frames']!=total or item['transitions']!=transitions or abs(item['duration']-total/cfg['fps'])>1e-8:
        raise AssemblyError('GROUPED_TIMELINE_CHANGED：总帧数/转场不一致')
    if item['timeline_digest']!=timing_digest(item) or item['business_digest']!=business_digest(item):
        raise AssemblyError('GROUPED_DIGEST_CHANGED：冻结方案指纹不一致')
    bgm=item['bgm']
    if not 0<=bgm['start']<bgm['end']<=min(bgm['duration'],bgm['approved_end_limit'])+1e-6 or abs(bgm['end']-bgm['start']-item['duration'])>1e-6:
        raise AssemblyError('BGM不足或超过确认范围')
    if not item.get('voice_match_revision'):
        voice=(item.get('voice') or {}).get('effective_voice_duration',0)
        allowed=max(cfg['max_seconds'],voice+max(cfg.get('voice_end_hold_seconds',.8),2))
        if not cfg['min_seconds']-1e-8<=item['duration']<=allowed+1/cfg['fps']:
            raise AssemblyError('VIDEO_TARGET_UNSATISFIABLE：未跟随配音模式的时长越界')
    if item.get('source_audio')!='discard':
        raise AssemblyError('原素材音频必须排除')
    validate_frozen_item(item,cfg);validate_text_layers(item,cfg)
    anchor=item.get('package_anchor')
    if anchor:
        if anchor.get('timestamp_source')!='whisper_known_text_dtw_v1':
            raise AssemblyError('VOICE_ALIGNMENT_UNRELIABLE：包装锚点不是声学对齐')
        allowed=set(anchor['whitelist'])
        locked=[c for c in item['clips'] if c.get('planning_block')=='package']
        if not locked or any(c['relative_path'] not in allowed for c in locked):
            raise AssemblyError('PACKAGE_LOCK_INVALID：包装画面不在冻结名单')
        a,b=anchor['start_frame'],anchor['end_frame']
        intervals=sorted((c['timeline_start_frame'],c['timeline_start_frame']+c['duration_frames']) for c in locked)
        margin=math.floor(float(cfg.get('package_anchor_margin_seconds',.2))*cfg['fps']+1e-7)
        from package_slot_timing import validate_ending_policy
        end_limit=validate_ending_policy(item,cfg)
        if not max(0,a-margin)<=intervals[0][0]<=a or intervals[-1][1]>end_limit:
            raise AssemblyError('PACKAGE_LOCK_DRIFT：包装画面入场/结束超过允许的少量余量')
        if (anchor.get('locked_start_frame',intervals[0][0])!=intervals[0][0] or
                anchor.get('locked_end_frame',intervals[-1][1])!=intervals[-1][1]):
            raise AssemblyError('PACKAGE_LOCK_CHANGED：包装画面实际锁定区间改变')
        end=a
        for x,y in intervals:
            if x>end and end<b:raise AssemblyError('PACKAGE_LOCK_GAP：包装画面未连续覆盖口播')
            end=max(end,y)
        if intervals[0][0]>a or end<b:
            raise AssemblyError('PACKAGE_LOCK_TOO_SHORT：包装画面未覆盖完整锚点')


def plan_grouped_batch(clips,music,ranges,cfg,store,log,stop=None,script_packages=None):
    from core import (APP_VERSION,validate_config,object_hash,now,load_top_texts,
                      music_candidates,choice_weighted,validate_bgm_classification,AssemblyError)
    from dedup_v2 import SelectionIndex,REVISION as DEDUP_REVISION
    from timeline_processing import bake_timeline,timing_digest,TIMELINE_REVISION,AB_DURATION_REVISION
    from video_processing import normalize_processing_config,processing_settings,processing_warnings
    from video_plan_policy import build_schedule,apply_item_timing,REVISION as POLICY
    from voice_duration_matching import fit_windows,target_frames,post_subtitle_tail_frames,REVISION as MATCH
    from voice_pipeline import freeze_script_on_item
    from editing_profiles import new_plan_config,PACKAGE_MATCHING_REVISION
    import runtime_diagnostics as diag
    began=time.perf_counter();cfg=normalize_processing_config(new_plan_config(cfg));validate_config(cfg)
    if script_packages is not None and len(script_packages)!=cfg['count']:
        raise AssemblyError('文案与本批数量不一致')
    from sk_materials import active as sk_active, preflight as sk_preflight, Review as SKReview
    is_sk=sk_active(cfg)
    active=[deepcopy(c) for c in clips if c.get('enabled') and c.get('reviewed')]
    if not active:raise AssemblyError('MATERIAL_REVIEW_REQUIRED：没有已复核的G组素材')
    relations=(SKReview if is_sk else Coverage)(store,cfg['material_root'])
    snapshot=deepcopy(cfg.get('grouped_coverage_snapshot'))
    if snapshot:
        from grouped_materials import root_key
        if snapshot.get('revision')!=REVISION or snapshot.get('root')!=root_key(cfg['material_root']):
            raise GroupedError('COVERAGE_SNAPSHOT_INVALID','启动快照与素材根目录不一致')
        assets={a['path']:a for a in snapshot['assets']}
        for c in active:
            a=assets.get(c['relative_path'])
            if not a or a['hash']!=c['hash']:raise GroupedError('COVERAGE_FINGERPRINT_CHANGED',c['relative_path'])
            c['safe_cut_confirmed']=bool(a['safe_cut'])
            c['opening_excluded']=bool(a.get('opening_excluded'))
            if is_sk:c['can_open']=bool(a.get('can_open'))
    else:
        relations.annotate(active);snapshot=relations.snapshot(active)
    if is_sk:active=sk_preflight(active,cfg)
    from ball_opening import allowed as opening_allowed,validate as validate_opening,order_candidate
    if not any(opening_allowed(c,cfg) for c in active):
        raise GroupedError('BALL_OPENING_UNAVAILABLE','所有已复核素材均禁止作开头，请恢复至少一个首镜；素材仍可用于中后段')
    from ball_scene_rotation import (active as scene_active,capture_config as capture_scene_config,
                                     Selection as SceneSelection,freeze as freeze_scenes)
    cfg=capture_scene_config(cfg,store.root)
    history_info=None
    if is_sk:
        from sk_selection import load_history
        history,history_info=load_history(store,cfg,active)
        log(f'SK历史载入{history_info["load_seconds"]:.3f}秒；滚动窗口{history_info["window_start"]}～{history_info["window_end"]}；成功{history_info["success_records"]}条，预留{history_info["reservation_records"]}条；{history_info["coverage"]}')
    else:history=store.usage()
    dedup=SelectionIndex(active,history,cfg)
    if is_sk:
        from sk_selection import Selection as SKSelection
        dedup=SKSelection(dedup,active,cfg,history_info)
    scene_selection=SceneSelection(dedup,active,cfg) if scene_active(cfg) else None
    if scene_selection:dedup=scene_selection
    musage=Counter(h.get('bgm',{}).get('hash','') for h in history)
    schedule=cfg.get('video_mode_schedule') or build_schedule(cfg)
    if len(schedule)!=cfg['count']:raise AssemblyError('冻结模式数量不一致')
    top_needed=any(a.get('top_text_enabled') for a in schedule)
    texts=load_top_texts(cfg['top_text_library']) if top_needed else []
    if top_needed and not texts:raise AssemblyError('顶部文案为空')
    fixed=int(cfg.get('top_text_fixed_index',0))
    if fixed and not 1<=fixed<=len(texts):raise AssemblyError('固定顶文编号越界')
    seed=str(cfg.get('seed') or uuid.uuid4().hex);rng=random.Random(seed)
    bid='assembly_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    batch=dict(schema_version=1,app_version=APP_VERSION,id=bid,created_at=now(),seed=seed,
        config=deepcopy(cfg),source_audio='discard',scope='manual_grouped_assembly',
        selection_rule_version=REVISION,selection_revision=DEDUP_REVISION,items=[],
        warnings=processing_warnings(cfg),requested_count=cfg['count'],planning_failed_tasks=[],
        voice_failed_tasks=[],script_flow_version='manual_voice_subtitles_v1',
        video_policy_version=cfg.get('video_policy_version'),
        video_mode_distribution=dict(Counter(a['video_mode'] for a in schedule)))
    if is_sk:
        from sk_materials import summary as sk_scan_summary
        batch['sk_scan_summary']=sk_scan_summary(clips)
        from sk_material_bias import inventory
        batch['sk_material_bias_inventory']=inventory(clips,cfg)
        batch['sk_history_coverage']=history_info
    if any(post_subtitle_tail_frames(cfg,a,p or {}) for a,p in zip(schedule,script_packages or [])):
        batch['warnings'].append('配音字幕结束后再显示顶部文案，至少保留2秒真实素材画面；不移动文字位置，不截断口播。')
    whole_block_cache={}
    from grouped_voice_search import REVISION as SEARCH_REVISION,Search
    slot_search=(Search(active,cfg,dedup,snapshot) if cfg.get('grouped_search_revision')==SEARCH_REVISION else None)
    for index,assignment in enumerate(schedule):
        if stop and stop.is_set():break
        number=index+1;job=f'{bid}_{number:03d}';item_start=time.perf_counter()
        package=script_packages[index] if script_packages else None
        with diag.bind(batch_id=bid,job_id=job,number=number):
            target=None;anchor=None;rejected=Counter();fit_diagnostics=[];attempted=0;deadline=None;search_extensions=0
            search_stats=(slot_search.visited,slot_search.history_pruned,slot_search.timing_candidates) if slot_search else None
            try:
                if package and package.get('voice_error'):
                    raise GroupedError('VOICE_FAILED',package['voice_error'])
                narrated=bool(package and package.get('narration_enabled'))
                from duration_tail_policy import active as common_duration, narrated_target
                strict=bool((cfg.get('match_video_to_voice') or common_duration(cfg)) and narrated)
                post_subtitle_frames=post_subtitle_tail_frames(cfg,assignment,package or {})
                target=(target_frames(package['voice'],cfg['fps'])+post_subtitle_frames
                        if strict else assignment['planned_video_frames'])
                if narrated and common_duration(cfg):
                    target=narrated_target(cfg,assignment,package)
                if narrated and not strict:
                    required=math.ceil((package['voice']['effective_voice_duration']+cfg.get('voice_end_hold_seconds',.8))*cfg['fps'])
                    if assignment.get('top_text_enabled') and cfg.get('top_text_timing') in ('auto','after_voice'):
                        required=max(required,math.ceil((package['voice']['effective_voice_duration']+2)*cfg['fps']))
                    if post_subtitle_frames:
                        required=max(required,target_frames(package['voice'],cfg['fps'])+post_subtitle_frames)
                    target=max(target,required)
                tracks=music_candidates(music,ranges,cfg,narration_enabled=narrated)
                tracks=[t for t in tracks if math.floor((t['end_limit']-t['start'])*cfg['fps']+1e-7)>=target]
                if not tracks:raise GroupedError('BGM_INSUFFICIENT','音乐无法覆盖目标时长')
                from voice_edge_fitting import enabled as edge_enabled,prepare as edge_prepare,freeze as freeze_edges
                edge_fit=bool(strict and edge_enabled(cfg))
                best=None;rejected=Counter()
                deadline=time.monotonic()+cfg['plan_seconds_per_video']
                for attempt in range(cfg['attempts_per_video']):
                    if stop and stop.is_set():break
                    if attempt and time.monotonic()>deadline:
                        if slot_search and edge_fit and best is None and search_extensions<2:
                            search_extensions+=1
                            deadline=time.monotonic()+cfg['plan_seconds_per_video']
                            log(f'第{number}条继续选片：第{search_extensions+1}轮，保持原时长、裁切许可和历史去重限制。')
                        else:break
                    attempted=attempt+1
                    plan_seed=f'{seed}:{index}:{attempt}:grouped'
                    try:
                        if slot_search and edge_fit:
                            def trial(candidate):
                                try:
                                    validate_opening(candidate,cfg)
                                    _,trial_out,_,_=fit_windows(candidate,cfg,plan_seed,[('all',target)],edge_fit=True)
                                    from ball_pacing import validate_timeline as validate_ball_pacing
                                    validate_ball_pacing(trial_out,cfg)
                                    if scene_selection and not scene_selection.validate_mix(trial_out):
                                        raise ValueError('BALL_SCENE_DOMINANCE：单条同类画面超过60%，重选镜头')
                                    accepted,why,_,_=dedup.evaluate(dict(id=job,clips=trial_out,output_fps=cfg['fps']),cfg['dedup_mode']=='strict')
                                    if not accepted:
                                        rejected['HISTORY_DEDUP_REJECTED：'+why]+=1;return False
                                    return True
                                except (ValueError,AssemblyError) as exc:
                                    rejected[str(exc)]+=1
                                    return False
                            seq=slot_search.select(target,plan_seed,rng,deadline=deadline,stop=stop,validator=trial)
                        else:
                            seq=_block(active,target,[],'all',cfg,rng,dedup,snapshot,exact=strict,whole_cache=whole_block_cache,edge_fit=edge_fit,deadline=deadline)
                        blocks=[('all',target)]
                        if edge_fit and not slot_search:
                            from complete_action_search import repair_block
                            for block_name,block_target in blocks:
                                if block_name=='package':continue
                                repaired=repair_block(seq,active,block_name,block_target,cfg,plan_seed,snapshot,rng,deadline=deadline)
                                if repaired is not None:seq=repaired
                        seq=order_candidate(seq,cfg)
                        if not is_sk:
                            from editorial import order_sequence
                            seq=order_sequence(seq,cfg,plan_seed,package)
                        if edge_fit:seq=edge_prepare(seq)
                        seq_full,trans_full,total_full=bake_timeline(seq,cfg,plan_seed)
                        keep_full=(not strict and not anchor and cfg['music_mode']=='fade'
                            and target<=total_full<=max(round(cfg['max_seconds']*cfg['fps']),target)
                            and any(math.floor((t['end_limit']-t['start'])*cfg['fps']+1e-7)>=total_full for t in tracks))
                        if keep_full:out,transitions,total=seq_full,trans_full,total_full
                        else:
                            seq,out,transitions,total=fit_windows(seq,cfg,plan_seed,blocks,edge_fit=edge_fit)
                            if total!=target:raise GroupedError('VIDEO_TARGET_UNSATISFIABLE','总帧数不等于目标')
                        from ball_pacing import validate_timeline as validate_ball_pacing
                        validate_ball_pacing(out,cfg)
                        validate_opening(out,cfg)
                        if scene_selection and not scene_selection.validate_mix(out):
                            raise ValueError('BALL_SCENE_DOMINANCE：单条同类画面超过60%，重选镜头')
                        if err:=conflict(out,snapshot):raise GroupedError(*err)
                        accepted,reason,nearest,penalty=dedup.evaluate(
                            dict(id=job,clips=out,output_fps=cfg['fps']),cfg['dedup_mode']=='strict')
                        if not accepted:raise GroupedError('HISTORY_DEDUP_REJECTED',reason)
                        rank=scene_selection.score(out,penalty) if scene_selection else (penalty,)
                        if cfg.get('editorial_enabled'):
                            from editorial import candidate_score
                            merit=candidate_score(out,cfg,package)
                            rank=tuple(rank[:-1])+(rank[-1]+.8*merit,)
                        if best is None or rank<best[0]:
                            best=(rank,(out,transitions,total,plan_seed,reason,nearest),penalty)
                        if best and attempt>=4:break
                    except Exception as exc:
                        if cfg.get('batch_failure_policy')!='skip_recompose_v1' and not isinstance(exc,(ValueError,AssemblyError)):raise
                        if not isinstance(exc,(ValueError,AssemblyError)):
                            batch.setdefault('candidate_errors',[]).append(dict(number=number,attempt=attempt+1,error=str(exc)))
                        rejected[str(exc)]+=1
                        detail=getattr(exc,'fit_diagnostic',None) or getattr(exc,'selection_diagnostic',None)
                        if detail and cfg.get('voice_fit_capacity_revision')=='voice_fit_capacity_v2':
                            fit_diagnostics.append(dict(attempt=attempt+1,seed=plan_seed,detail=deepcopy(detail)))
                            fit_diagnostics=fit_diagnostics[-8:]
                if best is None:
                    detail='；'.join(f'{k}（{v}次）' for k,v in rejected.most_common(4))
                    code='VIDEO_TARGET_UNSATISFIABLE'
                    if cfg.get('voice_fit_capacity_revision')=='voice_fit_capacity_v2':
                        code='VOICE_MATCH_SEARCH_EXHAUSTED'
                        detail=f'在{cfg["plan_seconds_per_video"]*(1+search_extensions):g}秒选片预算内未找到满足全部约束的组合；'+detail
                    raise GroupedError(code,detail or '候选不足，没有放宽覆盖或安全裁切')
                _,(out,transitions,total,plan_seed,reason,nearest),penalty=best
                tracks=[t for t in tracks if math.floor((t['end_limit']-t['start'])*cfg['fps']+1e-7)>=total]
                track=choice_weighted(rng,tracks,[1/(1+musage[t['media']['hash']]) for t in tracks])
                identities=dedup.keys({'clips':out})
                item=dict(id=job,number=number,status='planned',editing_profile_id=cfg.get('editing_profile_id'),
                    grouped_revision=REVISION,selection_rule_version=REVISION,selection_revision=DEDUP_REVISION,
                    package_matching_revision=PACKAGE_MATCHING_REVISION,
                    clips=out,total_frames=total,duration=total/cfg['fps'],output_fps=cfg['fps'],
                    sequence_hash=object_hash(identities),set_hash=object_hash(sorted(identities)),
                    profile_seed=plan_seed,timeline_revision=TIMELINE_REVISION,transitions=transitions,
                    transition_overlap_seconds=sum(t['frames'] for t in transitions)/cfg['fps'],
                    source_duration_sum=sum(c['duration'] for c in out),
                    video_processing=processing_settings(cfg),source_audio='discard',
                    file_export_id=uuid.uuid4().hex if processing_settings(cfg)['hash_refresh'] else '',
                    duration_policy_version=cfg.get('video_policy_version',POLICY),video_mode=assignment['video_mode'],
                    planned_video_frames=assignment['planned_video_frames'],
                    planned_video_duration=assignment['planned_video_duration'],final_video_duration=total/cfg['fps'],
                    top_text_enabled=assignment.get('top_text_enabled',False),
                    top_text_policy=cfg.get('top_text_timing','auto'),top_text_timing=cfg.get('top_text_timing','auto'),
                    narration_enabled=narrated,subtitle_enabled=bool(package and package.get('subtitle_enabled')),
                    bgm_enabled=True,bgm=dict(track['media'],start=track['start'],end=track['start']+total/cfg['fps'],
                        approved_end_limit=track['end_limit'],range_id=track['range_id'],
                        natural=bool(track['natural'] and track['target_frames']==total),note=track['note']),
                    bgm_path=track['media']['path'],bgm_type=track['media'].get('bgm_type'),
                    bgm_rule_version=cfg.get('bgm_rule_version'),package_anchor=anchor,
                    target_account=cfg.get('selection_v2_target_account',''),publication_status='not_marked',
                    warnings=[f'第{i+1}段：{n}' for i,c in enumerate(out) for n in c.get('edit_notes',[])],
                    dedup_detail=dict(nearest=nearest,reason=reason,penalty=penalty,
                                      interval_limit=cfg['selection_v2_interval_limit'],
                                      account_history_status='仅使用已有明确发布记录'))
                if cfg.get('voice_job_temp_dir'):item['voice_temp_version']='job_temp_v1'
                if package:freeze_script_on_item(item,package,cfg)
                if strict:item['voice_match_revision']=MATCH
                if edge_fit:freeze_edges(item,cfg)
                if texts and item['top_text_enabled']:
                    ti=fixed-1 if fixed else index%len(texts) if cfg.get('top_text_mode')=='sequence' else rng.randrange(len(texts))
                    item['top_text']=dict(enabled=True,text=texts[ti],index=ti+1,hash=object_hash(texts[ti]),
                                          source=cfg['top_text_library'])
                else:item['top_text']=dict(enabled=False,text='',windows=[])
                if not edge_fit and cfg.get('processing_enabled') and cfg.get('processing_ab_duration_enabled'):
                    item['random_duration_revision']=AB_DURATION_REVISION
                from timeline_processing import freeze_micro_trim
                freeze_micro_trim(item,cfg)
                # Relations are frozen once per batch; foreground confirmation
                # cannot change a batch already being planned.
                selected_paths={c['relative_path'] for c in out}
                item['coverage_snapshot']=dict(revision=snapshot['revision'],root=snapshot['root'],
                    assets=[deepcopy(a) for a in snapshot['assets'] if a['path'] in selected_paths],
                    pairs=[deepcopy(r) for r in snapshot['pairs'] if r['left'] in selected_paths and r['right'] in selected_paths])
                if is_sk:item['coverage_snapshot']['sk_revision']=snapshot['sk_revision']
                if snapshot.get('opening_revision'):
                    item['coverage_snapshot']['opening_revision']=snapshot['opening_revision']
                apply_item_timing(item,cfg)
                freeze_scenes(item,cfg)
                if is_sk:
                    from sk_selection import freeze as freeze_sk
                    freeze_sk(item,cfg,history_info)
                item['timeline_digest']=timing_digest(item);item['business_digest']=business_digest(item)
                validate_grouped_item(item,cfg);validate_bgm_classification(item,cfg)
                item['planning_diagnostic']=dict(stages={'grouped_selection':time.perf_counter()-item_start},
                    target_frames=target,candidate_count=len(active),safe_cut_count=sum(bool(c.get('safe_cut_confirmed')) for c in active),
                    attempted_candidates=attempt+1,rejection_summary=dict(rejected),
                    package_ending_policy=anchor.get('ending_policy') if anchor else None,
                    block_target_frames=dict(blocks))
                if cfg.get('voice_fit_capacity_revision')=='voice_fit_capacity_v2':
                    item['planning_diagnostic']['capacity_selection_revision']='voice_fit_capacity_v2'
                    item['planning_diagnostic']['recent_rejection_details']=fit_diagnostics
                if slot_search and edge_fit:
                    item['planning_diagnostic'].update(search_revision=SEARCH_REVISION,search_extensions=search_extensions,
                        search_budget_seconds=cfg['plan_seconds_per_video']*(1+search_extensions),
                        visited_states=slot_search.visited-search_stats[0],
                        history_pruned_candidates=slot_search.history_pruned-search_stats[1],
                        exact_timing_trials=slot_search.timing_candidates-search_stats[2])
                batch['items'].append(item);dedup.add(item);musage[item['bgm']['hash']]+=1
                diag.emit('grouped_plan','G组方案通过',package_anchor=anchor,
                          paths=[c['path'] for c in out],coverage_pass=True,
                          voice_match=item.get('voice_match_snapshot'),voice_fit=item.get('voice_fit_snapshot'),**item['planning_diagnostic'])
                log(f'方案{number:02d}：{"SK自由混剪" if is_sk else "G随机"} {item["duration"]:.3f}秒 / {len(out)}段')
                if is_sk:log('SK实际类别时长：'+str(item['sk_snapshot']['category_seconds'])+'；候选拒绝：'+str(dict(rejected)))
            except Exception as exc:
                if cfg.get('batch_failure_policy')!='skip_recompose_v1' and not isinstance(exc,(ValueError,AssemblyError)):raise
                failure=dict(id=job,number=number,status='PLANNING_FAILED',error=str(exc),
                             failure_code=getattr(exc,'code',str(exc).split('：')[0]),
                             script_id=package.get('script_id') if package else None,
                             material_root=cfg['material_root'],candidate_paths=[c['path'] for c in active],
                             target_frames=target,voice_seconds=(package.get('voice') or {}).get('effective_voice_duration') if package else None,
                             package_anchor=anchor,safe_cut_count=sum(bool(c.get('safe_cut_confirmed')) for c in active),
                             rejection_summary=dict(rejected),planning_seconds=time.perf_counter()-item_start)
                if cfg.get('voice_fit_capacity_revision')=='voice_fit_capacity_v2':
                    failure.update(fit_diagnostics=fit_diagnostics,attempted_candidates=attempted,
                        search_budget_seconds=cfg['plan_seconds_per_video']*(1+search_extensions),
                        search_stop_reason='cancelled' if stop and stop.is_set() else 'time_budget' if deadline is not None and time.monotonic()>deadline else 'attempt_limit' if attempted>=cfg['attempts_per_video'] else 'constraint',
                        failure_stage='grouped_selection_voice_fit')
                from batch_error_summary import planning_help
                failure['user_explanation']=planning_help(failure)
                batch['planning_failed_tasks'].append(failure);log(f'第{number}条规划失败：{exc}')
                diag.emit('grouped_plan_failed',str(exc),'ERROR',**{'failure_stage':'grouped_planning',**failure})
    batch['selection_v2_summary']=dedup.summary(batch['items'],{'G':active})
    batch['selection_v2_summary']['bottlenecks']='；'.join(f['error'] for f in batch['planning_failed_tasks'])
    batch['grouped_summary']=dict(files=len(active),combos=sum(c['asset_kind']=='combo' for c in active),
        independent=sum(c['asset_kind']=='independent' for c in active),
        pending_pairs=sum(r['state']=='pending' for r in snapshot['pairs']),
        planning_seconds=time.perf_counter()-began)
    if scene_selection:
        batch['ball_scene_rotation_summary']=scene_selection.scene_summary()
        summary=batch['ball_scene_rotation_summary']
        log(f'牛肉丸全量轮换：{len(active)}个候选，本批使用{summary["selected_identities"]}个不同镜头；'
            '四类画面按整批时长4:3:2:1软平衡，其他画面参与，单条不强制凑齐四类。')
    if is_sk:
        from sk_selection import finalize as finalize_sk
        finalize_sk(batch)
        log('SK类别分布：'+str(batch['sk_summary']['types']))
        log(batch['sk_summary']['deviation_reason'])
    return batch
