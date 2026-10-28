"""SK policy adapter over the existing timing search and visual dedup index."""
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import time

from sk_materials import (REVISION, TYPES, LABELS, preferences, scene_key, unit_key,
                          classify, active, hard_conflict)


def category_seconds(clips, fps):
    """Attribute half of every adjacent overlap to each participating clip."""
    frames = [float(c['duration_frames']) for c in clips]
    for i in range(1, len(clips)):
        left, right = clips[i-1], clips[i]
        overlap = max(0, left['timeline_start_frame'] + left['duration_frames'] - right['timeline_start_frame'])
        frames[i-1] -= overlap / 2
        frames[i] -= overlap / 2
    result = Counter()
    for c, value in zip(clips, frames):
        result[c['category']] += value / fps
    return {k: result[k] for k in TYPES}


def _time(value):
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError):
        return None


def load_history(store, cfg, clips, *, at=None, exclude_batch=''):
    """Success time is rolling UTC; valid reservations have no age cutoff."""
    started = time.perf_counter()
    exclude_batch = exclude_batch or getattr(store, 'bid', '')
    end = (at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start = end - timedelta(hours=72)
    by_hash = {c['hash']: c for c in clips}
    root = Path(cfg['material_root']).resolve()
    records, unknown, warnings, seen = [], 0, [], set()

    def normalize(item, stamp, state, batch_id=''):
        nonlocal unknown
        if item.get('id') in seen or (exclude_batch and batch_id == exclude_batch):
            return
        old = deepcopy(item)
        relevant = False
        complete = bool(old.get('clips'))
        for clip in old.get('clips', []):
            if clip.get('hash') in by_hash:
                current = by_hash[clip['hash']]
                for k, v in current.items():
                    if k.startswith('sk_') or k in ('category', 'relative_path'):
                        clip[k] = v
                relevant = True
            else:
                try:
                    p = Path(clip['path']).resolve()
                    relative = p.relative_to(root).as_posix()
                    clip.update(classify(relative, root))
                    relevant = True
                except (ValueError, KeyError, OSError):
                    pass
            if not clip.get('hash'):
                complete = False
        if not relevant:
            if old.get('sk_snapshot') or active(old.get('_history_config', {})):
                unknown += 1
            return
        if not complete:
            unknown += 1
            return
        old.pop('_history_config', None)
        old['sk_history_state'] = state
        old['sk_history_time'] = stamp
        records.append(old)
        seen.add(old.get('id'))

    with store.connect() as db:
        db.execute('CREATE INDEX IF NOT EXISTS history_used_at_idx ON history(used_at)')
        successful = db.execute('SELECT output_id,payload,used_at FROM history WHERE julianday(used_at)>=julianday(?) AND julianday(used_at)<=julianday(?) ORDER BY julianday(used_at) DESC',
                                (start.isoformat(), end.isoformat())).fetchall()
        unknown_times = db.execute('SELECT count(*) FROM history WHERE julianday(used_at) IS NULL').fetchone()[0]
        reserved = db.execute('SELECT output_id,batch_id,payload FROM reservations WHERE batch_id!=?', (exclude_batch,)).fetchall()
    for oid, payload, stamp in successful:
        try:
            normalize(json.loads(payload), stamp, 'success')
        except (ValueError, TypeError, KeyError) as exc:
            warnings.append(f'历史{oid}无法还原：{exc}')
    for oid, bid, payload in reserved:
        try:
            normalize(json.loads(payload), '', 'reservation', bid)
        except (ValueError, TypeError, KeyError) as exc:
            warnings.append(f'预留{oid}无法还原：{exc}')
    # Legacy manifests may carry successful SK use before this scheme existed.
    # Recover successes only; abandoned drafts never become success records.
    for path in (store.root / 'batches').glob('*/batch_manifest.json'):
        try:
            batch = json.loads(path.read_text(encoding='utf-8-sig'))
            if batch.get('id') == exclude_batch:
                continue
            for item in batch.get('items', []):
                if item.get('status') != 'completed' or item.get('id') in seen:
                    continue
                stamp = item.get('finished_at') or batch.get('last_run_at')
                stamp_time = _time(stamp)
                if stamp_time is None:
                    if active(batch.get('config', {})):
                        unknown += 1
                    continue
                if start <= stamp_time <= end:
                    normalize(dict(item, _history_config=batch.get('config', {})), stamp, 'success', batch.get('id', ''))
        except (OSError, ValueError, TypeError) as exc:
            warnings.append(f'历史清单无法读取：{path.name}：{exc}')
    info = dict(revision=REVISION, window_start=start.isoformat(), window_end=end.isoformat(), hours=72,
                success_records=sum(r['sk_history_state'] == 'success' for r in records),
                reservation_records=sum(r['sk_history_state'] == 'reservation' for r in records),
                unknown_records=unknown, unknown_time_records=unknown_times, warnings=warnings,
                coverage='覆盖不足' if unknown or unknown_times or warnings else '现存数据库及清单中可识别记录已检查',
                load_seconds=time.perf_counter()-started)
    return records, info


class Selection:
    """Hierarchical random sampling, duration feedback and group diagnostics."""
    def __init__(self, base, pool, cfg, info):
        self.base, self.pool, self.cfg, self.info = base, pool, cfg, info
        self.prefs = preferences(cfg)
        from sk_material_bias import rules, winner
        self.material_bias = rules(cfg)
        self.material_bias_factors={c['relative_path']:(winner(c['relative_path'],self.material_bias) or {}).get('factor',1.)
                                    for c in pool} if self.material_bias else {}
        normal,boosted=Counter(),Counter()
        if self.material_bias:
            for c in pool:
                weight=self.prefs[c['category']]/100/max(.1,c['duration'])
                normal[c['category']]+=weight
                boosted[c['category']]+=weight*self.material_bias_factors[c['relative_path']]
        self.material_bias_normalization={k:normal[k]/value for k,value in boosted.items() if value}
        self.seconds = Counter()
        self.scene_uses, self.unit_uses, self.open_uses = Counter(), Counter(), Counter()
        self.scene_postings = defaultdict(set)
        for i, record in enumerate(base.records):
            self._index_groups(record, i, batch=False)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _index_groups(self, item, index, batch):
        for clip in item.get('clips', []):
            if not clip.get('sk_library'):
                continue
            key = scene_key(clip)
            self.scene_uses[key] += 1
            self.unit_uses[unit_key(clip)] += 1
            self.scene_postings[key].add(index)
        if item.get('clips'):
            first = item['clips'][0]
            self.open_uses[first.get('hash')] += 1
        if batch:
            self.seconds.update(category_seconds(item['clips'], self.cfg['fps']))

    def add(self, item, batch=True):
        self.base.add(item, batch=batch)
        self._index_groups(item, len(self.base.records)-1, batch)

    def candidate_ok(self, chosen, clip):
        return not hard_conflict(chosen+[clip])

    def candidate_weights(self, candidates, chosen=(), duration_frames=None, position=None):
        groups = defaultdict(lambda: defaultdict(list))
        times = []
        for i, c in enumerate(candidates):
            groups[(c['category'], scene_key(c))][unit_key(c)].append(i)
            frames = duration_frames[i] if duration_frames is not None else c['duration'] * self.cfg['fps']
            times.append(frames / self.cfg['fps'])
        scenes_by_type = Counter(k[0] for k in groups)
        duration_by_type=Counter()
        for (typ,scene),units in groups.items():
            duration_by_type[typ]+=sum(sum(times[i] for i in indices)/len(indices)
                                       for indices in units.values())/len(units)
        total = sum(self.seconds.values())
        seen_scenes = {scene_key(c) for c in chosen}
        result = []
        for c in candidates:
            typ, scene, unit = c['category'], scene_key(c), unit_key(c)
            expected_duration = duration_by_type[typ] / scenes_by_type[typ]
            desired = self.prefs[typ] / 100
            actual = self.seconds[typ] / total if total else desired
            feedback = math.exp(max(-.7, min(.7, (desired-actual) / max(.1, desired) * .5)))
            weight = desired * feedback / max(.1, expected_duration)
            weight /= max(1, scenes_by_type[typ]) * len(groups[(typ, scene)]) * len(groups[(typ, scene)][unit])
            # Inventory-dependent rotation, never a fixed ban/cooldown.
            weight /= 1 + .45*self.scene_uses[scene] + .25*self.unit_uses[unit]
            if scene in seen_scenes:
                weight *= .18
            if position == 0:
                weight /= 1 + self.open_uses[c['hash']]
            result.append(max(1e-15, weight) if desired > 0 else 0.)
        from sk_material_bias import adjust
        return adjust(candidates,result,self.material_bias,self.material_bias_factors)

    def rank_candidates(self, indices, chosen, sizes, ideal, rng, position):
        clips = [self.pool[i] for i in indices]
        frames = [sizes[i] for i in indices]
        weights = self.candidate_weights(clips, chosen, frames, position)
        # Exponential race preserves hierarchical probability. Timing remains a
        # mild preference; feasibility and all hard constraints remain shared.
        noise = [-math.log(max(1e-12, rng.random())) / max(w, 1e-15) for w in weights]
        scale = sorted(noise)[len(noise)//2] if noise else 1
        return [idx for _, idx in sorted((noise[j]/max(scale, 1e-12) +
                    .025*abs(sizes[idx]-ideal)/max(1, ideal), idx) for j, idx in enumerate(indices))]

    def rotation_weight(self, clip, role):
        # Shared non-exact selection calls candidate_weights on the available
        # set; this fallback also supports the existing search weight interface.
        factor=self.material_bias_factors.get(clip['relative_path'],1.)
        return (self.prefs[clip['category']] / 100 / max(.1, clip['duration']) * factor *
                self.material_bias_normalization.get(clip['category'],1.))

    def evaluate(self, item, strict=False):
        accepted, reason, nearest, penalty = self.base.evaluate(item, strict)
        if not accepted:
            return accepted, reason, nearest, penalty
        candidate_scenes = [scene_key(c) for c in item['clips']]
        candidates = set().union(*(self.scene_postings[s] for s in candidate_scenes)) if candidate_scenes else set()
        best = None
        for idx in candidates:
            other = self.base.records[idx]
            old = [scene_key(c) if c.get('sk_library') else c.get('identity_key', '') for c in other['clips']]
            shared = sum((Counter(candidate_scenes) & Counter(old)).values())
            prefix = 0
            for a, b in zip(candidate_scenes, old):
                if a != b:
                    break
                prefix += 1
            pairs = len(set(zip(candidate_scenes, candidate_scenes[1:])) & set(zip(old, old[1:])))
            score = shared/max(1, min(len(old), len(candidate_scenes))) + .2*prefix + .15*pairs
            if best is None or score > best['score']:
                best = dict(other_id=other.get('id'), score=score, shared_scene_groups=shared,
                            same_opening_prefix=prefix, repeated_scene_pairs=pairs,
                            reason=f'相似场景重合{shared}段，开头连续相似{prefix}段，重复相邻搭配{pairs}处')
        local_repeats = len(candidate_scenes) - len(set(candidate_scenes))
        detail = dict(base_nearest=nearest, scene_nearest=best, repeated_scene_adjustments=local_repeats)
        return True, reason, detail, penalty + (best['score'] if best else 0) + 2*local_repeats


def freeze(item, cfg, history_info):
    from core import object_hash
    seconds = category_seconds(item['clips'], cfg['fps'])
    scenes = [scene_key(c) for c in item['clips']]
    item['sk_snapshot'] = dict(revision=REVISION, root=str(Path(cfg['material_root']).resolve()),
        canvas=[cfg['width'],cfg['height']],
        preferences=preferences(cfg), category_seconds=seconds, opening_type=item['clips'][0]['category'],
        history=deepcopy(history_info), fit=cfg.get('sk_fit', 'contain'),
        soft_scene_adjustments=len(scenes)-len(set(scenes)),
        duration_basis='最终播放帧数，重叠转场由相邻片段各分摊一半')
    item['sk_visual_sequence'] = object_hash([c['hash'] for c in item['clips']])
    from sk_material_bias import snapshot
    item['sk_snapshot']['material_bias']=snapshot(cfg)


def validate(item, cfg):
    from sk_materials import validate_sequence
    from core import object_hash
    validate_sequence(item['clips'], cfg)
    value = item.get('sk_snapshot', {})
    if value.get('revision') != REVISION or value.get('preferences') != preferences(cfg):
        raise ValueError('SK方案快照与冻结配置不一致')
    from sk_material_bias import validate as validate_bias
    validate_bias(value.get('material_bias'),cfg)
    if value.get('fit') != cfg.get('sk_fit', 'contain'):
        raise ValueError('SK画面适配与冻结配置不一致')
    if value.get('canvas') is not None and value['canvas']!=[cfg['width'],cfg['height']]:
        raise ValueError('SK画布与冻结配置不一致')
    if value.get('category_seconds') != category_seconds(item['clips'], cfg['fps']):
        raise ValueError('SK实际类别时长被修改')
    if item.get('sk_visual_sequence') != object_hash([c['hash'] for c in item['clips']]):
        raise ValueError('SK视觉组合顺序被修改')
    if abs(sum(value['category_seconds'].values())-item['duration']) > 1e-6:
        raise ValueError('SK类别时长与总时间线不一致')


def finalize(batch):
    cfg = batch['config']
    if not active(cfg):
        return batch
    seconds, counts = Counter(), Counter()
    scenes = defaultdict(set)
    for item in batch['items']:
        if 'sk_snapshot' not in item:
            continue
        seconds.update(item['sk_snapshot']['category_seconds'])
        for c in item['clips']:
            counts[c['category']] += 1
            scenes[c['category']].add(scene_key(c))
    total = sum(seconds.values())
    batch['sk_summary'] = dict(revision=REVISION, total_seconds=total,
        types=[dict(type=k, label=LABELS[k], target_percent=preferences(cfg)[k],
                    actual_percent=100*seconds[k]/total if total else 0, seconds=seconds[k],
                    clip_count=counts[k], scene_count=len(scenes[k])) for k in TYPES],
        deviation_reason='比例是整批时长软目标；受随机性、场景轮换、硬去重、合法时间线和批量规模影响，不强补尾批。',
        planned=len(batch['items']), unplanned=cfg['count']-len(batch['items']))
    inventory={r['type']:r for r in batch.get('sk_scan_summary',{}).get('types',[])}
    for row in batch['sk_summary']['types']:
        row['available_scenes']=inventory.get(row['type'],{}).get('available_scenes')
        row['deviation_note']=(f'目标{row["target_percent"]:g}%，实际{row["actual_percent"]:.1f}%；可用场景{row["available_scenes"]}个。'
                               '按随机性、合法时长及场景轮换选择，未为补比例突破硬限制。')
    from sk_material_bias import summarize
    batch['sk_summary']['material_bias']=summarize(batch)
    return batch


def assert_reservable(batch, store):
    """Called under the existing output publication lock; previews never write."""
    if not active(batch.get('config', {})):
        return
    from dedup_v2 import SelectionIndex
    pool = [c for i in batch['items'] for c in i.get('clips', [])]
    records, info = load_history(store, batch['config'], pool, exclude_batch=batch['id'])
    index = SelectionIndex(pool, records, batch['config'])
    for item in batch['items']:
        if item.get('status') != 'planned':
            continue
        ok, reason, _, _ = index.evaluate(item, batch['config']['dedup_mode'] == 'strict')
        if not ok:
            raise ValueError('SK发布前去重失败，未建立本批预留：' + reason)
        index.add(item)
