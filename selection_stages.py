# -*- coding: utf-8 -*-
"""Five-stage folder selection. Existing manifests keep their saved four-group rules."""
from __future__ import annotations
import runtime_diagnostics as diag

from collections import Counter
import copy
from datetime import datetime
from pathlib import PurePosixPath
import math
import random
import re
import time
import uuid

LEGACY_RULE_VERSION = 'five_stage_v1_20260930'
C_FAMILY_RULE_VERSION = 'five_stage_c_family_v2_20260930'
RULE_VERSION = 'five_stage_c_numeric_v3_20260930'
GROUP_ROTATION_VERSION = 'mutex_member_rotation_v1'
OPENING_DISTRIBUTION_VERSION = 'c_opening_batch_min_v1'


class GroupRotation:
    """Rebuild member turns from the existing success/reservation records."""
    def __init__(self, records, identity, cfg=None):
        self.identity = identity
        self.cfg = cfg
        self.counts = Counter()
        for record in records:
            self.add(record)

    def reference(self, clip):
        group = clip.get('mutex_group')
        if self.cfg and clip.get('relative_path'):
            try:
                current = classify_relative(clip['relative_path'], self.cfg)
            except ValueError:
                current = None
            if current and current['category'] == 'C' and current.get('member_number') is not None:
                return current['mutex_group'], f'member:{current["member_number"]}'
        if clip.get('category') == 'C' and clip.get('member_number') is not None:
            return group, f'member:{clip["member_number"]}'
        return group, self.identity(clip)

    def add(self, item):
        used = {self.reference(c) for c in item.get('clips', []) if c.get('mutex_group')}
        self.counts.update(used)

    def count(self, clip):
        return self.counts[self.reference(clip)]

    def ordered(self, members):
        from core import natural_key
        unique = {}
        for clip in sorted(members, key=lambda c: natural_key(c['relative_path'])):
            unique.setdefault(self.identity(clip), clip)
        def rank(clip):
            member = self.reference(clip)[1]
            number = int(member[7:]) if member.startswith('member:') else None
            return self.count(clip), number is None, number if number is not None else 0, natural_key(clip['relative_path'])
        return sorted(unique.values(), key=rank)


def classify_relative(relative_path, cfg):
    """Classify C opening names without conflating root C members and Cn families."""
    path = PurePosixPath(str(relative_path).replace('\\', '/'))
    parts = path.parts
    reverse = {name: group for group, name in cfg['group_folders'].items()}
    if path.is_absolute() or len(parts) < 2 or parts[0] not in reverse or '..' in parts:
        raise ValueError(f'素材路径不在已配置的五组目录内：{relative_path}')
    category = reverse[parts[0]]
    c_open = category == 'C' and len(parts) >= 3 and parts[1] == '开头'
    if cfg.get('selection_rule_version') == LEGACY_RULE_VERSION:
        group_parts = (parts[:3] if len(parts) >= 4 else None) if c_open else (
            parts[:2] if len(parts) >= 3 else None)
        mutex = '/'.join((category, *group_parts[1:])) if group_parts else 'file:' + '/'.join(parts)
        return {'category': category, 'group': category, 'can_open': category == 'A' or c_open,
                'mutex_group': mutex, 'relative_path': '/'.join(parts)}

    version = cfg.get('selection_rule_version') or RULE_VERSION
    member_number = None
    if c_open:
        folder_numbers = [int(match.group(1)) for part in parts[2:-1]
                          if (match := re.fullmatch(r'C([0-9]+)', part))]
        if len(set(folder_numbers)) > 1:
            raise ValueError(f'C/开头的目录标注了冲突的所属组：{relative_path}')
        named = re.fullmatch(r'C([0-9]+)\s+([0-9]+)', path.stem)
        numeric = re.fullmatch(r'[0-9]+', path.stem)
        if named:
            group_number, member_number = int(named.group(1)), int(named.group(2))
            if folder_numbers and folder_numbers[0] != group_number:
                raise ValueError(f'C/开头的目录与文件名所属组冲突：{relative_path}')
        elif numeric and folder_numbers:
            group_number, member_number = folder_numbers[0], int(path.stem)
        elif numeric and version == RULE_VERSION and len(parts) == 3:
            member_number = int(path.stem)
            mutex = f'file:C/{member_number}'
        else:
            raise ValueError(f'C/开头无法解析所属组和成员编号：{relative_path}')
        if named or folder_numbers:
            mutex = f'C/C{group_number}'
    elif category == 'C' and len(parts) >= 3 and (folder := re.fullmatch(r'C([0-9]+)', parts[1])):
        group_number = int(folder.group(1))
        nested_numbers = [int(match.group(1)) for part in parts[2:-1]
                          if (match := re.fullmatch(r'C([0-9]+)', part))]
        if any(number != group_number for number in nested_numbers):
            raise ValueError(f'C组目录标注了冲突的所属组：{relative_path}')
        named = re.fullmatch(r'C([0-9]+)\s+([0-9]+)', path.stem)
        numeric = re.fullmatch(r'[0-9]+', path.stem)
        if named and int(named.group(1)) != group_number:
            raise ValueError(f'C组目录与文件名所属组冲突：{relative_path}')
        member_number = int(named.group(2)) if named else int(path.stem) if numeric else None
        mutex = f'C/C{group_number}'
    else:
        group_parts = parts[:2] if len(parts) >= 3 else None
        mutex = '/'.join((category, *group_parts[1:])) if group_parts else 'file:' + '/'.join(parts)
        if category == 'C' and len(parts) == 2 and version == RULE_VERSION and re.fullmatch(r'[0-9]+', path.stem):
            member_number = int(path.stem)
            mutex = f'file:C/{member_number}'
    return {'category': category, 'group': category, 'can_open': category == 'A' or c_open,
            'mutex_group': mutex, 'member_number': member_number,
            'relative_path': '/'.join(parts)}


