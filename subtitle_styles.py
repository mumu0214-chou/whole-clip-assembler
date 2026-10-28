"""Bottom-caption settings only. Frozen jobs and top text remain independent."""
from copy import deepcopy
from pathlib import Path
import json
import os
import re
import uuid
from subtitle_settings import SMART, ANIMATION, smart, animation

DEFAULT = dict(version=1, font_family='Microsoft YaHei', font_file='', font_sha256='',
    font_size=58., font_weight=800., bold=True, italic=False, underline=False, color='#ffffff', opacity=1.,
    letter_spacing=0., line_spacing=1.22, align='center', stroke_enabled=True,
    stroke_color='#000000', stroke_width=3., shadow_enabled=True, shadow_color='#000000',
    shadow_opacity=.8, shadow_x=0., shadow_y=2., shadow_blur=7., x=.5, y=.72,
    max_width=.84, safe_horizontal=.08, safe_bottom=.14, scale=1.,
    bubble='none', bubble_color='#000000', bubble_opacity=.65, bubble_radius=18.,
    padding_x=22., padding_y=12., bubble_width=0., bubble_height=0.,
    gradient=False, gradient_color='#ffca28',highlight_keywords='',highlight_color='#ffe133',
    highlight_scale=1.22,protected_terms='',vertical_anchor='top',smart=deepcopy(SMART),animation=deepcopy(ANIMATION))
RANGES = dict(font_size=(12,180), opacity=(0,1), letter_spacing=(-3,30), line_spacing=(.8,3),
    stroke_width=(0,16), shadow_opacity=(0,1), shadow_x=(-60,60), shadow_y=(-60,60),
    shadow_blur=(0,60), x=(0,1), y=(0,1), max_width=(.15,1), safe_horizontal=(0,.35),
    safe_bottom=(0,.45), scale=(.25,3), bubble_opacity=(0,1), bubble_radius=(0,100),
    padding_x=(0,100), padding_y=(0,100), bubble_width=(0,1080), bubble_height=(0,900),
    highlight_scale=(1,1.5),font_weight=(100,900))
