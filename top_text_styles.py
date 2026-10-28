"""Measured top-copy typography, shared by preview and frozen batch overlays."""
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
import json
import math
import os

REVISION = 'top_text_visual_v1'
PRESETS = {
    'clean': ('清晰白字 · 微软雅黑', 'msyhbd.ttc', '#ffffff', 2, 1.22),
    'warm': ('暖黄醒目 · 微软雅黑', 'msyhbd.ttc', '#ffe68a', 2, 1.22),
    'compact': ('紧凑黑体 · 黑体', 'simhei.ttf', '#ffffff', 2, 1.12),
    'serif': ('白色宋体 · 宋体', 'simsun.ttc', '#ffffff', 2, 1.25),
}
DEFAULT = dict(revision=REVISION, preset='clean', font_size=60, x=.5, y=.2,
               max_width=.78, stroke=2, shadow=True, align='center')


def normalize(style):
    value=dict(DEFAULT,**(style or {}))
    if value['revision']!=REVISION or value['preset'] not in PRESETS:
        raise ValueError('顶部文案样式版本或字体样式无效')
    for key,low,high in [('font_size',28,140),('x',0,1),('y',0,1),('max_width',.35,.95),('stroke',0,8)]:
        value[key]=float(value[key])
        if not math.isfinite(value[key]) or not low<=value[key]<=high:
            raise ValueError('顶部文案参数超出范围：'+key)
    if value['align'] not in ('left','center','right'):raise ValueError('顶部文案对齐方式无效')
    if not isinstance(value['shadow'],bool):raise ValueError('顶部文案阴影开关无效')
    return {key:value[key] for key in DEFAULT}


def from_config(cfg):
    if cfg.get('top_text_style'):return normalize(cfg['top_text_style'])
    # Opening the editor alone never migrates saved settings or old manifests.
    return normalize(dict(font_size=cfg.get('top_text_font_size',60),
        y=min(.9,(cfg.get('top_text_top',90)+120)/max(1,cfg.get('height',1920))),
        max_width=cfg.get('top_text_max_width',.78),stroke=cfg.get('top_text_stroke',3)))


def font_path(preset):
    if os.name == 'nt':
        path=Path(os.environ.get('WINDIR',r'C:\Windows'))/'Fonts'/PRESETS[preset][1]
    else:
        path=Path('/System/Library/Fonts/PingFang.ttc')
    if not path.is_file():raise ValueError('此字体未安装，请选择其他样式：'+PRESETS[preset][0])
    return path


@lru_cache(maxsize=32)
def font(preset,size):
    from PIL import ImageFont
    return ImageFont.truetype(str(font_path(preset)),size)


def render(text,style,width,height):
    """Rasterize once at export resolution. Explicit line breaks are preserved."""
    from PIL import Image,ImageDraw,ImageFilter
    style=normalize(style)
    if not text.strip():raise ValueError('顶部文案为空，无法预览')
    if len(text)>4000:raise ValueError('顶部文案过长，请精简至4000字以内')
    lines=text.replace('\r','').split('\n')
    scale=height/1920;requested=max(1,round(style['font_size']*scale))
    stroke=round(style['stroke']*scale);pad=max(stroke+2,round(10*scale))
    max_w=width*style['max_width'];max_h=height*.90
    draw=ImageDraw.Draw(Image.new('RGBA',(1,1)))
    size=requested
    while True:
        face=font(style['preset'],size)
        boxes=[draw.textbbox((0,0),line or ' ',font=face,stroke_width=stroke) for line in lines]
        line_widths=[b[2]-b[0] if line else 0 for b,line in zip(boxes,lines)]
        line_h=max(max(b[3]-b[1] for b in boxes),math.ceil(size*PRESETS[style['preset']][4]))
        w=math.ceil(max(line_widths)+2*pad);h=math.ceil(len(lines)*line_h+2*pad)
        if w<=max_w and h<=max_h:break
        size-=1
        if size<max(1,round(24*scale)):
            raise ValueError('文案过长，安全区内无法清晰放下；请增加换行或精简文案')
    layer=Image.new('RGBA',(w,h));p=ImageDraw.Draw(layer)
    for n,(line,b,lw) in enumerate(zip(lines,boxes,line_widths)):
        x=pad if style['align']=='left' else w-pad-lw if style['align']=='right' else (w-lw)/2
        p.text((x-b[0],pad+n*line_h-b[1]),line,font=face,
               fill=PRESETS[style['preset']][2],stroke_width=stroke,stroke_fill='#151515')
    if style['shadow']:
        shadow=Image.new('RGBA',(w,h),(0,0,0,0));alpha=layer.getchannel('A').filter(ImageFilter.GaussianBlur(max(1,2*scale)))
        shadow.putalpha(alpha.point(lambda a:round(a*.45)))
        shadow.alpha_composite(layer);layer=shadow
    return layer,dict(width=w,height=h,font_size=size,requested_font_size=requested,
                      lines=len(lines),auto_reduced=size<requested)


