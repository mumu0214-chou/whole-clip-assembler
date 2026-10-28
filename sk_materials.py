"""SK directory semantics; media, review flags and timing cache remain shared."""
from mac_paths import path as _portable_path
from collections import Counter
from copy import deepcopy
from pathlib import Path, PurePosixPath
import math
import os
import time

REVISION = 'sk_diverse_v1'
FORMAT = 'sk_diverse'
ROOT = _portable_path(r'D:\DY video\人工处理素材\牛肉\sk')
TYPES = ('A', 'B', 'C', 'E', 'F', 'G', '切肉', '烫肉')
LABELS = dict(A='套餐组合', B='手持盒装肉', C='手持盘装肉', E='肉品与火锅',
              F='档口展示', G='其他细节', 切肉='切肉', 烫肉='烫肉')
PREFERENCES = dict(zip(TYPES, (20., 40., 10., 10., 10., 10., 0., 0.)))
SCHEME_KEYS = ('material_root', 'material_format', 'selection_rule_version', 'group_folders',
               'width', 'height', 'opening_c_min_per_batch', 'match_video_to_voice',
               'processing_ab_duration_enabled', 'processing_trim', 'processing_micro_trim_enabled',
               'sk_fit', 'grouped_search_revision', 'package_matching_revision',
               'package_count_matching_enabled', 'voice_fit_capacity_revision', 'sk_material_bias_rules')


def active(cfg):
    return cfg.get('fresh_editing_scheme') == REVISION and cfg.get('editing_profile_id', 'fresh_beef_standard') == 'fresh_beef_standard'


def configure(cfg, scheme):
    """Switch one beef strategy, keeping product/spec/voice identity intact."""
    out = deepcopy(cfg)
    previous = REVISION if active(cfg) else 'standard'
    if scheme == previous:
        return out
    saved = deepcopy(out.get('fresh_scheme_settings', {}))
    saved[previous] = {k: deepcopy(out[k]) for k in SCHEME_KEYS if k in out}
    if scheme not in ('standard', REVISION):
        raise ValueError('未知牛肉剪辑方案')
    selected = saved.get(scheme)
    if selected is None:
        if scheme == 'standard':
            raise ValueError('原牛肉方案快照缺失，不能猜测或覆盖原设置')
        selected = dict(material_root=ROOT, material_format=FORMAT, selection_rule_version=REVISION,
                        group_folders={k: k for k in TYPES}, width=1080, height=1440,
                        opening_c_min_per_batch=0, match_video_to_voice=True, sk_fit='contain',
                        grouped_search_revision='grouped_frozen_slot_search_v1',
                        voice_fit_capacity_revision='voice_fit_capacity_v2',
                        package_matching_revision='package_matching_removed_v1',
                        package_count_matching_enabled=False,
                        processing_ab_duration_enabled=False, processing_trim=False,
                        processing_micro_trim_enabled=False)
        selected['sk_material_bias_rules']=[]
    for k in SCHEME_KEYS:
        out.pop(k, None)
    out.update(deepcopy(selected), fresh_editing_scheme=scheme, fresh_scheme_settings=saved)
    out.setdefault('sk_type_percentages', deepcopy(PREFERENCES))
    return out


def preferences(cfg):
    values = cfg.get('sk_type_percentages', PREFERENCES)
    if set(values) != set(TYPES):
        raise ValueError('SK类别时长偏好需要包含全部八类')
    result = {k: float(values[k]) for k in TYPES}
    if any(not math.isfinite(v) or v < 0 for v in result.values()):
        raise ValueError('SK类别百分比必须是有限的非负数')
    if abs(sum(result.values()) - 100) > .00001:
        raise ValueError(f'SK类别百分比合计必须为100%，当前为{sum(result.values()):g}%')
    return result


def validate_config(cfg):
    if cfg.get('material_format')==FORMAT and not active(cfg):
        raise ValueError('SK方案标记与牛肉品类身份不一致，请重新选择剪辑方案')
    if not active(cfg):
        return
    preferences(cfg)
    from sk_material_bias import rules as material_bias_rules
    material_bias_rules(cfg)
    if Path(cfg['material_root']).resolve() != Path(ROOT).resolve():
        raise ValueError('SK方案只允许使用：' + ROOT)
    if cfg.get('sk_fit', 'contain') not in ('contain', 'cover'):
        raise ValueError('SK画面适配请选择保留完整画面或裁切铺满')


