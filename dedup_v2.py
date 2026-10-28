# -*- coding: utf-8 -*-
"""Derived, rebuildable selection index. SQLite history and manifests remain authoritative."""
from __future__ import annotations
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
import ntpath

REVISION = 'selection_dedup_v2_20260929'
DEFAULTS = {
    'selection_version': 'v2',
    'selection_v2_interval_limit': .70,
    'selection_v2_recent_window': 50,
    'selection_v2_target_account': '',
    'selection_v2_weights': {
        'batch': 5.0, 'role': 2.5, 'recent': 1.2, 'total': .35,
        'interval': 12.0, 'same_material': 2.0, 'pair': 1.2,
        'triple': 2.5, 'ends': 1.0, 'duration': 1.0,
    },
}


def validate_settings(cfg):
    if cfg.get('selection_version', 'v2') not in ('v1', 'v2'):
        raise ValueError('选片算法版本无效。')
    limit = float(cfg.get('selection_v2_interval_limit', .70))
    if not math.isfinite(limit) or not 0 <= limit <= 1:
        raise ValueError('相同原片区间比例上限须为0～1。')
    recent = int(cfg.get('selection_v2_recent_window', 50))
    if not 1 <= recent <= 2000:
        raise ValueError('近期记录窗口须为1～2000。')
    if len(str(cfg.get('selection_v2_target_account', ''))) > 100:
        raise ValueError('目标账号名称过长。')
    weights = cfg.get('selection_v2_weights', DEFAULTS['selection_v2_weights'])
    if set(weights) != set(DEFAULTS['selection_v2_weights']) or any(
        not math.isfinite(float(v)) or not 0 <= float(v) <= 100 for v in weights.values()
    ):
        raise ValueError('选片权重配置无效。')


def digest(values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':')).encode('utf-8')).hexdigest()


def union(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1] + 1e-9:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def overlap_segments(left, right):
    a, b = union(left), union(right)
    i = j = 0
    result = []
    while i < len(a) and j < len(b):
        start, end = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if end > start:
            result.append((start, end))
        if a[i][1] < b[j][1]: i += 1
        else: j += 1
    return result


def _window(c):
    try:
        a, b = float(c['source_in_seconds']), float(c['source_out_seconds'])
        rate = float(c.get('playback_rate', 1))
        if 0 <= a < b and math.isfinite(a+b+rate) and rate > 0:
            return a, b, rate
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _displayed(segments, windows):
    """Union before conversion; if windows overlap, use the slowest rate conservatively."""
    total = 0.
    for a, b in segments:
        bounds = sorted({a, b} | {max(a, min(b, x)) for w in windows for x in w[:2]})
        for x, y in zip(bounds, bounds[1:]):
            if y <= x: continue
            rates = [w[2] for w in windows if w[0] < y and w[1] > x]
            if rates: total += (y-x) / min(rates)
    return total


