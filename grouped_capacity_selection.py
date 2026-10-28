"""Opt-in, bounded G-slot selection using conservative duration/crop capacity.

The intervals here only reject impossible candidates and rank complete sources.
They never approve a timeline: the caller must run bake_timeline, the shared
voice fitter, coverage validation and history dedup on the complete sequence.
"""
import heapq
import math
import time

from grouped_materials import GroupedError, pair_key

REVISION = 'voice_fit_capacity_v2'
MAX_NODES = 800


def active(cfg):
    return cfg.get('voice_fit_capacity_revision') == REVISION


def _speed_bounds(cfg):
    if not (cfg.get('processing_enabled') and cfg.get('processing_speed_enabled')):
        return 1., 1., 1.
    if cfg.get('processing_speed_values', 'fixed') == 'random':
        lo = float(cfg.get('processing_speed_min', .95))
        hi = float(cfg.get('processing_speed_max', 1.05))
        expected = (lo + hi) / 2
    else:
        lo = hi = expected = float(cfg.get('processing_speed', 1.))
    # build_slot_profiles freezes speed to six decimal places before timing.
    lo, hi, expected = round(lo, 6), round(hi, 6), round(expected, 6)
    # Position/count/probability scopes may leave this clip at normal speed.
    # The per-file short-clip skip is incorporated separately in _bounds.
    if cfg.get('processing_speed_scope', 'random_count') != 'all':
        lo, hi = min(lo, 1.), max(hi, 1.)
    return lo, hi, expected


def _transition_max(cfg, name):
    if name == 'package' or not (cfg.get('processing_enabled') and cfg.get('processing_transition')):
        return 0
    seconds = (float(cfg.get('processing_transition_max', .3))
               if cfg.get('processing_transition_values', 'fixed') == 'random'
               else float(cfg.get('processing_transition_seconds', .25)))
    return max(1, round(seconds * cfg['fps']))


def _crop_settings(cfg):
    from shot_trim_controls import active as shot_active, maximum, minimum
    cap = float(cfg.get('voice_fit_max_trim_seconds', 1.))
    if shot_active(cfg):
        cap = maximum(cfg)
        if not cfg.get('shot_trim_voice_enabled', True):
            cap = 0.
    return cap, minimum(cfg)


def _bounds(clip, cfg, rates, cap, minimum):
    fps = cfg['fps']; duration = float(clip['duration'])
    lo_rate, hi_rate, expected_rate = rates
    if minimum > 0 and duration / hi_rate < minimum:
        if duration / lo_rate < minimum:
            lo_rate = hi_rate = expected_rate = 1.
        else:
            lo_rate, hi_rate = min(lo_rate, 1.), max(hi_rate, 1.)
            if duration / expected_rate < minimum:
                expected_rate = 1.
    full_lo = max(1, math.floor(duration / hi_rate * fps + 1e-8))
    full_hi = max(1, math.floor(duration / lo_rate * fps + 1e-8))
    nominal = max(1, math.floor(duration / expected_rate * fps + 1e-8))
    permitted = bool(clip.get('safe_cut_confirmed')) and cap > 0
    # Original clips shorter than the floor remain valid complete actions.
    # The floor limits additional cropping; it is not a material filter.
    floor = min(full_lo, max(1, math.ceil(minimum * fps - 1e-7)))
    low = max(floor, max(1, math.ceil((duration - cap) / hi_rate * fps - 1e-7))) if permitted else full_lo
    return dict(min_frames=low, max_frames=full_hi, full_min_frames=full_lo,
                nominal_frames=nominal, safe_cut=bool(clip.get('safe_cut_confirmed')),
                maximum_removed_source_seconds=cap if permitted else 0.)


def _interval(selected, options, overlap):
    low = sum(options[i]['min_frames'] for i in selected)
    high = sum(options[i]['max_frames'] for i in selected)
    if overlap:
        # Selection may shuffle the eventual order. Subtract the request cap
        # for every internal edge, rather than using one provisional adjacency
        # layout and incorrectly excluding a different legal ordering.
        low -= max(0, len(selected)-1) * overlap
        # bake_timeline caps each edge to a third of each neighbor, so even an
        # interior clip retains at least a third of its minimum frame length.
        low = max(math.ceil(sum(options[i]['min_frames'] for i in selected) / 3), low)
    return max(1, low), high


