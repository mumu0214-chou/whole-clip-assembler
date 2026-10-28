"""Search G sequences against the frozen slot speeds and transitions.

This is a candidate generator. The original fitter, coverage checks and history
index remain the authority. Nothing in this module grants crop permission.
"""
import math
import time
import statistics

from grouped_materials import GroupedError, pair_key

REVISION = 'grouped_frozen_slot_search_v1'
MAX_NODES = 12000


class Search:
    def __init__(self, pool, cfg, dedup, snapshot):
        self.pool, self.cfg, self.dedup = pool, cfg, dedup
        states = {r['pair_key']: r['state'] for r in snapshot.get('pairs', [])}
        self.blocked = []
        for a in pool:
            self.blocked.append(sum(1 << j for j, b in enumerate(pool)
                if a['hash'] == b['hash'] or a['identity_key'] == b['identity_key']
                or (bool(a.get('sk_placement_group')) and a.get('sk_placement_group') == b.get('sk_placement_group')
                    if snapshot.get('sk_revision') else states.get(pair_key(a, b), 'pending') != 'clear')))
        self.seen = set()
        self.visited = 0
        self.timing_candidates = 0
        self.shared_cache = {}
        self.history_denominators = []
        self.history_pruned = 0

    def _shared_lower(self, index, timing, low):
        """Conservative overlap after MAXIMUM permitted crop on either side.

        A prefix already above the history limit cannot be rescued by appending
        sources. This bound only discards provably rejected paths; unknown
        windows/versions are left to the existing final history validator.
        """
        from dedup_v2 import _window,overlap_segments,_displayed
        clip=self.pool[index];key=self.dedup.identity(clip)
        rate=timing['playback_rate'];a=timing['source_in_seconds'];b=timing['source_out_seconds']
        # Uncut timing can include a fractional source-frame remainder. Include
        # the entire source span unless the lower frame count actually shrinks.
        span=(b-a) if low==timing['duration_frames'] else low*rate/self.cfg['fps']
        cache_key=(len(self.dedup.records),index,rate,round(span,9),a,b)
        if cache_key in self.shared_cache:return self.shared_cache[cache_key]
        result=[]
        for record_index in self.dedup.postings.get(key,()):
            others=[c for c in self.dedup.records[record_index]['clips'] if self.dedup.identity(c)==key]
            windows=[_window(c) for c in others]
            if not windows or None in windows or any(c.get('hash')!=clip['hash'] for c in others):continue
            possibilities=[(a,min(b,a+span)),(max(a,b-span),b)]
            values=[]
            for window in possibilities:
                pieces=overlap_segments([window],[(w[0],w[1]) for w in windows])
                values.append((sum(y-x for x,y in pieces)/rate,_displayed(pieces,windows)))
            full_pieces=overlap_segments([(a,b)],[(w[0],w[1]) for w in windows])
            full_shared=sum(y-x for x,y in full_pieces)/rate
            result.append((record_index,min(v[0] for v in values),min(v[1] for v in values),full_shared))
        if len(self.shared_cache)>=2048:self.shared_cache.clear()
        self.shared_cache[cache_key]=result
        return result

    def select(self, goal, seed, rng, *, deadline=None, stop=None, validator=None):
        from ball_opening import allowed as opening_allowed
        from timeline_processing import build_slot_profiles, timing_for_media, transition_frames
        from voice_edge_fitting import settings
        from shot_trim_controls import active as shot_active
        cfg = self.cfg; fps = cfg['fps']; s = settings(cfg)
        from ball_pacing import active as paced, minimum_frames
        pacing=paced(cfg)
        goal = int(goal)
        from dedup_v2 import _window
        if len(self.history_denominators)!=len(self.dedup.records):self.shared_cache.clear()
        if len(self.seen)>=4096:self.seen.clear()
        while len(self.history_denominators)<len(self.dedup.records):
            record=self.dedup.records[len(self.history_denominators)]
            old_fps=record.get('fps') or record.get('output_fps') or record.get('video_processing',{}).get('fps') or 30
            self.history_denominators.append(sum(c['duration_frames']/old_fps if c.get('duration_frames') else
                (w[1]-w[0])/w[2] if (w:=_window(c)) else float(c.get('duration',0)) for c in record['clips']))
        maximum = min(len(self.pool), int(cfg['max_clips']))
        weights = [max(1e-12, self.dedup.rotation_weight(c, 'ordinary')) for c in self.pool]
        # Vary shot count as well as sources. Prioritize counts near the actual
        # target; do not bias all searches toward a short fixed subset.
        average = sum(c['duration'] for c in self.pool) / max(1, len(self.pool))
        counts = list(range(1, maximum + 1))
        if pacing:
            counts=[n for n in counts if n<6 or n*math.ceil(1.9*fps-1e-7)<=goal]
        rank = {n: rng.random() for n in counts}
        counts.sort(key=lambda n: abs(n * average * fps - goal) / max(fps, goal) + rank[n] * .45)
        visited = 0; budget_stop = False; state_limit = False
        for n in counts:
            if (stop and stop.is_set()) or (deadline is not None and time.monotonic() >= deadline):
                budget_stop = True; break
            profiles, requests = build_slot_profiles(n, cfg, seed, set(range(n)), voice_fitting=True)
            full = []; lower = []; timings=[]
            for pos, profile in enumerate(profiles):
                profile['first_slot'] = pos == 0
                frames = []; mins = []; details=[]
                sides = ['head', 'tail']
                if shot_active(cfg) and cfg.get('processing_micro_trim_mode') in ('head', 'tail'):
                    sides = [cfg['processing_micro_trim_mode']]
                if pos == 0 and s['protect_first'] and 'head' in sides: sides.remove('head')
                if pos == n-1 and s['protect_last'] and 'tail' in sides: sides.remove('tail')
                for clip in self.pool:
                    timing = timing_for_media(clip, profile, cfg)
                    size = timing['duration_frames']; low = size
                    if pacing and pos<3 and size<minimum_frames(cfg,pos):
                        frames.append(size);mins.append(size);details.append(timing);continue
                    if sides and clip.get('safe_cut_confirmed') and (not shot_active(cfg) or cfg.get('shot_trim_voice_enabled', True)):
                        span = timing['source_out_seconds'] - timing['source_in_seconds']
                        cap = max(1, math.ceil((span-s['max_trim_seconds'])/timing['playback_rate']*fps-1e-7))
                        floor = min(size, max(1, math.ceil(s['minimum_seconds']*fps-1e-7),
                                              minimum_frames(cfg,pos,size) if pacing else 1))
                        low = min(size, max(cap, floor))
                    if pacing:low=max(low,minimum_frames(cfg,pos,size)) if pos>=3 else max(low,min(size,minimum_frames(cfg,pos)))
                    frames.append(size); mins.append(low);details.append(timing)
                full.append(frames); lower.append(mins);timings.append(details)
            # These loose bounds may keep impossible paths; they cannot discard
            # a legal path. Exact overlap/capacity is checked on completed paths.
            suffix_max = [0] * (n+1); suffix_min = [0] * (n+1)
            for pos in range(n-1, -1, -1):
                suffix_max[pos] = suffix_max[pos+1] + max(full[pos])
                suffix_min[pos] = suffix_min[pos+1] + min(lower[pos])
                if pos: suffix_min[pos] -= requests[pos-1]['requested_frames']
            noise = [(-math.log(max(1e-12, rng.random())) / weights[i]) for i in range(len(self.pool))]
            scale=max(1e-12,statistics.median(noise))
            noise=[value/scale for value in noise]
            chosen = []; count_start = visited;shared={}
            denominator_upper=(goal+sum(r['requested_frames'] for r in requests))/fps
            limit=float(cfg.get('selection_v2_interval_limit',.7))+1e-5

            def visit(pos, total, minimum, forbidden, previous, previous_min):
                nonlocal visited, budget_stop, state_limit
                if visited - count_start >= max(256, MAX_NODES // len(counts)):
                    budget_stop = True; state_limit = True; return None
                if visited % 32 == 0 and ((stop and stop.is_set()) or (deadline is not None and time.monotonic() >= deadline)):
                    budget_stop = True; return None
                visited += 1
                if total + suffix_max[pos] < goal or minimum + suffix_min[pos] > goal:
                    return None
                if pos == n:
                    key = (goal, seed, tuple(chosen))
                    if minimum <= goal <= total <= goal + math.floor(s['max_excess_seconds']*fps+1e-7) and key not in self.seen:
                        identities=tuple(self.dedup.identity(self.pool[i]) for i in chosen)
                        if identities in self.dedup.sequences or (cfg.get('dedup_mode')=='strict' and frozenset(identities) in self.dedup.sets):
                            self.history_pruned+=1;return None
                        if cfg.get('dedup_mode')=='strict':
                            physical=sum((timings[p][i]['source_out_seconds']-timings[p][i]['source_in_seconds'])/timings[p][i]['playback_rate'] for p,i in enumerate(chosen))
                            max_removal=max(0,physical-goal/fps)+1e-7*n
                            if any(full_shared-max_removal>denominator_upper*limit for _,_,full_shared in shared.values()):
                                self.history_pruned+=1;return None
                        self.seen.add(key); self.timing_candidates += 1
                        result=[dict(self.pool[i], planning_block='all', boundary_no_transition=False) for i in chosen]
                        if validator is None or validator(result):return result
                    return None
                available = [i for i in range(len(self.pool)) if not forbidden & (1 << i)]
                ideal = (goal-total) / (n-pos)
                rank_candidates = getattr(self.dedup, 'rank_candidates', None)
                priority = getattr(self.dedup, 'candidate_priority', None)
                if rank_candidates:
                    available = rank_candidates(available, [self.pool[j] for j in chosen], full[pos], ideal, rng, pos)
                elif priority:
                    available.sort(key=lambda i: priority(self.pool[i], abs(full[pos][i]-ideal) / fps + noise[i] * .65))
                else:
                    available.sort(key=lambda i: abs(full[pos][i]-ideal) / fps + noise[i] * .65)
                for i in available:
                    if pos==0 and not opening_allowed(self.pool[i],cfg):continue
                    size, low = full[pos][i], lower[pos][i]
                    candidate_ok=getattr(self.dedup,'candidate_ok',None)
                    if candidate_ok and not candidate_ok([self.pool[j] for j in chosen],self.pool[i]):continue
                    if pacing and pos<3 and size<minimum_frames(cfg,pos):continue
                    if pacing and pos>=3 and chosen and size<math.ceil(1.8*fps-1e-7) and full[pos-1][chosen[-1]]<math.ceil(1.8*fps-1e-7):continue
                    value = total + size - (transition_frames(requests[pos-1], previous, size) if pos else 0)
                    min_value = minimum + low - (transition_frames(requests[pos-1], previous_min, low) if pos else 0)
                    contributions=self._shared_lower(i,timings[pos][i],low) if cfg.get('dedup_mode')=='strict' else []
                    blocked=False
                    for rid,shown_a,shown_b,full_a in contributions:
                        old_a,old_b,old_full=shared.get(rid,(0.,0.,0.))
                        if (old_a+shown_a>denominator_upper*limit or
                            (self.history_denominators[rid]>0 and old_b+shown_b>self.history_denominators[rid]*limit)):
                            blocked=True;break
                    if blocked:
                        self.history_pruned+=1;continue
                    for rid,shown_a,shown_b,full_a in contributions:
                        old_a,old_b,old_full=shared.get(rid,(0.,0.,0.));shared[rid]=(old_a+shown_a,old_b+shown_b,old_full+full_a)
                    chosen.append(i)
                    found = visit(pos+1, value, min_value, forbidden | self.blocked[i], size, low)
                    chosen.pop()
                    for rid,shown_a,shown_b,full_a in contributions:
                        old_a,old_b,old_full=shared[rid];shared[rid]=(old_a-shown_a,old_b-shown_b,old_full-full_a)
                    if found is not None: return found
                    if budget_stop: break
                return None

            found = visit(0, 0, 0, 0, 0, 0)
            if found is not None:
                self.visited += visited
                return found
            if deadline is not None and time.monotonic() >= deadline: break
            if stop and stop.is_set(): break
            budget_stop = False  # A count's state limit must not starve the others.
        self.visited += visited
        detail = dict(revision=REVISION, goal_frames=goal, visited=visited,
                      maximum_nodes=MAX_NODES, budget_stopped=budget_stop or state_limit,
                      timing_candidates=self.timing_candidates, history_pruned=self.history_pruned,
                      exact_timeline_validation_required=True)
        exc = GroupedError('VOICE_SLOT_SEARCH_MISS',
            '本轮冻结速度/转场下未找到新的候选，将更换候选继续搜索' if budget_stop else '本轮冻结速度/转场下未找到新的合法时长候选')
        exc.selection_diagnostic = detail
        raise exc