def classify(relative, root=ROOT):
    from grouped_materials import root_key
    path = PurePosixPath(str(relative).replace('\\', '/'))
    parts = path.parts
    if path.is_absolute() or '..' in parts or len(parts) < 2 or parts[0] not in TYPES:
        raise ValueError('SK目录待确认：' + str(relative))
    folders = parts[1:-1]
    nonnumeric = [x for x in folders if not x.isdecimal()]
    numeric = [x for x in folders if x.isdecimal()]
    if len(nonnumeric) > 1 or len(numeric) > 1 or (numeric and not folders[-1].isdecimal()):
        raise ValueError('SK异常层级待确认：' + path.as_posix())
    library = root_key(root)
    scene_path = '/'.join(parts[:-2]) if numeric else '/'.join(parts[:-1])
    # Direct numeric groups are their own family, never one giant type group.
    if numeric and not nonnumeric:
        scene_path = '/'.join(parts[:-1])
    scene = library + '|scene:' + scene_path if folders else ''
    placement = library + '|placement:' + '/'.join(parts[:-1]) if numeric else ''
    return dict(group=parts[0], category=parts[0], relative_path=path.as_posix(),
                sk_revision=REVISION, sk_library=library, sk_scene_group=scene,
                sk_similar_group=scene if nonnumeric else '',
                sk_placement_group=placement, sk_scene_path=scene_path if folders else '',
                sk_placement_path='/'.join(parts[:-1]) if numeric else '',
                source_group=scene, combo_id=scene, asset_kind='independent',
                member_number=int(numeric[0]) if numeric else None,
                mutex_group=placement, can_open=parts[0] in ('A', 'E'), timeline_role='自由混剪')


def scene_key(clip):
    return clip.get('sk_scene_group') or clip.get('sk_library', '') + '|independent:' + clip['hash']


def unit_key(clip):
    return clip.get('sk_placement_group') or 'file:' + clip['hash']


def hard_conflict(clips):
    hashes, identities, placements = set(), set(), set()
    for c in clips:
        h, ident, placement = c.get('hash'), c.get('identity_key'), c.get('sk_placement_group')
        if h in hashes or ident in identities:
            return 'SK_DUPLICATE_CONTENT', '同条成片不能重复使用相同视频内容'
        if placement and placement in placements:
            return 'SK_PLACEMENT_CONFLICT', '同一数字摆放组只能选择一个版本：' + c['sk_placement_path']
        hashes.add(h); identities.add(ident)
        if placement:
            placements.add(placement)
    return None


def opening_allowed(clip, cfg):
    return preferences(cfg).get(clip['category'], 0) > 0 and bool(clip.get('can_open'))


def validate_sequence(clips, cfg, snapshot=None):
    if not clips or len(clips) > cfg['max_clips']:
        raise ValueError('SK镜头为空或超过镜头数量上限')
    prefs = preferences(cfg)
    for clip in clips:
        expected = classify(clip['relative_path'], cfg['material_root'])
        for k in ('category', 'sk_library', 'sk_scene_group', 'sk_placement_group'):
            if clip.get(k) != expected[k]:
                raise ValueError('SK分组身份与冻结素材不一致：' + clip['relative_path'])
        if prefs[clip['category']] <= 0:
            raise ValueError('SK零占比类别不能进入本批：' + clip['category'])
        if not Path(clip['path']).resolve().is_relative_to(Path(cfg['material_root']).resolve()):
            raise ValueError('SK素材超出本库边界')
    if not opening_allowed(clips[0], cfg):
        raise ValueError('SK首镜没有人工资格或所属类别为0%')
    if err := hard_conflict(clips):
        raise ValueError('：'.join(err))