class SelectionIndex:
    def __init__(self, assets, records, cfg):
        self.cfg = cfg
        self.weights = cfg.get('selection_v2_weights', DEFAULTS['selection_v2_weights'])
        by_hash = defaultdict(set)
        for c in list(assets) + [c for r in records for c in r.get('clips', [])]:
            if c.get('hash') and c.get('identity_key', '').startswith('clip:'):
                by_hash[c['hash']].add(c['identity_key'])
        self.hash_alias = {h: min(keys) for h, keys in by_hash.items()}
        self.records = []
        self.postings = defaultdict(set)
        self.path_postings = defaultdict(set)
        self.sequences = set()
        self.sets = set()
        self.total = Counter(); self.first = Counter(); self.last = Counter(); self.ordinary = Counter()
        self.recent = Counter(); self.batch = Counter()
        self.pairs = Counter(); self.triples = Counter(); self.ends = Counter()
        self.account_published = Counter()
        self.recent_size = int(cfg.get('selection_v2_recent_window', 50))
        # Existing Store.usage returns success ordered newest first, then reservations.
        for r in records[:self.recent_size]:
            for c in r.get('clips', []): self.recent[self.identity(c)] += 1
        for record in records: self.add(record, batch=False)

    def identity(self, c):
        h=c.get('hash','')
        return self.hash_alias.get(h) or c.get('identity_key') or ('sha256:' + h)

    @staticmethod
    def saved_path(c):
        return ntpath.normcase(ntpath.normpath(c['path'])) if c.get('path') else ''

    def keys(self, item):
        return tuple(self.identity(c) for c in item['clips'])

    def add(self, item, batch=True):
        if not item.get('clips'): return
        keys = self.keys(item)
        idx = len(self.records)
        self.records.append(item)
        self.sequences.add(keys); self.sets.add(frozenset(keys))
        for key in set(keys): self.postings[key].add(idx)
        for path in {self.saved_path(c) for c in item['clips']} - {''}:
            self.path_postings[path].add(idx)
        self.total.update(keys); self.first[keys[0]] += 1; self.last[keys[-1]] += 1
        self.ordinary.update(keys[1:-1])
        self.pairs.update(zip(keys, keys[1:]))
        self.triples.update(zip(keys, keys[1:], keys[2:]))
        self.ends[(keys[0], keys[-1])] += 1
        if batch: self.batch.update(keys)
        account = item.get('target_account') or item.get('account')
        if account and (item.get('publication_status') == 'published' or item.get('published') is True):
            self.account_published[account] += 1

    def rotation_weight(self, clip, role):
        key = self.identity(clip); w = self.weights
        role_count = self.first[key] if role == 'first' else self.last[key] if role == 'last' else self.ordinary[key]
        cost = (w['batch']*self.batch[key] + w['role']*role_count +
                w['recent']*self.recent[key] + w['total']*self.total[key])
        return 1 / (1+cost)

    def compare(self, item, other):
        a = defaultdict(list); b = defaultdict(list)
        for c in item['clips']: a[self.identity(c)].append(c)
        for c in other['clips']: b[self.identity(c)].append(c)
        common = set(a)&set(b)
        shared = shown_a = shown_b = 0.
        unknown = []
        paths_a=defaultdict(list); paths_b=defaultdict(list)
        for c in item['clips']:
            if (path:=self.saved_path(c)): paths_a[path].append(c)
        for c in other['clips']:
            if (path:=self.saved_path(c)): paths_b[path].append(c)
        # Same saved path may indicate a new version, but without a stable clip ID
        # the SHA remains the authoritative identity. Never merge transitive paths.
        version_paths={path for path in set(paths_a)&set(paths_b)
                       if {c.get('hash') for c in paths_a[path]} != {c.get('hash') for c in paths_b[path]}}
        unknown.extend('version-path:'+path for path in version_paths)
        for key in common:
            aa = [_window(c) for c in a[key]]; bb = [_window(c) for c in b[key]]
            hashes_a = {c.get('hash') for c in a[key]}; hashes_b = {c.get('hash') for c in b[key]}
            # Different content versions have no proven common time coordinates.
            if None in aa+bb or not all(hashes_a|hashes_b) or len(hashes_a|hashes_b) != 1:
                unknown.append(key); continue
            pieces = overlap_segments([(w[0],w[1]) for w in aa],[(w[0],w[1]) for w in bb])
            shared += sum(y-x for x,y in pieces)
            shown_a += _displayed(pieces,aa)
            shown_b += _displayed(pieces,bb)
        def denom(record):
            fps = record.get('fps') or record.get('output_fps') or record.get('video_processing',{}).get('fps') or 30
            return sum((c.get('duration_frames',0)/fps if c.get('duration_frames') else
                        (w[1]-w[0])/w[2] if (w:=_window(c)) else float(c.get('duration',0)))
                       for c in record['clips'])
        da, db = denom(item), denom(other)
        ka, kb = self.keys(item), self.keys(other)
        pairs_a=set(zip(ka,ka[1:]));pairs_b=set(zip(kb,kb[1:]))
        triples_a=set(zip(ka,ka[1:],ka[2:]));triples_b=set(zip(kb,kb[1:],kb[2:]))
        return {'other_id':other.get('id',''), 'other_account':other.get('target_account') or other.get('account') or '',
                'other_published':other.get('publication_status')=='published' or other.get('published') is True,
                'same_material_count':len(common), 'clip_count_ratio':len(common)/min(len(ka),len(kb)),
                'shared_source_seconds':round(shared,6), 'candidate_display_seconds':round(shown_a,6),
                'other_display_seconds':round(shown_b,6),
                'candidate_ratio':round(shown_a/da,6) if da else 0.,
                'other_ratio':round(shown_b/db,6) if db else 0.,
                'max_ratio':round(max(shown_a/da if da else 0,shown_b/db if db else 0),6),
                'repeated_pairs':len(pairs_a & pairs_b), 'repeated_triples':len(triples_a & triples_b),
                'repeated_ends':int((ka[0],ka[-1])==(kb[0],kb[-1])),
                'version_relation_count':len(version_paths),
                'interval_complete':not unknown, 'interval_unknown_materials':sorted(unknown),
                'denominator':'各自片段播放时长之和，未扣转场重叠'}

    def evaluate(self, item, strict=False):
        keys=self.keys(item)
        if keys in self.sequences:
            return False, '历史已有完全相同的素材身份顺序', None, 0.
        if strict and frozenset(keys) in self.sets:
            return False, '更严格模式禁止相同素材集合换顺序', None, 0.
        candidates=set().union(*(self.postings[k] for k in set(keys))) if keys else set()
        for path in {self.saved_path(c) for c in item['clips']} - {''}:
            candidates.update(self.path_postings[path])
        nearest=None; penalty=0.; limit=float(self.cfg.get('selection_v2_interval_limit',.70))
        account=str(self.cfg.get('selection_v2_target_account','')).strip()
        for idx in candidates:
            comp=self.compare(item,self.records[idx])
            if nearest is None or (comp['max_ratio'],comp['same_material_count'],comp['version_relation_count'],comp['repeated_triples']) > (
                nearest['max_ratio'],nearest['same_material_count'],nearest['version_relation_count'],nearest['repeated_triples']): nearest=comp
            if strict and comp['max_ratio'] > limit+1e-9:
                return False, f'相同原片区间比例{comp["max_ratio"]:.1%}超过{limit:.0%}上限', comp, 0.
            p=(self.weights['interval']*comp['max_ratio']+
               self.weights['same_material']*(comp['clip_count_ratio']+.2*comp['version_relation_count'])+
               self.weights['pair']*comp['repeated_pairs']+
               self.weights['triple']*comp['repeated_triples']+
               self.weights['ends']*comp['repeated_ends'])
            if account and comp['other_account']==account and comp['other_published']: p*=1.5
            penalty=max(penalty,p)
        if nearest:
            signals=[]
            if nearest['shared_source_seconds']:
                signals.append(f"共享原片{nearest['shared_source_seconds']:.2f}秒")
            if nearest['same_material_count']:
                signals.append(f"同素材{nearest['same_material_count']}条")
            if nearest['repeated_triples']: signals.append('重复连续三段')
            elif nearest['repeated_pairs']: signals.append('重复相邻搭配')
            if nearest['repeated_ends']: signals.append('重复首尾搭配')
            if nearest['version_relation_count']: signals.append('同路径内容版本线索')
            if not nearest['interval_complete']: signals.append('部分区间未知')
            return True, '接受；已按' + '、'.join(signals or ['旧搭配']) + '降权', nearest, penalty
        return True, '接受；未发现共享素材的历史方案，已按角色和批内使用次数轮换', None, penalty

    def summary(self, items, pools):
        first=Counter(self.identity(i['clips'][0]) for i in items)
        last=Counter(self.identity(i['clips'][-1]) for i in items)
        groups=Counter(c['group'] for i in items for c in i['clips'])
        labels={self.identity(c):c.get('display') or c.get('name') or self.identity(c)
                for i in items for c in i['clips']}
        used={self.identity(c) for i in items for c in i['clips']}
        possible={self.identity(c) for group in pools.values() for c in group}
        return {'first_distribution':dict(first),'last_distribution':dict(last),
                'material_labels':labels,
                'group_uses':dict(groups),'unused_eligible_materials':len(possible-used),
                'group_coverage':{group:{'selected':len({self.identity(c) for i in items for c in i['clips'] if c['group']==group}),
                                         'candidates':len({self.identity(c) for c in pool})}
                                  for group,pool in pools.items()},
                'eligible_materials':len(possible), 'denominator':'各自片段播放时长之和，未扣转场重叠'}
