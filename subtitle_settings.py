"""Versioned smart layout and independent animation settings."""
from copy import deepcopy
import math

SMART=dict(version=1,enabled=True,density='normal',punctuation='simple',max_lines=2,
           balanced=True,avoid_orphan=True,protect_specs=True,protect_words=True,pause_assist=True)
ENTRY={'none':'无','fade':'快速淡入','slide_up':'向上滑入','slide_down':'向下滑入',
       'slide_left':'从左滑入','slide_right':'从右滑入','pop':'缩放弹出','bounce':'弹跳进入',
       'reveal':'逐字显现','typewriter':'打字机','wipe':'遮罩擦入'}
EXIT={'none':'无','fade':'淡出','slide_down':'向下滑出','slide_up':'向上滑出',
      'slide_left':'向左滑出','slide_right':'向右滑出','shrink':'缩小消失','cut':'快速消失'}
LOOP={'none':'无','breath':'轻微呼吸','float':'轻微漂浮','bounce':'轻弹强调','shimmer':'微光强调'}
ANIMATION=dict(version=1,entry=dict(kind='fade',duration=.18,delay=0.,intensity=1.,distance=28.,scale=.85,direction='left',easing='ease_out'),
               exit=dict(kind='none',duration=.15,delay=0.,intensity=1.,distance=28.,scale=.85,direction='right',easing='ease_in'),
               loop=dict(kind='none',duration=1.6,delay=0.,intensity=.3,distance=6.,scale=1.03,direction='up',easing='linear'))


def smart(value=None):
    out=deepcopy(SMART);out.update({k:v for k,v in (value or {}).items() if k in SMART})
    if out['version']!=1 or out['density'] not in ('sparse','normal','dense') or out['punctuation'] not in ('simple','minimal','original'):
        raise ValueError('智能字幕设置无效')
    if int(out['max_lines']) not in (1,2):raise ValueError('字幕最多支持两行')
    out['max_lines']=int(out['max_lines'])
    for k,v in SMART.items():
        if isinstance(v,bool):out[k]=bool(out[k])
    return out


def animation(value=None):
    out=deepcopy(ANIMATION)
    if (value or {}).get('version',1)!=1:raise ValueError('字幕动画版本无效')
    for stage,catalog in (('entry',ENTRY),('exit',EXIT),('loop',LOOP)):
        out[stage].update({k:v for k,v in (value or {}).get(stage,{}).items() if k in out[stage]})
        x=out[stage]
        if x['kind'] not in catalog:raise ValueError('字幕动画类型无效：'+stage)
        for k,lo,hi in [('duration',.01,8),('delay',0,4),('intensity',0,2),('distance',0,160),('scale',.25,1.3)]:
            x[k]=float(x[k])
            if not math.isfinite(x[k]) or not lo<=x[k]<=hi:raise ValueError('动画参数超出范围：'+k)
        if x['direction'] not in ('left','right','up','down'):raise ValueError('动画方向无效')
        if x['easing'] not in ('linear','ease_in','ease_out','ease_in_out'):raise ValueError('动画缓动无效')
    return out


def budget(value,frames,fps):
    """Reserve at least 55% of a cue for stable reading, including very short cues."""
    value=animation(value);out=deepcopy(value);flags=[]
    if frames<max(4,round(fps*.22)):
        for stage in ('entry','exit','loop'):out[stage]['kind']='none'
        flags.append('short_cue_animation_disabled')
    allowed=max(0,frames-round(frames*.55))
    requests=[]
    for stage in ('entry','exit'):
        x=out[stage];active=x['kind']!='none'
        requests.append((round(x['duration']*fps)+round(x['delay']*fps)) if active else 0)
    factor=min(1,allowed/max(1,sum(requests)))
    if factor<1:flags.append('animation_compressed')
    for stage in ('entry','exit'):
        x=out[stage];active=x['kind']!='none'
        x['duration_frames']=max(1,math.floor(x['duration']*fps*factor)) if active else 0
        x['delay_frames']=math.floor(x['delay']*fps*factor) if active else 0
    out['stable_frames']=frames-sum(out[s]['duration_frames']+out[s]['delay_frames'] for s in ('entry','exit'))
    out['flags']=flags
    return out