class Review:
    """Use the existing path/hash scoped opening and safe-cut tables."""
    def __init__(self, store, root):
        from grouped_materials import Coverage
        self.coverage = Coverage(store, root)
        self.store, self.root = store, self.coverage.root

    def annotate(self, clips):
        self.coverage.annotate(clips)
        with self.store.connect() as db:
            saved = {(p, h): bool(excluded) for p, h, excluded in db.execute(
                'SELECT path,hash,excluded FROM grouped_opening_review WHERE root=?', (self.root,))}
        for c in clips:
            c['can_open'] = not saved.get((c['relative_path'], c['hash']), c['category'] not in ('A', 'E'))
            c['opening_excluded'] = not c['can_open']
        return clips

    def exclude_opening(self, clips, excluded):
        self.coverage.exclude_opening(clips, excluded)
        for c in clips:
            c['can_open'] = not excluded

    def safe_cut(self, clips, enabled):
        self.coverage.safe_cut(clips, enabled)

    def snapshot(self, clips):
        return dict(revision='grouped_coverage_v1', sk_revision=REVISION, root=self.root, pairs=[],
                    assets=[dict(path=c['relative_path'], hash=c['hash'], safe_cut=bool(c.get('safe_cut_confirmed')),
                                 can_open=bool(c.get('can_open')), opening_excluded=not c.get('can_open'),
                                 scene=c['sk_scene_group'], placement=c['sk_placement_group']) for c in clips])


def summary(clips, warnings=()):
    result = []
    for typ in TYPES:
        rows = [c for c in clips if c['category'] == typ]
        live = [c for c in rows if c.get('enabled') and c.get('reviewed')]
        result.append(dict(type=typ, label=LABELS[typ], files=len(rows),
                           similar_groups=len({c['sk_similar_group'] for c in rows if c.get('sk_similar_group')}),
                           placement_groups=len({c['sk_placement_group'] for c in rows if c.get('sk_placement_group')}),
                           independent=sum(not c.get('sk_scene_group') for c in rows),
                           available_scenes=len({scene_key(c) for c in live}),
                           opening_candidates=sum(bool(c.get('can_open')) for c in live)))
    return dict(types=result, files=len(clips), warnings=list(warnings))


def preflight(clips, cfg):
    prefs = preferences(cfg)
    active_clips = [c for c in clips if c.get('enabled') and c.get('reviewed') and prefs[c['category']] > 0]
    missing = [k for k in TYPES if prefs[k] > 0 and not any(c['category'] == k for c in active_clips)]
    if missing:
        raise ValueError('SK以下类别比例大于0但没有启用且复核的素材：' + '、'.join(missing) + '。请补素材或主动调整本批比例。')
    if not any(opening_allowed(c, cfg) for c in active_clips):
        raise ValueError('SK没有合法首镜：请启用A/E候选，或在素材清单明确标记其他类型的具体首镜。')
    return active_clips


def scan_materials(cfg, store, tool, log, stop=None):
    from core import VIDEO_EXTS, probe
    validate_config(cfg)
    start = time.perf_counter()
    root = Path(cfg['material_root']).resolve()
    clips, warnings = [], []
    if not root.is_dir():
        raise ValueError('SK素材目录不存在：' + str(root))
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs.sort(); files.sort()
        for name in dirs[:]:
            p = Path(folder) / name
            if p.is_symlink() or p.is_junction() or not p.resolve().is_relative_to(root):
                warnings.append('待确认目录链接，未跨库扫描：' + str(p)); dirs.remove(name)
        for name in files:
            if stop and stop.is_set():
                raise ValueError('SK扫描已取消')
            p = Path(folder) / name
            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            relative = p.relative_to(root).as_posix()
            try:
                if not p.resolve().is_relative_to(root):
                    raise ValueError('文件链接超出SK素材范围')
                identity = classify(relative, root)
                meta, hit = store.inspect(p, tool)
                if 'display_width' not in meta:
                    import json
                    meta.update(probe(p, tool))
                    with store.connect() as db:
                        db.execute('UPDATE media_cache SET payload=? WHERE path=?',
                                   (json.dumps(meta, ensure_ascii=False), meta['path']))
                if not meta.get('has_video') or meta['duration'] <= 0:
                    raise ValueError('没有有效视频流/时长')
                clip = dict(meta, **identity, display=relative, **store.flags(meta['hash']),
                            identity_key='sha256:' + meta['hash'], source_id='', project_link={})
                clips.append(clip)
                if not hit:
                    log(f'SK {relative} / {meta["duration"]:.3f}秒 / 显示{meta["display_width"]}×{meta["display_height"]}')
            except Exception as exc:
                warnings.append(f'待确认 {relative}：{exc}')
    Review(store, root).annotate(clips)
    log(f'SK扫描耗时{time.perf_counter()-start:.3f}秒：{len(clips)}个文件；异常待确认{len(warnings)}项。')
    return clips, warnings
