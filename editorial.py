"""Frozen, optional picture annotations and bounded editorial candidate scoring."""
from copy import deepcopy
from pathlib import Path
import json
import math

REVISION = 'picture_editorial_v1'
TAGS = {'burst':'掰开爆汁','soup':'煮汤','display':'展示','eat':'入口咀嚼',
        'package':'包装规格','cook':'烹饪','serve':'盛盘','texture':'质地特写'}
WORDS = {'burst':('爆汁','掰开','肉汁'), 'soup':('煮汤','清汤','火锅','煮面'),
         'eat':('弹牙','咬','吃','口感'), 'package':('小包装','150克','150g','一百五十克','10包','四包','十包'),
         'cook':('煮','炒','下锅'), 'display':('牛肉丸','牛肉'), 'texture':('紧实','鲜','纹理')}


def key(clip):
    return str(clip.get('relative_path') or clip['path']).replace('\\','/')+'|'+clip['hash']


def validate_note(note, duration):
    if any(t not in TAGS for t in note.get('tags',[])):
        raise ValueError('画面标签不受支持')
    core = note.get('action_core')
    if core is not None and (len(core)!=2 or not all(math.isfinite(float(v)) for v in core)
                            or not 0 <= core[0] < core[1] <= duration + 1e-6):
        raise ValueError('完整动作范围须为原片内的起点～终点（秒）')
    return deepcopy(note)


class Tags:
    def __init__(self, data_root, material_root):
        self.path=Path(data_root)/'editorial_tags.json'
        self.root=str(Path(material_root).resolve()).casefold()

    def rows(self):
        if not self.path.exists():return {}
        return json.loads(self.path.read_text(encoding='utf-8')).get(self.root,{})

    def save(self, changes):
        from file_mutex import file_mutex
        from core import atomic_json
        with file_mutex(self.path.with_suffix('.lock'),timeout=5):
            document=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            rows=document.setdefault(self.root,{})
            for clip,note in changes:
                rows[key(clip)]=validate_note(note,float(clip['duration']))
            atomic_json(self.path,document)


def capture(clips,cfg,data_root):
    if not cfg.get('editorial_enabled'):return clips,cfg
    cfg=deepcopy(cfg); clips=deepcopy(clips)
    if not cfg.get('editorial_snapshot'):
        notes=Tags(data_root,cfg['material_root']).rows()
        cfg['editorial_snapshot']=dict(revision=REVISION,root=str(Path(cfg['material_root']).resolve()),
            assets={key(c):notes[key(c)] for c in clips if key(c) in notes} if notes else {})
    for c in clips:
        c.pop('picture_annotation',None)
        if cfg['editorial_snapshot']['assets'] and key(c) in cfg['editorial_snapshot']['assets']:
            c['picture_annotation']=validate_note(cfg['editorial_snapshot']['assets'][key(c)],c['duration'])
    return clips,cfg


def validate_window(clip,timing,cfg):
    if not cfg.get('editorial_enabled'):return
    note=clip.get('picture_annotation',{})
    core=note.get('action_core')
    if core and (timing['source_in_seconds']>core[0]+1e-6 or timing['source_out_seconds']<core[1]-1e-6):
        raise ValueError('ACTION_CORE_PROTECTED：裁切会截断已标注的完整动作，请重选镜头')


def context(package,cfg):
    """Use measured word times only. No invented word times for unaligned audio."""
    if not cfg.get('editorial_enabled') or not package:return {}
    voice=package.get('voice') or {}
    aligned=voice.get('external_alignment') or {}
    words=aligned.get('words',[])
    if not words:return dict(reason='没有实测逐字对齐；仅保护已标动作，不猜口播时机')
    text=package.get('speech_text_override') or package.get('speech_text','')
    offset=float(voice.get('effective_voice_start',0))
    events=[];pauses=[]
    joined='';positions=[]
    for w in words:
        value=str(w.get('word',w.get('text',''))).strip()
        positions.extend([w]*len(value));joined+=value
    for tag,terms in WORDS.items():
        for term in terms:
            index=joined.find(term)
            while index>=0:
                first,last=positions[index],positions[index+len(term)-1]
                events.append(dict(start=max(0,float(first['start'])-offset),end=max(0,float(last['end'])-offset),tags=[tag]))
                index=joined.find(term,index+len(term))
    for i,w in enumerate(words):
        if i and float(w['start'])-float(words[i-1]['end'])>=.12:
            pauses.append((float(w['start'])+float(words[i-1]['end']))/2-offset)
        if str(w.get('word',w.get('text',''))).rstrip().endswith(('，','。','！','？','；',',','.','!','?')):
            pauses.append(float(w['end'])-offset)
    return dict(events=events,pauses=sorted(set(pauses)),alignment='measured',text=text)