def validate_stage_sequence(clips, cfg):
    from core import AssemblyError
    if len(clips) < 2:
        raise AssemblyError('至少需要首镜和E结尾。')
    ids, hashes, mutexes = set(), set(), set()
    state = 'AB'
    for i, clip in enumerate(clips):
        try:
            expected = classify_relative(clip['relative_path'], cfg)
        except (KeyError, ValueError) as exc:
            raise AssemblyError(f'第{i+1}段缺少可信的五组路径快照：{exc}') from exc
        fields = ('category', 'group', 'can_open', 'mutex_group')
        if cfg.get('selection_rule_version') in (C_FAMILY_RULE_VERSION, RULE_VERSION):
            fields += ('member_number',)
        for key in fields:
            if clip.get(key) != expected[key]:
                raise AssemblyError(f'第{i+1}段的{key}与保存的目录层级不一致。')
        key, digest, mutex = clip['identity_key'], clip['hash'], clip['mutex_group']
        if key in ids or digest in hashes:
            raise AssemblyError('同一条成片不能重复使用同素材或相同内容。')
        if mutex in mutexes:
            raise AssemblyError(f'同一条成片不能重复使用互斥组：{mutex}')
        ids.add(key); hashes.add(digest); mutexes.add(mutex)
        category = clip['category']
        if i == 0:
            if not clip['can_open'] or category not in ('A', 'C'):
                raise AssemblyError('第一镜只能来自A或C/开头。')
            if clip.get('timeline_role') != ('首镜A' if category=='A' else '首镜C/开头'):
                raise AssemblyError('首镜角色快照与实际位置不一致。')
            # First-frame C is an opening exception, not entry into C stage.
            continue
        if i == len(clips)-1:
            if category != 'E': raise AssemblyError('最后一镜必须来自E。')
            if clip.get('timeline_role') != 'E结尾': raise AssemblyError('E结尾角色快照不一致。')
            continue
        role = {'A':'A/B前段','B':'A/B前段','C':'C展示','D':'D过渡'}.get(category)
        if clip.get('timeline_role') != role:
            raise AssemblyError(f'第{i+1}段的阶段角色快照不一致。')
        if category in ('A', 'B'):
            if state != 'AB': raise AssemblyError('进入C或D后不能返回A/B。')
        elif category == 'C':
            if state == 'D': raise AssemblyError('D后只能直接接E。')
            state = 'C'
        elif category == 'D':
            if state == 'D': raise AssemblyError('D最多一条，后面只能接E。')
            state = 'D'
            if i != len(clips)-2: raise AssemblyError('D后只能直接接E。')
        else:
            raise AssemblyError('E只能放在最后。')


def rotate_selected_members(seq, roles, pools, cfg, seed, lo, hi, deadline, dedup, rotation, item_id):
    """Keep the selected groups, then choose their least-used feasible members."""
    from core import AssemblyError
    from timeline_processing import bake_timeline
    notes = []
    reuse_cost = 0.
    for i, role in enumerate(roles):
        pool = (pools['A']+pools['B'] if role == 'AB' else
                pools['C_OPEN'] if role == 'OPEN_C' else
                pools['A'] if role == 'OPEN_A' else pools[role])
        group = seq[i]['mutex_group']
        members = rotation.ordered([c for c in pool if c['mutex_group'] == group])
        if not members:
            continue
        original = seq[i]
        if len(members) == 1:
            if rotation.count(original):
                notes.append(f'{group}复用{original["relative_path"]}：当前角色只有这一名已启用、已复核的有效成员。')
                reuse_cost += 1
            continue
        selected = None
        blocked = []
        for member in members:
            if time.monotonic() >= deadline:
                blocked.append('搜索时间已到')
                break
            proposed = list(seq)
            proposed[i] = dict(member, timeline_role=original['timeline_role'])
            if original.get('voice_fit_revision'):proposed[i]['voice_fit_revision']=original['voice_fit_revision']
            try:
                output, _, total = bake_timeline(proposed, cfg, seed)
                if not lo <= total <= hi:
                    blocked.append('时长/BGM不匹配')
                    continue
                validate_stage_sequence(output, cfg)
                accepted, reason, _, _ = dedup.evaluate(
                    {'id':item_id, 'clips':output, 'output_fps':cfg['fps']},
                    cfg['dedup_mode'] == 'strict')
                if not accepted:
                    blocked.append(reason)
                    continue
            except AssemblyError as exc:
                blocked.append(str(exc))
                continue
            seq = proposed
            selected = member
            break
        if selected is None:
            selected = original
        count = rotation.count(selected)
        least = rotation.count(members[0])
        if count > least:
            reason = '、'.join(dict.fromkeys(blocked)) or '其他成员未形成合格方案'
            notes.append(f'{group}复用{selected["relative_path"]}：较少使用的成员未通过本条条件（{reason}）。')
            reuse_cost += 1 + count-least
        elif count and all(rotation.count(member) >= count for member in members):
            notes.append(f'{group}按自然编号进入下一轮：同组当前启用复核的成员此前均已使用。')
    return seq, notes, reuse_cost


