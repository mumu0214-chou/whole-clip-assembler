"""Diversity-first G selection; manual effect bindings also describe pictures.

All eligible clips remain candidates. Scene targets are a batch-level tie-break,
never a per-video requirement or a reason to change approved source windows.
"""
from collections import Counter, deque
from contextlib import closing
import math
from pathlib import Path
import re
import sqlite3

from editing_profiles import ball_profile, grouped

REVISION = 'ball_scene_diversity_v1'
SCENES = {'burst': '掰开牛肉丸', 'soup': '煮汤', 'display': '展示牛肉丸', 'eat': '吃牛肉丸'}
WEIGHTS = {'burst': 4, 'soup': 3, 'display': 2, 'eat': 1}
ALIASES = {'爆汁': 'burst', '爆汁声': 'burst', '沸腾': 'soup', '煮沸': 'soup',
           '沸腾声': 'soup', '煮沸声': 'soup', '展示': 'display', '展示声': 'display',
           '咀嚼': 'eat', '咀嚼声': 'eat'}


def active(cfg):
    return (ball_profile(cfg) and grouped(cfg) and
            cfg.get('ball_scene_selection_revision') == REVISION)


def scene_for_name(name):
    name = re.sub(r'\s+', '', str(name)).replace('（', '(').replace('）', ')')
    name = re.sub(r'\(\d+\)$', '', name)
    return ALIASES.get(name)


def capture_config(cfg, data_dir):
    """Read classification independently of sound playback enable/disable.

    Called at the planning request boundary, before background voice work.
    Existing frozen metadata wins over later library edits.
    """
    if not (ball_profile(cfg) and grouped(cfg)) or 'ball_scene_bindings_snapshot' in cfg:
        return cfg
    path = Path(data_dir) / 'clip_sfx' / 'library.sqlite3'
    rows = []
    if path.is_file():
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)) as db:
            for bid, aid, digest, name in db.execute(
                    'SELECT b.id,a.id,b.clip_hash,a.name FROM bindings b JOIN assets a ON a.id=b.asset_id ORDER BY b.id'):
                if scene := scene_for_name(name):
                    rows.append(dict(binding_id=bid, asset_id=aid, clip_hash=digest, scene=scene))
    return dict(cfg, ball_scene_bindings_snapshot=dict(revision=REVISION, bindings=rows))


def categories(cfg):
    snapshot = cfg.get('ball_scene_bindings_snapshot', dict(revision=REVISION, bindings=[]))
    if snapshot.get('revision') != REVISION:
        raise ValueError('牛肉丸场景快照版本无效，未读取当前音效库替代。')
    result = {}
    for row in snapshot['bindings']:
        if row['scene'] not in SCENES or not row.get('clip_hash'):
            raise ValueError('牛肉丸场景绑定无效。')
        result.setdefault(row['clip_hash'], set()).add(row['scene'])
    for identity,note in cfg.get('editorial_snapshot',{}).get('assets',{}).items():
        result[identity.rsplit('|',1)[-1]]=set(note.get('tags',[]))
    return {key: tuple(s for s in SCENES if s in values) for key, values in result.items()}


def describe(clips, cfg):
    """Account actual displayed time; crossfade overlap is shared half/half.

    A multiply-labelled clip splits its contribution between its labels rather
    than counting the same picture twice. Unlabelled pictures keep their time.
    """
    mapped = categories(cfg); seconds = dict.fromkeys(SCENES, 0.); other = 0.; rows = []
    for clip in clips:
        inc = clip.get('incoming_transition', {}).get('frames', 0)
        out = clip.get('outgoing_transition', {}).get('frames', 0)
        shown = (clip['duration_frames'] - (inc + out) / 2) / cfg['fps']
        labels = mapped.get(clip['hash'], ())
        for label in labels:
            seconds[label] += shown / len(labels)
        if not labels:
            other += shown
        rows.append(dict(hash=clip['hash'], path=clip['relative_path'], scenes=list(labels), seconds=shown))
    return dict(revision=REVISION, target_weights=dict(WEIGHTS), scope='batch_soft_target',
                diversity_first=True, require_all_scenes_per_video=False,
                fixed_labelled_share=None, scene_seconds=seconds, other_seconds=other, clips=rows)


def freeze(item, cfg):
    if active(cfg):
        item['ball_scene_snapshot'] = describe(item['clips'], cfg)


def validate(item, cfg):
    snapshot = item.get('ball_scene_snapshot')
    if snapshot is None and not active(cfg):
        return  # Historical manifests keep their original selection and digest.
    if not active(cfg) or snapshot != describe(item['clips'], cfg):
        raise ValueError('牛肉丸场景分配与冻结画面/分类不一致，不能替换历史方案。')