def prepare_packages(packages,cfg,log):
    if not cfg.get('editorial_enabled') or not packages:return packages
    result=deepcopy(packages)
    for p in result:
        if not p or p.get('voice_error'):continue
        voice=p.get('voice') or {}
        if p.get('narration_enabled') and voice and not voice.get('external_alignment'):
            from subtitle_alignment import alignment
            try:
                voice['external_alignment']=alignment(voice['voice_processed'],p.get('speech_text_override') or p['speech_text'],
                    voice.get('effective_voice_start',0),voice['effective_voice_end'])
                voice['alignment_identity']=voice['external_alignment']['cache_key']
                log('已准备实测口播停顿，用于画面匹配和节奏选择。')
            except (ValueError,OSError) as exc:
                log('口播匹配缺少可靠对齐，本条仅使用画面标签和动作保护：'+str(exc))
        p['_editorial_context']=context(p,cfg)
    return result


def candidate_score(clips,cfg,package):
    if not cfg.get('editorial_enabled'):return 0.
    ctx=(package or {}).get('_editorial_context') or context(package,cfg)
    fps=cfg['fps'];matched=0.;possible=0.;pause_cost=0.
    from ball_scene_rotation import categories
    inferred=categories(cfg)
    for i,c in enumerate(clips):
        start=c['timeline_start_frame']/fps;end=start+c['duration_frames']/fps
        tags=set(c['picture_annotation']['tags'] if 'picture_annotation' in c else inferred.get(c['hash'],()))
        if cfg.get('edit_style') in ('marketing','appetite'):
            if i==0 and tags and not tags.intersection(('serve','display','burst','eat')):pause_cost+=.6
            if i and Path(c['path']).parent==Path(clips[i-1]['path']).parent:pause_cost+=.7
        for e in ctx.get('events',[]):
            overlap=max(0,min(end,e['end'])-max(start,e['start']))
            possible+=overlap
            if tags.intersection(e['tags']):matched+=overlap
        if i and ctx.get('pauses'):
            pause_cost+=min(.6,min(abs(start-p) for p in ctx['pauses']))
        core=c.get('picture_annotation',{}).get('action_core')
        if core:pause_cost+=min(.3,max(0,c['source_out_seconds']-core[1]))
    rhythm=0.
    if cfg.get('edit_style') in ('marketing','appetite'):
        durations=[c['duration_frames']/fps for c in clips]
        rhythm=.4*sum(d<1.8 for d in durations)/max(1,len(durations))+.08*max(0,len(clips)-9)
        if cfg.get('edit_style')=='marketing':rhythm+=.05*sum(max(0,d-4.5) for d in durations)
    return round((1-matched/possible if possible else 0)+pause_cost/max(1,len(clips)-1)+rhythm,6)


def order_sequence(seq,cfg,seed,package):
    """Reorder only the already selected G shots; preserve all rotation choices."""
    if not cfg.get('editorial_enabled') or not package:return seq
    from timeline_processing import bake_timeline
    from ball_pacing import validate_timeline
    from ball_opening import validate as validate_opening
    from ball_scene_rotation import categories
    mapped=categories(cfg)
    def cost(candidate):
        out,_,_=bake_timeline(candidate,cfg,seed)
        validate_timeline(out,cfg);validate_opening(out,cfg)
        for i in range(2,len(out)):
            tags=[set(c.get('picture_annotation',{}).get('tags',mapped.get(c['hash'],()))) for c in out[i-2:i+1]]
            if tags[0]&tags[1]&tags[2]:return float('inf')
        return candidate_score(out,cfg,package)
    best=list(seq)
    try:score=cost(best)
    except ValueError:return seq
    # Bounded local search. No new material, timing guesses or mandatory classes.
    for _ in range(2):
        changed=False
        for i in range(len(best)-1):
            for j in range(i+1,min(len(best),i+4)):
                if best[i].get('planning_block')!=best[j].get('planning_block'):continue
                trial=list(best);trial[i],trial[j]=trial[j],trial[i]
                try:merit=cost(trial)
                except (ValueError,RuntimeError):continue
                if merit+1e-6<score:best,score=trial,merit;changed=True
        if not changed:break
    return best


def finalize(batch):
    cfg=batch['config']
    if not cfg.get('editorial_enabled'):return batch
    from core import object_hash
    for item in batch['items']:
        snapshot=dict(revision=REVISION,annotations=[dict(identity=key(c),note=c.get('picture_annotation')) for c in item['clips']],
                      matching='soft_after_diversity',pace='measured_pause_and_action_boundary',
                      matched_score=candidate_score(item['clips'],cfg,dict(item.get('script_package') or {},voice=item.get('voice'))))
        item['editorial_snapshot']=snapshot
        item['editorial_digest']=object_hash(snapshot)
    return batch


def validate(item,cfg):
    if not item.get('editorial_snapshot'):return
    from core import object_hash
    if object_hash(item['editorial_snapshot'])!=item.get('editorial_digest'):
        raise ValueError('画面标签快照指纹不一致')
    expected=cfg.get('editorial_snapshot',{}).get('assets',{})
    for c in item['clips']:
        if c.get('picture_annotation')!=expected.get(key(c)):
            raise ValueError('画面标签与冻结批次不一致')
        validate_window(c,c,cfg)
