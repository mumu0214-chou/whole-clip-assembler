"""Explicit, path-scoped SK preferences; never change eligibility or identities."""
from collections import Counter
from copy import deepcopy
from pathlib import Path, PurePosixPath
import math

REVISION='sk_material_bias_v1'
KEY='sk_material_bias_rules'
NOTE='提升选中机会，非固定成片百分比；类别偏好、去重、首镜及裁切许可仍生效。重叠时更具体的路径优先，不叠乘。'


def normalize(values):
    from sk_materials import TYPES
    if not isinstance(values,list) or len(values)>1000:
        raise ValueError('SK素材偏好应为列表，最多1000项')
    result=[];seen=set()
    for row in values:
        if not isinstance(row,dict):raise ValueError('SK素材偏好格式无效')
        raw=row.get('path','')
        if not isinstance(raw,str) or not raw or raw!=raw.strip():
            raise ValueError('素材偏好路径不能为空或带首尾空格')
        raw=raw.replace('\\','/');path=PurePosixPath(raw)
        if path.is_absolute() or ':' in raw or any(p in ('','..','.') for p in raw.split('/')) or len(path.parts)<2 or path.parts[0] not in TYPES:
            raise ValueError('请选择SK类别下的子目录或具体文件：'+raw)
        kind=row.get('kind')
        if kind not in ('folder','file'):raise ValueError('素材偏好请选择目录或文件')
        if kind=='file':
            from core import VIDEO_EXTS
            if path.suffix.lower() not in VIDEO_EXTS:raise ValueError('素材偏好文件必须是视频：'+raw)
        try:factor=float(row.get('factor'))
        except (ValueError,TypeError):raise ValueError('提升倍数应为1～100') from None
        if isinstance(row.get('factor'),bool) or not math.isfinite(factor) or not 1<=factor<=100:
            raise ValueError('提升倍数应为1～100')
        key=raw.casefold()
        if key in seen:raise ValueError('素材偏好路径重复：'+raw)
        seen.add(key);result.append(dict(path=raw,kind=kind,factor=factor))
    return sorted(result,key=lambda r:r['path'].casefold())


def rules(cfg):return normalize(cfg.get(KEY,[]))


def match(relative,row):
    value=relative.replace('\\','/').casefold();prefix=row['path'].casefold()
    return value==prefix if row['kind']=='file' else value.startswith(prefix+'/')


def winner(relative,values):
    matches=[r for r in values if match(relative,r)]
    return max(matches,key=lambda r:(len(PurePosixPath(r['path']).parts),r['kind']=='file'),default=None)


def adjust(candidates,weights,values,factor_by_path=None):
    if not values or not candidates:return weights
    factors=[factor_by_path[c['relative_path']] if factor_by_path is not None and c['relative_path'] in factor_by_path
             else (winner(c['relative_path'],values) or {}).get('factor',1.) for c in candidates]
    if all(f==1 for f in factors):return weights
    # Redistribute within each type; a preferred A scene must not take B's
    # category preference simply because A contains more explicit boosts.
    before=Counter();after=Counter()
    for c,w,f in zip(candidates,weights,factors):
        before[c['category']]+=w;after[c['category']]+=w*f
    return [w*f*before[c['category']]/after[c['category']] if after[c['category']] else 0.
            for c,w,f in zip(candidates,weights,factors)]


def snapshot(cfg):return dict(revision=REVISION,rules=deepcopy(rules(cfg)))


def validate(saved,cfg):
    if saved is None:
        if rules(cfg):raise ValueError('SK素材偏好缺少冻结快照')
    elif saved!=snapshot(cfg):raise ValueError('SK素材偏好与冻结配置不一致')


def selected_path(path,kind,root):
    base=Path(root).resolve();value=Path(path).resolve()
    if not value.is_relative_to(base):raise ValueError('只能选择当前SK素材目录内的素材')
    relative=value.relative_to(base).as_posix()
    return normalize([dict(path=relative,kind=kind,factor=3.)])[0]['path']


def inventory(clips,cfg):
    from sk_materials import preferences
    values=rules(cfg)
    prefs=preferences(cfg)
    return [dict(row,available_files=sum(match(c['relative_path'],row) and c.get('enabled',False)
        and c.get('reviewed',False) and prefs[c['category']]>0
        for c in clips)) for row in values]


def summarize(batch):
    values=rules(batch['config']);rows=[];total=batch['sk_summary']['total_seconds']
    available={r['path']:r['available_files'] for r in batch.get('sk_material_bias_inventory',[])}
    seconds=Counter();counts=Counter();matched={}
    for item in batch['items'] if values else []:
        clips=item.get('clips',[])
        for j,c in enumerate(clips):
            path=c['relative_path']
            if path not in matched:matched[path]=winner(path,values)
            row=matched[path]
            if row is None:continue
            overlap=0
            if j:overlap+=max(0,clips[j-1]['timeline_start_frame']+clips[j-1]['duration_frames']-c['timeline_start_frame'])/2
            if j+1<len(clips):overlap+=max(0,c['timeline_start_frame']+c['duration_frames']-clips[j+1]['timeline_start_frame'])/2
            seconds[row['path']]+=(c['duration_frames']-overlap)/batch['config']['fps'];counts[row['path']]+=1
    for row in values:
        amount=seconds[row['path']]
        rows.append(dict(row,available_files=available.get(row['path']),selected_clips=counts[row['path']],
                         seconds=amount,actual_percent=100*amount/total if total else 0.))
    return dict(revision=REVISION,rules=rows,note=NOTE)