BUBBLES = {
    '无背景':dict(bubble='none'),
    '黑色半透明底框':dict(bubble='box',bubble_color='#000000',bubble_opacity=.65,bubble_radius=4),
    '白色圆角底框':dict(bubble='box',bubble_color='#ffffff',bubble_opacity=.95,bubble_radius=24,color='#111111',stroke_enabled=False),
    '彩色纯色底框':dict(bubble='box',bubble_color='#1464d2',bubble_opacity=1.,bubble_radius=6,color='#ffffff'),
    '简洁对白气泡':dict(bubble='speech',bubble_color='#ffffff',bubble_opacity=.95,bubble_radius=24,color='#111111',stroke_enabled=False),
    '圆角强调字幕框':dict(bubble='box',bubble_color='#ffc928',bubble_opacity=1.,bubble_radius=38,color='#151515',stroke_enabled=False),
}
FANCY = [
 ('白字黑边',{}),('白字无描边',dict(stroke_enabled=False,shadow_enabled=False)),
 ('黑字白边',dict(color='#111111',stroke_color='#ffffff')),
 ('黄字黑边',dict(color='#ffdc35')),('红字白边',dict(color='#ef3038',stroke_color='#ffffff')),
 ('蓝字黑边',dict(color='#4599ff')),('粉字白边',dict(color='#f653a6',stroke_color='#ffffff')),
 ('黄色强调字',dict(color='#ffe43d',stroke_width=6,font_size=68)),
 ('白字黑阴影',dict(stroke_enabled=False,shadow_x=4,shadow_y=6,shadow_blur=5)),
 ('蓝色阴影字',dict(shadow_color='#1659df',shadow_x=4,shadow_y=5,shadow_blur=8,stroke_enabled=False)),
 ('粉色发光字',dict(stroke_enabled=False,shadow_color='#ff329b',shadow_opacity=1,shadow_x=0,shadow_y=0,shadow_blur=20,color='#ffe8f6')),
 ('渐变文字',dict(gradient=True,color='#ff6699',gradient_color='#ffe25a',stroke_enabled=False,shadow_enabled=False)),
 ('绿色描边字',dict(stroke_color='#20873f',stroke_width=5)),
 ('红黄撞色字',dict(color='#ffe23b',stroke_color='#dd2834',stroke_width=6)),
 ('简洁粗体字幕',dict(stroke_enabled=False,shadow_enabled=False,bold=True,color='#ffffff')),
 ('黄白关键词黑描边',dict(color='#ffffff',stroke_enabled=True,stroke_color='#000000',stroke_width=5,
                          shadow_enabled=True,shadow_y=3,shadow_blur=5,highlight_keywords='150g小包装，薅羊毛，降价',
                          highlight_color='#ffe133',highlight_scale=1.25)),
 ('大字白色厚黑影',dict(color='#ffffff',stroke_enabled=True,stroke_color='#141414',stroke_width=4,
                      shadow_enabled=True,shadow_color='#000000',shadow_opacity=.95,shadow_x=5,shadow_y=7,shadow_blur=9,
                      font_size=78,highlight_keywords='')),
 ('青绿白底字幕条',dict(font_family='Noto Sans SC',font_file='',font_sha256='',font_weight=900,
                      font_size=48,bold=True,color='#49b2ad',stroke_enabled=False,shadow_enabled=False,
                      gradient=False,bubble='teal_bar',bubble_color='#6abebf',bubble_opacity=1.,bubble_radius=13.,
                      bubble_width=944.,bubble_height=100.,padding_x=14.,padding_y=14.,
                      x=.5,y=.5,vertical_anchor='center',max_width=.875,safe_horizontal=.0625,
                      line_spacing=1.2,letter_spacing=0.,scale=1.,align='center',
                      highlight_color='#49b2ad',highlight_scale=1.,
                      smart=dict(SMART,max_lines=1),animation=dict(deepcopy(ANIMATION),
                          entry=dict(ANIMATION['entry'],kind='none'),exit=dict(ANIMATION['exit'],kind='none'),
                          loop=dict(ANIMATION['loop'],kind='none')))),
]

def normalize(value):
    result=deepcopy(DEFAULT)
    if value:
        if not isinstance(value,dict): raise ValueError('字幕样式格式无效')
        if int(value.get('version',1))!=1: raise ValueError('不支持的字幕样式版本')
        result.update({k:v for k,v in value.items() if k in DEFAULT})
    result['version']=1
    for key,(low,high) in RANGES.items():
        val=float(result[key])
        if not low<=val<=high: raise ValueError(f'{key} 应在 {low}～{high} 之间')
        result[key]=val
    for key,default in DEFAULT.items():
        if isinstance(default,bool): result[key]=bool(result[key])
        if key=='color' or key.endswith('_color'):
            if not re.fullmatch('#[0-9a-fA-F]{6}',str(result[key])): raise ValueError('字幕颜色应为 #RRGGBB：'+key)
    if result['align'] not in ('left','center','right'): raise ValueError('字幕对齐无效')
    if result['bubble'] not in ('none','box','speech','teal_bar'): raise ValueError('字幕气泡无效')
    if result['vertical_anchor'] not in ('top','center'):raise ValueError('字幕纵向定位无效')
    if not isinstance(result['highlight_keywords'],str) or len(result['highlight_keywords'])>240:
        raise ValueError('关键词最多240字，用逗号分隔')
    from caption_words import terms
    result['protected_terms']='\n'.join(terms(result['protected_terms']))
    result['smart']=smart(result['smart']);result['animation']=animation(result['animation'])
    return result