@diag.measured("selection","planner")
def plan_stage_batch(clips, music, ranges, cfg, store, log, stop=None, script_packages=None):
    from core import (APP_VERSION, AssemblyError, object_hash, validate_config, validate_bgm_classification,
                      music_candidates, choice_weighted, load_top_texts, now)
    from dedup_v2 import REVISION as DEDUP_REVISION, SelectionIndex
    from timeline_processing import (_search_slots, build_slot_profiles, bake_timeline,
                                     timing_digest, validate_timing_item, TIMELINE_REVISION,
                                     AB_DURATION_REVISION, TIMELINE_DEFAULTS)
    from video_processing import (normalize_processing_config, processing_settings,
                                  processing_warnings, parse_positions)
    from voice_pipeline import freeze_script_on_item
    from video_plan_policy import REVISION as VIDEO_POLICY_REVISION, build_schedule, apply_item_timing
    cfg = normalize_processing_config(cfg)
    from voice_duration_matching import resolve_schedule,fit_windows,REVISION as MATCH_REVISION
    cfg=resolve_schedule(cfg,script_packages)
    validate_config(cfg)
    if script_packages is not None and len(script_packages) != cfg['count']:
        raise AssemblyError('文案与本批生成数量不一致。')
    if cfg.get('selection_rule_version') != RULE_VERSION:
        raise AssemblyError('旧版五组规则仅供已保存批次继续执行；新方案请使用当前C组归属规则。')
    active = [dict(c) for c in clips if c.get('enabled') and c.get('reviewed')]
    pools = {group:[c for c in active if c['category']==group] for group in 'ABCDE'}
    pools['C_OPEN'] = [c for c in pools['C'] if c['can_open']]
    if not (pools['A'] or pools['C_OPEN']):
        raise AssemblyError('缺少有效首镜：需要A或C/开头中已启用、已复核的素材。')
    if not pools['E']: raise AssemblyError('缺少有效E结尾素材。')
    from video_plan_policy import LEGACY_REVISION, PREVIOUS_REVISION, MIN_POST_VOICE_TOP_SECONDS
    new_policy = cfg.get('video_policy_version') in (VIDEO_POLICY_REVISION, PREVIOUS_REVISION, LEGACY_REVISION)
    natural_tracks = (music_candidates(music, ranges, cfg)
                      if new_policy and cfg['music_mode'] == 'natural' else [])
    instrumental_tracks = (music_candidates(music, ranges, cfg, narration_enabled=True)
                           if new_policy and cfg['music_mode'] == 'natural' else [])
    schedule = (cfg.get('video_mode_schedule') or
                build_schedule(cfg, {'all':[track['target_frames'] for track in natural_tracks],
                                     'instrumental':[track['target_frames'] for track in instrumental_tracks]}
                               if cfg['music_mode'] == 'natural' else None)) if new_policy else None
    if new_policy and len(schedule) != cfg['count']:
        raise AssemblyError('已冻结的视频模式分配数量与生成数量不一致。')
    tracks = music_candidates(music, ranges, cfg) if not new_policy else []
    if not new_policy and not tracks: raise AssemblyError('没有满足目标时长的音乐区间。')
    top_needed = (any(entry.get('top_text_enabled', entry.get('top_text_allocated', False))
                      for entry in schedule) if new_policy
                  else cfg.get('top_text_enabled'))
    texts = load_top_texts(cfg['top_text_library']) if top_needed else []
    fixed = int(cfg.get('top_text_fixed_index',0) or 0)
    if top_needed and not texts: raise AssemblyError('顶部文案为空。')
    if fixed and not 1 <= fixed <= len(texts): raise AssemblyError('固定文案编号超出范围。')
    history = store.usage()
    dedup = SelectionIndex(active, history, cfg)
    rotation = GroupRotation(history, dedup.identity, cfg)
    musage = Counter(h.get('bgm',{}).get('hash','') for h in history)
    tusage = Counter(h.get('top_text',{}).get('hash','') for h in history)
    used_text = set()
    seed = str(cfg.get('seed') or uuid.uuid4().hex)
    rng = random.Random(seed)
    bid = 'assembly_'+datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    settings = processing_settings(cfg)
    batch_config = copy.deepcopy(cfg)
    if cfg.get('video_policy_version') == VIDEO_POLICY_REVISION:
        for key in ('script_mode', 'post_voice_policy', 'post_voice_plain_percent',
                    'post_voice_top_percent', 'post_voice_top_text_ratio',
                    'post_voice_clean_ratio', 'post_voice_random_mode'):
            batch_config.pop(key, None)
    batch = {'schema_version':1,'app_version':APP_VERSION,'selection_rule_version':RULE_VERSION,
             'id':bid,'created_at':now(),'config':batch_config,'seed':seed,
             'source_audio':'discard','scope':'manual_whole_clip_assembly','items':[],
             'warnings':processing_warnings(cfg),'requested_count':cfg['count'],
             'approval_note':'五阶段组合只修改新方案，不更改原素材或正式资格。'}
    if script_packages is not None:
        batch['script_flow_version'] = 'manual_voice_subtitles_v1'
        batch['voice_failed_tasks'] = []
    if new_policy:
        batch['video_policy_version'] = cfg['video_policy_version']
        batch['video_mode_distribution'] = dict(Counter(entry['video_mode'] for entry in schedule))
        top_count_key = ('top_text_enabled_count' if cfg['video_policy_version'] == VIDEO_POLICY_REVISION
                         else 'top_text_allocated_count')
        batch[top_count_key] = sum(entry.get('top_text_enabled',
                                             entry.get('top_text_allocated', False))
                                   for entry in schedule)
    c_open_target = int(cfg.get('opening_c_min_per_batch', 0))
    c_open_accepted = 0
    batch['opening_distribution_version'] = OPENING_DISTRIBUTION_VERSION
    skipped = sum(c.get('enabled') and not c.get('reviewed') for c in clips)
    if skipped: batch['warnings'].append(f'已排除{skipped}条启用但未复核的素材。')
    minf = math.ceil(cfg['min_seconds']*cfg['fps'])
    maxf = math.floor(cfg['max_seconds']*cfg['fps'])
    preferred = (cfg['preferred_min']+cfg['preferred_max'])/2*cfg['fps']
    unique_mutex = len({c['mutex_group'] for c in active})
    maxn = min(cfg['max_clips'],unique_mutex,len({c['identity_key'] for c in active}))
    without_d = [c for c in active if c['category'] != 'D']
    without_d_maxn = min(cfg['max_clips'],len({c['mutex_group'] for c in without_d}),
                         len({c['identity_key'] for c in without_d}))
    without_d_average = sum(c['duration'] for c in without_d)/len(without_d)

    def weight(c, i, n):
        role = 'first' if i == 0 else 'last' if i == n-1 else 'ordinary'
        return dedup.rotation_weight(c, role)

    for index in range(cfg['count']):
        if stop and stop.is_set(): break
        try:
            selection_started=time.monotonic()
            with diag.bind(batch_id=bid,job_id=f'{bid}_{index+1:03d}',number=index+1,attempt_id=uuid.uuid4().hex):
                with diag.stage('selection','planner'):
                    script = script_packages[index] if script_packages is not None else None
                    if cfg.get('batch_failure_policy')=='skip_recompose_v1' and script and script.get('voice_error'):
                        raise AssemblyError('VOICE_FAILED：'+script['voice_error'])
                    assignment = schedule[index] if new_policy else None
                    voice_minf = (math.ceil(script['voice']['effective_voice_duration']*cfg['fps'] - 1e-7)
                                  if script and script['narration_enabled'] and script.get('voice') else minf)
                    from duration_tail_policy import active as common_duration, narrated_target
                    strict_voice=bool((cfg.get('match_video_to_voice') or common_duration(cfg)) and script and script.get('voice')
                                      and script.get('narration_enabled'))
                    fit_target=(narrated_target(cfg,assignment,script)
                                if strict_voice and common_duration(cfg) else voice_minf)
                    if new_policy:
                        planned_frames = assignment['planned_video_frames']
                        hold_frames = math.ceil(float(cfg['voice_end_hold_seconds'])*cfg['fps'] - 1e-7)
                        required_frames = voice_minf + hold_frames if voice_minf > planned_frames else voice_minf
                        if (not strict_voice and cfg.get('video_policy_version') == VIDEO_POLICY_REVISION and
                                assignment['video_mode'] == '配音+字幕+BGM' and
                                assignment.get('top_text_enabled') and
                                assignment.get('top_text_policy') in ('auto', 'after_voice') and
                                script and script.get('voice')):
                            required_frames = max(required_frames, voice_minf +
                                                  math.ceil(MIN_POST_VOICE_TOP_SECONDS*cfg['fps']-1e-7))
                        if strict_voice:required_frames=fit_target
                        maxf_item = max(maxf, required_frames)
                        desired_frames = max(planned_frames, required_frames)
                        track_cfg = dict(cfg, max_seconds=maxf_item/cfg['fps'])
                        tracks_this = [track for track in music_candidates(
                                       music, ranges, track_cfg, narration_enabled=bool(script and script['narration_enabled']))
                                       if ((track['target_frames'] >= required_frames) if track['natural'] else
                                           math.floor((track['end_limit']-track['start'])*cfg['fps']+1e-7)
                                           >= required_frames)]
                        if not strict_voice and cfg['music_mode'] == 'natural' and required_frames <= planned_frames:
                            tracks_this = [track for track in tracks_this
                                           if track['target_frames'] == planned_frames]
                        if not tracks_this:
                            if script and script['narration_enabled'] and cfg.get('bgm_rule_version'):
                                raise AssemblyError('NO_SUITABLE_INSTRUMENTAL_BGM：当前视频包含配音，但纯音乐库中没有符合条件的BGM。')
                            raise AssemblyError(f'第{index+1}条没有可覆盖{desired_frames/cfg["fps"]:.2f}秒的合格BGM区间。')
                    else:
                        required_frames = voice_minf
                        maxf_item = maxf
                        desired_frames = (math.ceil(script['target_video_duration']*cfg['fps'] - 1e-7)
                                          if script and script['narration_enabled'] and script.get('voice') else preferred)
                        tracks_this = tracks
                    if not new_policy and voice_minf > maxf:
                        raise AssemblyError(f'{script["script_id"]}配音长于当前最长视频时长；请调整时长档位，不截断配音。')
                    started = time.monotonic()
                    deadline = started+cfg['plan_seconds_per_video']
                    best = None; good = 0; rejected = Counter()
                    prioritize_c_open = c_open_accepted < c_open_target and bool(pools['C_OPEN'])
                    # Each search pattern gets a normal no-D route and, when possible, a D route.
                    # Keep the configured candidate budget (rounded up once for a complete pair).
                    variants = 2 if pools['D'] and maxn >= 3 else 1
                    attempts = max(variants, int(cfg['attempts_per_video']))
                    if attempts % variants: attempts += variants - attempts % variants
                    c_phase_attempts = max(variants, attempts//(2*variants)*variants)
                    c_phase_deadline = started + cfg['plan_seconds_per_video']/2
                    for attempt in range(attempts):
                        try:
                            if time.monotonic() >= deadline or (stop and stop.is_set()): break
                            if attempt % variants == 0:
                                track = choice_weighted(rng,tracks_this,[
                                    1/(1+musage[t['media']['hash']]) *
                                    (1/(1+abs(t['target_frames']-desired_frames)/cfg['fps'])
                                     if script and script['narration_enabled'] and t['natural'] else 1)
                                    for t in tracks_this])
                                if strict_voice:
                                    track=dict(track,natural=False,target_frames=None,
                                               note=track['note']+'；严格跟随实际配音，按确认区间裁尾淡出')
                                lo = hi = track['target_frames'] if track['natural'] else 0
                                if not track['natural']:
                                    lo = max(minf, required_frames)
                                    hi = min(maxf_item,math.floor((track['end_limit']-track['start'])*cfg['fps']+1e-7))
                                elif script and script['narration_enabled']:
                                    lo = max(lo, required_frames)
                                if strict_voice:
                                    lo=fit_target
                                    # Stage structure can require several complete clips even
                                    # when voice is short. Search them before the opt-in fitter.
                                    from voice_edge_fitting import enabled as edge_enabled
                                    hi=fit_target+(math.floor(float(cfg.get('voice_fit_max_excess_seconds',3))*cfg['fps']) if edge_enabled(cfg) else math.ceil(sum(sorted((c['duration'] for c in active),reverse=True)[:maxn])*cfg['fps']))
                                target = min(hi,max(lo,desired_frames if new_policy or script and script['narration_enabled'] else preferred))
                                if hi < lo or without_d_maxn < 2: break
                                if prioritize_c_open and pools['A']:
                                    open_role = ('OPEN_C' if attempt < c_phase_attempts and time.monotonic() < c_phase_deadline
                                                 else 'OPEN_A')
                                elif prioritize_c_open:
                                    open_role = 'OPEN_C'
                                else:
                                    open_role = choice_weighted(rng,
                                        [x for x in ('OPEN_A','OPEN_C') if pools['A' if x=='OPEN_A' else 'C_OPEN']],
                                        [len(pools['A' if x=='OPEN_A' else 'C_OPEN']) for x in ('OPEN_A','OPEN_C')
                                         if pools['A' if x=='OPEN_A' else 'C_OPEN']])
                                expected_without_d = max(2,target/cfg['fps']/max(.5,without_d_average))
                                no_d_sizes = list(range(2,without_d_maxn+1))
                                n_without_d = choice_weighted(rng,no_d_sizes,
                                    [1/(1+abs(x-expected_without_d))**1.5 for x in no_d_sizes])
                                if variants == 2:
                                    average_with_d = sum(c['duration'] for c in active)/len(active)
                                    expected_with_d = max(3,target/cfg['fps']/max(.5,average_with_d))
                                    d_sizes = list(range(3,maxn+1))
                                    n_with_d = choice_weighted(rng,d_sizes,
                                        [1/(1+abs(x-expected_with_d))**1.5 for x in d_sizes])
                            use_d = bool(variants == 2 and attempt % variants == 1)
                            n = n_with_d if use_d else n_without_d
                            max_c = min(len({c['mutex_group'] for c in pools['C']}),n-2-int(use_d))
                            c_count = rng.randint(0,max_c)
                            ab_count = n-2-int(use_d)-c_count
                            if ab_count and not (pools['A'] or pools['B']):
                                rejected['A/B前段候选不足'] += 1; continue
                            roles = [open_role]+['AB']*ab_count+['C']*c_count+(['D'] if use_d else [])+['E']
                            phase_deadline = (min(deadline, c_phase_deadline)
                                              if prioritize_c_open and pools['A'] and open_role == 'OPEN_C' else deadline)
                            profile_seed = f'{seed}:{index}:{attempt//variants}:{"with-d" if use_d else "without-d"}'
                            eligible = set(range(1,1+ab_count)) | ({0} if open_role=='OPEN_A' else set())
                            from voice_edge_fitting import enabled as edge_enabled,prepare as edge_prepare,freeze as freeze_edges
                            edge_fit=bool(strict_voice and edge_enabled(cfg))
                            profiles,requests = build_slot_profiles(n,cfg,profile_seed,eligible,voice_fitting=edge_fit)
                            profiles[0]['first_slot'] = True
                            seq = _search_slots(pools,n,c_count,profiles,requests,cfg,rng,weight,
                                                lo,hi,target,phase_deadline,True,roles)
                            if not seq:
                                rejected['时长、阶段、互斥或有限搜索预算'] += 1; continue
                            role_names = (['首镜A' if open_role=='OPEN_A' else '首镜C/开头']+
                                          ['A/B前段']*ab_count+['C展示']*c_count+
                                          (['D过渡'] if use_d else [])+['E结尾'])
                            seq = [dict(c,timeline_role=role) for c,role in zip(seq,role_names)]
                            if edge_fit:seq=edge_prepare(seq)
                            seq, rotation_notes, rotation_cost = rotate_selected_members(
                                seq, roles, pools, cfg, profile_seed, lo, hi, phase_deadline, dedup, rotation,
                                f'{bid}_{index+1:03d}')
                            identities = dedup.keys({'clips':seq})
                            sh,sth = object_hash(identities),object_hash(sorted(identities))
                            out,transitions,total = bake_timeline(seq,cfg,profile_seed)
                            if strict_voice:
                                try:seq,out,transitions,total=fit_windows(seq,cfg,profile_seed,[('all',fit_target)],edge_fit=edge_fit)
                                except AssemblyError as exc:
                                    rejected[str(exc)]+=1;continue
                            if not lo <= total <= hi:
                                rejected['实际取窗后时长或BGM区间'] += 1; continue
                            try: validate_stage_sequence(out,cfg)
                            except AssemblyError as exc:
                                rejected[str(exc)] += 1; continue
                            accepted,reason,nearest,penalty = dedup.evaluate(
                                {'id':f'{bid}_{index+1:03d}','clips':out,'output_fps':cfg['fps']},
                                cfg['dedup_mode']=='strict')
                            if not accepted:
                                rejected[reason] += 1; continue
                            duration = total/cfg['fps']
                            w = cfg['selection_v2_weights']
                            duration_penalty = (abs(duration-desired_frames/cfg['fps']) * 2
                                                if new_policy or script and script['narration_enabled'] else
                                                max(cfg['preferred_min']-duration,0,duration-cfg['preferred_max'])*2)
                            score = (duration_penalty+
                                     musage[track['media']['hash']]*.5+penalty+rng.random()*.1+
                                     sum(w['batch']*dedup.batch[dedup.identity(c)] for c in seq)/len(seq))
                            score += w['role']*(dedup.first[dedup.identity(seq[0])]+dedup.last[dedup.identity(seq[-1])])
                            score += rotation_cost
                            if use_d:
                                d_key = dedup.identity(seq[-2])
                                score += math.log1p(w['recent']*dedup.recent[d_key]+w['total']*dedup.total[d_key]+
                                                    w['role']*dedup.ordinary[d_key])
                            overlap_seconds = sum(t['frames'] for t in transitions)/cfg['fps']
                            continuous = sum((c['source_out_seconds']-c['source_in_seconds'])/c['playback_rate'] for c in out)
                            item = {'id':f'{bid}_{index+1:03d}','number':index+1,'status':'planned',
                                    'selection_rule_version':RULE_VERSION,'selection_revision':DEDUP_REVISION,
                                    'sequence_hash':sh,'set_hash':sth,'clips':out,'total_frames':total,
                                    'duration':duration,'source_duration_sum':sum(c['duration'] for c in seq),
                                    'frame_rounding_seconds':duration+overlap_seconds-continuous,
                                    'transition_overlap_seconds':overlap_seconds,'transitions':transitions,
                                    'profile_seed':profile_seed,'timeline_revision':TIMELINE_REVISION,
                                    'video_processing':settings,'output_fps':cfg['fps'],
                                    'file_export_id':uuid.uuid4().hex if settings['hash_refresh'] else '',
                                    'bgm':dict(track['media'],start=track['start'],end=track['start']+duration,
                                               approved_end_limit=track['end_limit'],range_id=track['range_id'],
                                               natural=track['natural'],note=track['note']),
                                    'source_audio':'discard','warnings':[f"第{i+1}段：{note}" for i,c in enumerate(out)
                                                                       for note in c['edit_notes']]+rotation_notes,
                                    'group_rotation_revision':GROUP_ROTATION_VERSION,
                                    'target_account':str(cfg.get('selection_v2_target_account','')).strip(),
                                    'publication_status':'not_marked',
                                    'dedup_detail':{'nearest':nearest,'reason':reason,'penalty':round(penalty,4),
                                                    'interval_limit':cfg['selection_v2_interval_limit'],
                                                    'comparison_scope':'本批预留及全部可读历史',
                                                    'account_history_status':('未检查账号发布历史' if not cfg.get('selection_v2_target_account')
                                                                              else '仅检查明确标记的同账号已发布记录'),
                                                    'denominator':'各自片段播放时长之和，未扣转场重叠'}}
                            if script:
                                freeze_script_on_item(item, script, cfg)
                            item['editing_profile_id']=cfg.get('editing_profile_id','fresh_beef_standard')
                            if strict_voice:item['voice_match_revision']=MATCH_REVISION
                            if edge_fit:freeze_edges(item,cfg)
                            if new_policy:
                                item.update(duration_policy_version=cfg['video_policy_version'],
                                            video_mode=assignment['video_mode'],
                                            planned_video_duration=assignment['planned_video_duration'],
                                            planned_video_frames=assignment['planned_video_frames'],
                                            final_video_duration=duration,
                                            top_text_timing=assignment['top_text_timing'],
                                            narration_enabled=bool(script and script['narration_enabled']),
                                            subtitle_enabled=bool(script and script['subtitle_enabled']))
                                if cfg['video_policy_version'] == LEGACY_REVISION:
                                    item.update(top_text_allocated=assignment['top_text_allocated'],
                                                post_voice_mode=assignment['post_voice_mode'])
                                else:
                                    item['top_text_enabled'] = assignment['top_text_enabled']
                                    if cfg['video_policy_version'] == VIDEO_POLICY_REVISION:
                                        item['top_text_policy'] = assignment['top_text_policy']
                            if not edge_fit and cfg.get('processing_enabled') and cfg.get('processing_ab_duration_enabled'):
                                item['random_duration_revision'] = AB_DURATION_REVISION
                                item['random_duration_seed'] = profile_seed
                                item['random_duration_parameters'] = {k:cfg.get(k,v) for k,v in TIMELINE_DEFAULTS.items()
                                                                      if k.startswith('processing_ab_duration_')}
                                if cfg.get('processing_ab_duration_scope')=='positions':
                                    for pos in sorted(parse_positions(cfg.get('processing_ab_duration_positions',''))):
                                        if pos <= len(out) and out[pos-1]['group'] not in ('A','B'):
                                            item['warnings'].append(f'第{pos}段属于{out[pos-1]["group"]}组，跳过A/B随机时长。')
                            from timeline_processing import freeze_micro_trim
                            freeze_micro_trim(item,cfg)
                            item['timeline_digest'] = timing_digest(item)
                            if not strict_voice:validate_timing_item(item,cfg)
                            good += 1
                            rank = (0 if prioritize_c_open and out[0]['category'] == 'C' else 1, score)
                            if cfg.get('editorial_enabled'):
                                from editorial import candidate_score
                                rank=(rank[0],round(score,1),candidate_score(out,cfg,script),score)
                            if best is None or rank < best[0]: best = (rank,item)
                            if good >= 5 and attempt % variants == variants-1: break
                        except Exception as exc:
                            if cfg.get('batch_failure_policy')!='skip_recompose_v1':raise
                            batch.setdefault('candidate_errors',[]).append(dict(number=index+1,attempt=attempt+1,error=str(exc)))
                            rejected[str(exc)]+=1
                            log(f'第{index+1}条候选异常，尝试重新组合：{exc}')
                            continue
                    if best is None:
                        main = ', '.join(f'{k} {v}次' for k,v in rejected.most_common(3)) or '候选不足'
                        batch['warnings'].append(f'限定搜索预算内仅找到{len(batch["items"])}/{cfg["count"]}条；主要瓶颈：{main}。可增加合格素材、音乐区间或单条搜索秒数，阶段和互斥规则未放宽。')
                        if cfg.get('batch_failure_policy')=='skip_recompose_v1':raise AssemblyError(main)
                        break
                    item = best[1]
                    if cfg.get('bgm_rule_version'):
                        item['bgm_path'] = item['bgm']['path']
                        item['bgm_type'] = item['bgm']['bgm_type']
                        item['bgm_rule_version'] = cfg['bgm_rule_version']
                        item['voice_temp_version'] = 'job_temp_v1'
                    if texts and (not new_policy or assignment.get('top_text_enabled',
                                                                   assignment.get('top_text_allocated', False))):
                        if fixed: ti = fixed-1
                        elif cfg.get('top_text_mode')=='sequence': ti = index%len(texts)
                        else:
                            choices = [i for i,t in enumerate(texts) if object_hash(t) not in used_text] or list(range(len(texts)))
                            ti = choice_weighted(rng,choices,[1/(1+tusage[object_hash(texts[i])]) for i in choices])
                        th = object_hash(texts[ti]);used_text.add(th);tusage[th] += 1
                        item['top_text']={'enabled':True,'text':texts[ti],'index':ti+1,'hash':th,
                                          'source':str(cfg['top_text_library'])}
                    else:
                        item['top_text']={'enabled':False,'text':'','index':0,'hash':'',
                                          'source':cfg.get('top_text_library','')}
                    if new_policy:
                        apply_item_timing(item, cfg)
                        validate_timing_item(item, cfg)
                    validate_bgm_classification(item, cfg)
                    item['planning_diagnostic']={'run_id':diag.hub().run_id if diag.hub() else None,'stages':{'selection':time.monotonic()-selection_started}}
                    from visual_layout import freeze as freeze_visual_layout
                    freeze_visual_layout(item,cfg)
                    batch['items'].append(item)
                    c_open_accepted += item['clips'][0]['category'] == 'C'
                    dedup.add(item)
                    rotation.add(item)
                    musage[item['bgm']['hash']] += 1
                    log(f"方案{index+1:02d}：{item['duration']:.3f}秒 / {len(item['clips'])}段 / {' → '.join(c['timeline_role'] for c in item['clips'])}")
        except Exception as exc:
            if cfg.get('batch_failure_policy')!='skip_recompose_v1':raise
            batch.setdefault('planning_failed_tasks',[]).append(dict(number=index+1,status='PLANNING_FAILED',error=str(exc),failure_code='VOICE_FAILED' if str(exc).startswith('VOICE_FAILED') else 'PLANNING_FAILED',script_id=(script_packages[index] or {}).get('script_id') if script_packages else None))
            log(f'第{index+1}条组合失败，已记录并继续后续：{exc}')
    if not batch['items'] and not batch.get('voice_failed_tasks') and not batch.get('planning_failed_tasks'):
        raise AssemblyError(batch['warnings'][-1] if batch['warnings'] else '没有找到合格方案。')
    voice_failed_count = len(batch.get('voice_failed_tasks', [])) + sum(
        item.get('status') == 'VOICE_FAILED' for item in batch['items'])
    if voice_failed_count:
        batch['warnings'].append(f'{voice_failed_count}篇文案配音失败，状态VOICE_FAILED；画面方案已冻结，未降级为无配音视频。')
    batch['selection_revision']=DEDUP_REVISION
    batch['selection_v2_summary']=dedup.summary(batch['items'],{k:pools[k] for k in 'ABCDE'})
    batch['selection_v2_summary']['bottlenecks']=((batch['warnings'][-1] if batch['warnings'] else '；'.join(f['error'] for f in batch.get('planning_failed_tasks',[]))) if len(batch['items'])<cfg['count']
                                                   else '已达到目标数量；未放宽阶段或互斥规则。')
    batch['stage_summary']={'category_counts':dict(Counter(c['category'] for c in active)),
                            'open_candidates':len(pools['A'])+len(pools['C_OPEN']),
                            'open_from_a':len(pools['A']),'open_from_c':len(pools['C_OPEN']),
                            'c_open_min_requested':c_open_target,'c_open_actual':c_open_accepted}
    if c_open_accepted < c_open_target:
        reason = ('没有已启用、已复核的C/开头候选' if not pools['C_OPEN'] else
                  '现有C/开头候选在时长、互斥、去重或单条搜索预算内未能形成足够方案')
        batch['warnings'].append(
            f'C/开头首镜仅找到{c_open_accepted}/{c_open_target}条：{reason}；未放宽限制。')
    return batch
