"""Small, deterministic editorial accents; original pictures/audio never changed."""
from mac_paths import path as _portable_path
from pathlib import Path
import re
import shutil
import os

REVISION='editorial_accents_v1'
LABELS={'原有剪辑':'plain','轻营销 / 趣味':'marketing','食欲 / 沉浸':'appetite'}
ASSETS=Path(_portable_path(r'D:\GPTData\cache\assembler_editorial_assets'))


def freeze(item,cfg):
    from core import object_hash
    mode=cfg.get('edit_style','plain')
    if mode not in LABELS.values():raise ValueError('剪辑风格无效')
    if mode=='plain':item.pop('edit_style_snapshot',None);return
    events=[];fps=cfg['fps'];last=-100
    if mode=='marketing':
        for cue in item.get('subtitles',[]):
            text=cue['text'];kind=None;label=''
            if '听劝' in text:kind,label='reaction','听劝！'
            elif '小包装' in text:kind,label='callout','小包装'
            elif price:=re.search(r'\d+(?:块\d+|\.\d+)(?:元)?',text):kind,label='price',price.group()
            if not kind or cue['start_frame']-last<3*fps:continue
            start=cue['start_frame'];end=min(cue['end_frame'],start+round(1.5*fps))
            if end-start<round(.7*fps):continue
            events.append(dict(kind=kind,text=label,start_frame=start,end_frame=end))
            last=start
            if len(events)>=3:break
    snapshot=dict(revision=REVISION,mode=mode,events=events,
                  reaction_audio=bool(cfg.get('edit_style_audio',True)),picture_accents=bool(cfg.get('edit_style_push',True)),assets=[])
    snapshot['digest']=object_hash(snapshot);item['edit_style_snapshot']=snapshot


def freeze_assets(batch,folder):
    from core import object_hash,sha256
    folder=Path(folder)
    for item in batch['items']:
        s=item.get('edit_style_snapshot')
        if not s:continue
        s['assets']=[]
        if s['reaction_audio']:
            for name in sorted({'bell-ding.wav' if e['kind']=='price' else 'pop-1.wav' for e in s['events']}):
                source=ASSETS/name
                if not source.is_file():raise ValueError('趣味音效素材缺失：'+str(source)+'；可关闭风格提示音继续')
                digest=sha256(source);target=folder/(digest+'.wav');folder.mkdir(parents=True,exist_ok=True)
                if not target.exists():shutil.copy2(source,target)
                if sha256(target)!=digest:raise ValueError('风格音效冻结校验失败')
                s['assets'].append(dict(name=name,path=str(target),sha256=digest))
        s.pop('digest',None);s['digest']=object_hash(s)


def validate(item):
    from core import object_hash,sha256
    s=item.get('edit_style_snapshot')
    if not s:return
    if s.get('revision')!=REVISION or s.get('mode') not in LABELS.values() or s['digest']!=object_hash({k:v for k,v in s.items() if k!='digest'}):
        raise ValueError('剪辑风格快照不一致')
    for e in s['events']:
        if not 0<=e['start_frame']<e['end_frame']<=item['total_frames']:raise ValueError('风格事件时间越界')
    for a in s['assets']:
        if not Path(a['path']).is_file() or sha256(a['path'])!=a['sha256']:raise ValueError('冻结风格音效内容变化')


def overlay(item,cfg,base,ffmpeg,cancel=None):
    s=item.get('edit_style_snapshot') or {}
    if not s.get('assets') or not s.get('reaction_audio'):return None
    validate(item)
    from clip_sfx import run,pcm_info
    base=Path(base);target=round(item['duration']*48000);fps=cfg['fps']
    args=[ffmpeg,'-v','error','-nostdin','-y','-i',str(base)];filters=[];labels=['[0:a]']
    for j,e in enumerate(s['events'],1):
        name='bell-ding.wav' if e['kind']=='price' else 'pop-1.wav'
        asset=next(a for a in s['assets'] if a['name']==name)
        args+=['-i',asset['path']]
        delay=round(e['start_frame']/fps*48000)
        filters.append(f'[{j}:a]aresample=48000,atrim=duration=0.65,asetpts=PTS-STARTPTS,volume=0.18,adelay={delay}S:all=1[s{j}]')
        labels.append(f'[s{j}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0,atrim=end_sample={target}[out]')
    temp=base.with_name(base.stem+'.accent.part.wav')
    try:
        run(args+['-filter_complex',';'.join(filters),'-map','[out]','-ar','48000','-ac','2','-c:a','pcm_s16le',str(temp)],timeout=120,cancel=cancel)
        if pcm_info(temp)['samples']!=target:raise ValueError('风格音效改变了音频长度')
        os.replace(temp,base)
    finally:temp.unlink(missing_ok=True)
    return dict(style=s['mode'],event_count=len(s['events']))