def from_config(cfg):
    if cfg.get('subtitle_style'): return normalize(cfg['subtitle_style'])
    pos=cfg.get('subtitle_position','下中')
    y={'顶部':.1,'上中':.34,'居中':.5,'下中':.72,'底部安全区':.80,'自定义':cfg.get('subtitle_custom_y',.72)}[pos]
    return normalize(dict(font_size=cfg.get('subtitle_font_size',58),y=y,
        safe_horizontal=max(cfg.get('subtitle_safe_margin_left',.08),cfg.get('subtitle_safe_margin_right',.08)),
        safe_bottom=cfg.get('subtitle_safe_margin_bottom',.14)))

def preset(index, current=None):
    style=normalize(current)
    # Built-in appearance choices must not erase user-authored vocabulary.
    words=style['highlight_keywords']
    # The teal all-in-one template must not stick to the plain keyword choice.
    # Other custom backgrounds, geometry, fonts and reading settings survive.
    if FANCY[index][0]=='黄白关键词黑描边' and style['bubble']=='teal_bar':
        style['bubble']='none'
    keys=('color','stroke_enabled','stroke_color','stroke_width','shadow_enabled','shadow_color',
          'shadow_opacity','shadow_x','shadow_y','shadow_blur','gradient','gradient_color','bold','italic','underline',
          'highlight_keywords','highlight_color','highlight_scale')
    style.update({k:DEFAULT[k] for k in keys});style.update(FANCY[index][1])
    if words.strip():style['highlight_keywords']=words
    if FANCY[index][0]=='大字白色厚黑影':style['font_size']=78
    return normalize(style)

def preview_text(style):
    """Demonstrate the actual vocabulary without changing scripts or settings."""
    words=[w.strip() for w in re.split(r'[,，、;；\n]+',style.get('highlight_keywords',''))
           if w.strip() and len(w.strip())<=24]
    return ('今天'+words[0]+'，看看这个效果！' if words else
            '点着点着两三百就没了……所以我爸这个99米2斤，')

def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    part=path.with_name(path.name+'.'+uuid.uuid4().hex+'.part')
    try:
        part.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(part,path)
    finally: part.unlink(missing_ok=True)

class Presets:
    def __init__(self,path):
        self.path=Path(path);self.data={'version':1,'default':'','items':{}}
        if self.path.exists():
            try:
                data=json.loads(self.path.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError) as exc:
                raise ValueError('字幕预设文件无法读取；原文件保留。请恢复 subtitle_presets.json.bak 后重新打开编辑器。') from exc
            if data.get('version')!=1 or not isinstance(data.get('items'),dict): raise ValueError('字幕预设文件损坏；原文件保留，请从备份恢复或另存预设文件。')
            data['items']={k:normalize(v) for k,v in data['items'].items()};self.data=data
    def save(self,name,style,kind='full'):
        if not name.strip(): raise ValueError('预设名称不能为空')
        if kind not in ('style','animation','full'):raise ValueError('预设分类无效')
        self.data.setdefault('kinds',{})[name.strip()]=kind
        self.data['items'][name.strip()]=normalize(style);self.flush()
    def rename(self,old,new):
        if not new.strip() or new in self.data['items']: raise ValueError('预设名称为空或已存在')
        self.data['items'][new]=self.data['items'].pop(old)
        if old in self.data.get('kinds',{}):self.data['kinds'][new]=self.data['kinds'].pop(old)
        if self.data['default']==old:self.data['default']=new
        self.flush()
    def delete(self,name):
        self.data['items'].pop(name)
        self.data.get('kinds',{}).pop(name,None)
        if self.data['default']==name:self.data['default']=''
        self.flush()
    def set_default(self,name):
        if name not in self.data['items']:raise ValueError('预设不存在')
        self.data['default']=name;self.flush()
    def flush(self):
        if self.path.is_file():
            import shutil
            shutil.copy2(self.path,self.path.with_suffix('.json.bak'))
        atomic(self.path,self.data)