def position(style,metrics,width,height,x=None,y=None,snap=False):
    """Position uses the visible box center, with safe-edge clamping."""
    cx=(style['x'] if x is None else x)*width;cy=(style['y'] if y is None else y)*height
    vx=bool(snap and abs(cx-width/2)<=width*.018)
    hy=bool(snap and abs(cy-height/2)<=height*.018)
    if vx:cx=width/2
    if hy:cy=height/2
    w,h=metrics['width'],metrics['height']
    cx=max(width*.025+w/2,min(width*.975-w/2,cx))
    cy=max(height*.025+h/2,min(height*.975-h/2,cy))
    return dict(x=cx/width,y=cy/height,left=round(cx-w/2),top=round(cy-h/2),
                vertical=vx,horizontal=hy)


def digest(value):
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def freeze_batch(batch,folder):
    cfg=batch['config']
    if not cfg.get('top_text_style'):return
    style=normalize(cfg['top_text_style']);width,height=cfg['width'],cfg['height']
    for item in batch['items']:
        top=item.get('top_text',{})
        if not top.get('enabled') or not top.get('text'):continue
        layer,metrics=render(top['text'],style,width,height)
        pos=position(style,metrics,width,height)
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        path=folder/(str(item['number'])+'.png');layer.save(path)
        payload=dict(revision=REVISION,style=deepcopy(style),text_hash=digest(top['text']),
                     canvas=[width,height],metrics=metrics,position=pos,path=str(path.resolve()),
                     image_hash=sha256(path.read_bytes()).hexdigest())
        item['top_text_visual_snapshot']=dict(payload,digest=digest(payload))


def validate(item,cfg):
    snap=item.get('top_text_visual_snapshot')
    top=item.get('top_text',{})
    required=bool(cfg.get('top_text_style') and top.get('enabled') and top.get('text'))
    if required and not snap:raise ValueError('顶部文案样式未冻结，请重新生成组合')
    if not snap:return
    payload={k:v for k,v in snap.items() if k!='digest'}
    if (snap.get('revision')!=REVISION or digest(payload)!=snap.get('digest') or
        snap['style']!=normalize(cfg.get('top_text_style')) or snap['canvas']!=[cfg['width'],cfg['height']] or
        snap['text_hash']!=digest(top.get('text',''))):raise ValueError('顶部文案冻结样式与本批方案不一致')
    path=Path(snap['path'])
    if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=snap['image_hash']:
        raise ValueError('顶部文案冻结图片丢失或被修改，请重新生成组合')


def media(item,paths):
    snap=item.get('top_text_visual_snapshot')
    if not snap:return None
    key='top_'+snap['image_hash']+'.png';paths[key]=Path(snap['path'])
    return key


def props(item,src):
    snap=item['top_text_visual_snapshot']
    return dict(src=src,left=snap['position']['left'],top=snap['position']['top'],
                width=snap['metrics']['width'],height=snap['metrics']['height'])