class Selection:
    """Per-plan view over the existing identity/history/dedup index.

    Safety and history acceptance remain delegated to the original index. Only
    candidate ordering and comparisons between already-legal plans change.
    """
    def __init__(self, base, pool, cfg):
        self.base, self.pool, self.cfg = base, pool, cfg
        self.mapped = categories(cfg)
        self.seconds = Counter(); self.other_seconds = 0.
        self.recent_items = deque(maxlen=3)
        self.available_scenes = {scene for c in pool for scene in self.mapped.get(c['hash'], ())}

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _recent(self, key):
        return sum(counts[key] for counts in self.recent_items)

    def _scene_cost(self, clip):
        labels = self.mapped.get(clip['hash'], ())
        total = sum(self.seconds.values())
        if not labels or total <= 0:
            return 0.
        return sum(self.seconds[label] / total - WEIGHTS[label] / 10 for label in labels) / len(labels)

    def candidate_priority(self, clip, fit_cost):
        key = self.base.identity(clip)
        # Timing similarity previously dominated the random/history term. Give
        # unused batch identities an actual ordering priority, with fallback to
        # used identities when the existing bounded search needs them to fit.
        return (self.base.batch[key], self._recent(key), self.base.recent[key],
                math.floor(math.log2(1 + self.base.total[key])),
                fit_cost + 2 * self._scene_cost(clip))

    def candidate_ok(self, selected, clip):
        """Avoid repetitive runs when the reviewed pool offers alternatives."""
        if len(self.available_scenes)<2 or len(selected)<2:
            return True
        labels=self.mapped.get(clip['hash'], ())
        if len(labels)!=1:
            return True
        if not all(self.mapped.get(c['hash'], ())==labels for c in selected[-2:]):
            return True
        used={self.base.identity(c) for c in selected}
        fresh=self.base.batch[self.base.identity(clip)]
        return not any(self.base.identity(other) not in used and
                       self.base.batch[self.base.identity(other)]<=fresh and
                       self.mapped.get(other['hash'], ())!=labels for other in self.pool)

    def validate_mix(self, clips):
        """Reject a single scene taking over one video, without requiring all four."""
        if len(self.available_scenes)<2:
            return True
        shown=describe(clips,self.cfg)
        total=sum(shown['scene_seconds'].values())+shown['other_seconds']
        if total<=0:
            return True
        return max(shown['scene_seconds'].values())<=total*.60+1e-7

    def rotation_weight(self, clip, role):
        key = self.base.identity(clip)
        minimum = min((self.base.batch[self.base.identity(c)] for c in self.pool), default=0)
        freshness = math.exp(-min(24, 6 * (self.base.batch[key] - minimum) + 2 * self._recent(key)))
        scene = max(.5, min(2., 1 - self._scene_cost(clip)))
        return max(1e-12, freshness * scene * self.base.rotation_weight(clip, role))

    def score(self, clips, penalty):
        keys = [self.base.identity(c) for c in clips]; n = max(1, len(keys))
        projected = self.seconds.copy()
        for scene, value in describe(clips, self.cfg)['scene_seconds'].items():
            projected[scene] += value
        total = sum(projected.values())
        drift = sum(abs(projected[scene] / total - WEIGHTS[scene] / 10) for scene in SCENES) if total else 0.
        # Never sacrifice distinct pictures to hit a category quota.
        mix=describe(clips,self.cfg)
        current_total=sum(mix['scene_seconds'].values())+mix['other_seconds']
        dominance=max(mix['scene_seconds'].values())/current_total if current_total else 0.
        return (sum(self.base.batch[k] for k in keys) / n,
                sum(self._recent(k) for k in keys) / n,
                sum(self.base.recent[k] for k in keys) / n,
                sum(math.floor(math.log2(1 + self.base.total[k])) for k in keys) / n,
                max(0.,dominance-.45),penalty + drift)

    def add(self, item, batch=True):
        if batch:
            shown = describe(item['clips'], self.cfg)
            self.seconds.update(shown['scene_seconds']); self.other_seconds += shown['other_seconds']
            self.recent_items.append(Counter(self.base.identity(c) for c in item['clips']))
        self.base.add(item, batch=batch)

    def scene_summary(self):
        keys = {self.base.identity(c) for c in self.pool}
        total = sum(self.seconds.values())
        return dict(revision=REVISION, candidate_identities=len(keys),
                    labelled_candidate_hashes=len({c['hash'] for c in self.pool if c['hash'] in self.mapped}),
                    selected_identities=sum(self.base.batch[k] > 0 for k in keys),
                    total_selected_clips=sum(self.base.batch[k] for k in keys),
                    scene_seconds=dict(self.seconds), other_seconds=self.other_seconds,
                    target_weights=dict(WEIGHTS),
                    actual_labelled_share={s: self.seconds[s] / total if total else 0. for s in SCENES},
                    fixed_labelled_share=None, require_all_scenes_per_video=False,
                    priority='full_pool_diversity_then_soft_scene_time_balance')