def select_block(pool, goal, locked, name, cfg, rng, dedup, snapshot, *, deadline=None):
    """Return complete source clips, or None for an unmarked legacy config.

    At most MAX_NODES candidate states are considered. A bounded-search miss
    reports its budget explicitly and never claims global infeasibility.
    The first/last side guards need full-sequence context and are intentionally
    left to the exact fitter rather than guessed from a partial time slot.
    """
    if not active(cfg):
        return None
    goal = int(goal)
    if goal <= 0:
        return []
    slots = max(0, int(cfg['max_clips']) - len(locked))
    fps = cfg['fps']; cap, minimum = _crop_settings(cfg)
    rates = _speed_bounds(cfg); overlap = _transition_max(cfg, name)
    end_extra = 0
    extra = 0
    if name == 'package':
        # The caller supplies this only in a temporary package-selection cfg,
        # using the difference from the actual verified ending_policy proof.
        # It is a prefilter allowance, not authority to extend the final slot.
        end_extra = cfg.get('capacity_package_end_extra_frames', 0)
        if end_extra is None:
            end_extra = 0
        from package_slot_timing import MAX_RELEASE_SECONDS
        if (not isinstance(end_extra, int) or isinstance(end_extra, bool)
                or not 0 <= end_extra <= math.ceil(MAX_RELEASE_SECONDS * fps)):
            raise GroupedError('PACKAGE_END_ALLOWANCE_INVALID',
                               '包装末句额外帧须来自已验证收尾依据的非负整数差值')
        extra = 2 * math.floor(float(cfg.get('package_anchor_margin_seconds', .2)) * fps + 1e-7) + end_extra
    states = {row['pair_key']: row['state'] for row in snapshot.get('pairs', [])}

    def compatible(a, b):
        return (a['hash'] != b['hash'] and a['identity_key'] != b['identity_key']
                and states.get(pair_key(a, b), 'pending') == 'clear')

    choices = []
    diagnostics = []
    for clip in pool:
        b = _bounds(clip, cfg, rates, cap, minimum)
        reason = ''
        if not all(compatible(clip, other) for other in locked):
            reason = 'coverage_or_identity_conflict'
        # An interior clip can receive at most two transition overlaps. This
        # conservative bound is safe even when the actual position is unknown.
        elif b['min_frames'] > goal + extra + 2 * overlap:
            reason = 'minimum_after_allowed_crop_exceeds_slot'
        row = dict(path=clip['relative_path'], **b, excluded_reason=reason)
        diagnostics.append(row)
        if not reason:
            weight = max(1e-12, float(dedup.rotation_weight(clip, 'ordinary')))
            # Weighted random ordering keeps equal-duration choices rotating
            # without changing any other planner's or old manifest's RNG.
            rank = -math.log(max(1e-12, rng.random())) / weight
            choices.append((clip, b, weight, rank))
    choices.sort(key=lambda entry: entry[3])
    options = [entry[1] for entry in choices]
    compatibility = [[compatible(a[0], b[0]) for b in choices] for a in choices]
    result = []; visited = 0; serial = 0; limit_reached = False; deadline_reached = False
    queue = [(0., serial, (), 0)]

    def nominal_frames(selected):
        total = sum(options[i]['nominal_frames'] for i in selected)
        if overlap:
            total -= sum(min(overlap, options[a]['nominal_frames'] // 3,
                             options[b]['nominal_frames'] // 3)
                         for a, b in zip(selected, selected[1:]))
        return total

    while queue:
        if deadline is not None and time.monotonic() >= deadline:
            deadline_reached = True
            break
        _, _, selected, next_index = heapq.heappop(queue)
        if len(selected) >= slots:
            continue
        for i in range(next_index, len(choices)):
            if visited >= MAX_NODES:
                limit_reached = True
                break
            visited += 1
            if not all(compatibility[i][j] for j in selected):
                continue
            child = selected + (i,)
            low, high = _interval(child, options, overlap)
            nominal = nominal_frames(child)
            if low <= goal + extra and high >= goal:
                result.append((child, nominal))
                # Complete feasible intervals are handed to the exact fitter;
                # adding more clips only increases trimming and duplicate risk.
                continue
            if len(child) >= slots:
                continue
            # With no transition, or all remaining minimum lengths at least
            # the maximum overlap, appending clips cannot reduce this bound.
            monotone = not overlap or all(options[j]['min_frames'] >= overlap
                                         for j in range(i + 1, len(choices)))
            if low > goal + extra and monotone:
                continue
            serial += 1
            gap = max(0, goal - high)
            heapq.heappush(queue, (gap + max(0, low - goal - extra), serial, child, i + 1))
        if limit_reached:
            break

    if not result:
        detail = dict(revision=REVISION, block=name, goal_frames=goal,
                      goal_seconds=goal/fps, package_extra_frames=extra,
                      package_end_extra_frames=end_extra,
                      maximum_clips=slots, maximum_nodes=MAX_NODES,
                      visited=visited, limit_reached=limit_reached,
                      deadline_reached=deadline_reached,
                      constraints=dict(maximum_removed_source_seconds=cap,
                                       minimum_kept_seconds=minimum,
                                       speed_min=rates[0], speed_max=rates[1],
                                       transition_max_frames=overlap,
                                       first_last_guards='exact_full_sequence_validation'),
                      options=diagnostics, exact_timeline_validation_required=True)
        reason = ('有界搜索已达到候选预算，尚未找到可行组合' if limit_reached or deadline_reached
                  else '许可裁量、最短保留、速度及去重限制下未找到可容纳的镜头组合')
        exc = GroupedError('VOICE_SLOT_CAPACITY_INSUFFICIENT',
                           f'{name}时间槽 {goal/fps:.3f}秒：{reason}；实际拟合仍须完整时间线校验')
        exc.selection_diagnostic = detail
        raise exc

    weights = []
    for selected, nominal in result:
        rotation = sum(choices[i][2] for i in selected) / len(selected)
        gap = abs(nominal - goal) / fps
        weight = rotation / (1 + gap) ** 2
        if nominal < goal:
            weight *= .2  # May be feasible under slower speed; exact fit decides.
        if name == 'package':
            weight /= len(selected)
            if goal <= nominal <= goal + extra:
                weight *= 4  # Prefer naturally complete packaging actions.
        weights.append(weight)
    selected, _ = rng.choices(result, weights=weights, k=1)[0]
    output = [dict(choices[i][0], planning_block=name,
                   boundary_no_transition=(name == 'package')) for i in selected]
    rng.shuffle(output)
    return output
