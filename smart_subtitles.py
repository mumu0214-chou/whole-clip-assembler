"""Prepare text, acoustic timing, font-aware lines and QC once, before rendering."""
from copy import deepcopy
from bisect import bisect_left
from functools import lru_cache
import ctypes
from ctypes import wintypes
import difflib
import math
import re
import time
import unicodedata
from subtitle_settings import smart, budget
from caption_words import reading_units, REVISION as WORD_GUARD

REVISION='smart_caption_v1'
HARD=re.compile(r'…+|\.{3,}|[。！？!?；;\n]+|(?<!\d)\.(?!\d)')
SPEC=re.compile(r'(?:每包|每份|每袋)?(?:\d+(?:\.\d+)?(?:到|至|[-～~])?)+(?:%|％|小时|分钟|分钟内|斤|公斤|千克|kg|KG|[gG]|克|元|米|包|袋|盒|份|件|天|秒|毫升|ml)(?:独立装)?(?:\d+(?:\.\d+)?(?:斤|元|米|包|克|[gG]))*(?:牛肉|独立装|(?:每|[一两二])(?:斤|包|份|袋))?|[一二两三四五六七八九十百千万]+')
DECIMAL=re.compile(r'(?<!\d)\d+\.\d+(?!\d)')
PRICE_GUARD='spoken_price_v1'
# Prices are reading units, including colloquial yuan/jiao/fen spellings. A
# bare fraction must not consume the count in "49块10包" or "49块9折".
_MONEY_DIGIT=r'[0-9零〇一二两三四五六七八九]'
_MONEY_CN=r'[零〇一二两三四五六七八九十百千万亿]+'
_MONEY_NUMBER=rf'(?:(?:\d{{1,3}}(?:[,，]\d{{3}})+|\d+)(?:[.．点]{_MONEY_DIGIT}+)?|{_MONEY_CN}(?:点{_MONEY_DIGIT}+)?)'
_QUANTITY=r'(?:公斤|千克|毫升|小时|分钟|斤|克|[gG]|[kK][gG]|[mM][lL]|包|袋|盒|份|件|瓶|箱|套|碗|盘|只|条|颗|个|枚|人|天|秒|折|%|％|元|块|米)'
_FRACTION_END=rf'(?!{_QUANTITY})(?:(?!{_MONEY_DIGIT})|(?=[一两](?:包|袋|盒|份|件|瓶|箱|套|碗|盘|斤)))'
_QUANTITY_AMOUNT=rf'{_MONEY_NUMBER}(?:[ \t]*(?:到|至|[-～~—])[ \t]*{_MONEY_NUMBER})?[ \t]*{_QUANTITY}'
_BARE_FRACTION=rf'(?!{_QUANTITY_AMOUNT}){_MONEY_DIGIT}{{1,2}}{_FRACTION_END}'
_MINOR=rf'(?:{_MONEY_DIGIT}{{1,2}}[角毛](?:{_MONEY_DIGIT}分|{_BARE_FRACTION})?|{_MONEY_DIGIT}{{1,2}}分|{_BARE_FRACTION})'
_PREFIX=r'(?:人民币[ \t]*|[¥￥][ \t]*)'
PRICE=re.compile(
    rf'(?:{_PREFIX})?{_MONEY_NUMBER}(?:[ \t]*(?:到|至|[-～~—])[ \t]*{_MONEY_NUMBER})?'
    rf'[ \t]*(?:元|块|米|圆)(?:钱)?(?:[ \t]*{_MINOR})?'
    rf'|{_PREFIX}{_MONEY_NUMBER}'
    rf'|{_MONEY_NUMBER}[角毛](?:钱)?(?:{_MONEY_DIGIT}分|{_BARE_FRACTION})?'
    rf'|{_MONEY_NUMBER}分(?!钟)钱?'
    rf'|(?:\d+[.．点]{_MONEY_DIGIT}+|{_MONEY_CN}点{_MONEY_DIGIT}+)')


def graphemes(text):
    """Preserve combining marks, variation selectors, ZWJ emoji and flag pairs."""
    out=[];start=0;regional=0
    for i,c in enumerate(text):
        n=ord(c);ri=0x1f1e6<=n<=0x1f1ff
        joins=(i>0 and (unicodedata.combining(c) or c=='\u200d' or text[i-1]=='\u200d'
                      or 0xfe00<=n<=0xfe0f or 0x1f3fb<=n<=0x1f3ff or (ri and regional%2)))
        if i and not joins:out.append((text[start:i],start,i));start=i
        regional=regional+1 if ri else 0
    if text:out.append((text[start:],start,len(text)))
    return out


def normalize_source(raw):
    result='';mapping=[]
    for cluster,a,b in graphemes(raw):
        part=unicodedata.normalize('NFC',cluster).replace('\r','')
        result+=part;mapping.extend([(a,b)]*len(part))
    return result,mapping


def display(text,mode):
    if mode=='original':return text
    # Decimal dots are protected; units, prices and product facts never change.
    text=re.sub(r'…+|\.{3,}|[,，、。；;：:]|(?<!\d)\.(?!\d)','',text)
    if mode=='minimal':text=re.sub(r'[!?！？]','',text)
    return text.strip()


class FontMeasure:
    """Windows actual selected face/weight/italic metrics, no character-count surrogate."""
    def __init__(self,style,width=1080,height=1920):
        self.s=style;self.unit=height/1920*style['scale'];self.size=style['font_size']*self.unit
        self.spacing=style['letter_spacing']*self.unit
        self.gdi=ctypes.windll.gdi32
        self.gdi.CreateCompatibleDC.restype=wintypes.HDC
        self.gdi.CreateFontW.restype=wintypes.HANDLE
        self.gdi.CreateFontW.argtypes=[ctypes.c_int]*5+[wintypes.DWORD]*8+[wintypes.LPCWSTR]
        self.gdi.SelectObject.argtypes=[wintypes.HDC,wintypes.HANDLE];self.gdi.SelectObject.restype=wintypes.HANDLE
        self.gdi.DeleteObject.argtypes=[wintypes.HANDLE];self.gdi.DeleteDC.argtypes=[wintypes.HDC]
        self.gdi.GetTextExtentPoint32W.argtypes=[wintypes.HDC,wintypes.LPCWSTR,ctypes.c_int,ctypes.c_void_p]
        if style.get('font_file'):self.gdi.AddFontResourceExW(style['font_file'],0x10,0)
        self.dc=self.gdi.CreateCompatibleDC(None)
        self.font=self.gdi.CreateFontW(-round(self.size*16),0,0,0,round(style.get('font_weight',800)) if style['bold'] else 400,
            int(style['italic']),0,0,1,0,0,4,0,style['font_family'])
        if not self.font:raise ValueError('字体测量失败，需要重新选择字体')
        self.old=self.gdi.SelectObject(self.dc,self.font)
        self.cache={}
    def measure(self,text):
        if text not in self.cache:
            size=wintypes.SIZE();encoded=text.encode('utf-16-le');count=len(encoded)//2
            if not self.gdi.GetTextExtentPoint32W(self.dc,text,count,ctypes.byref(size)):
                raise ValueError('实际字体宽度测量失败')
            self.cache[text]=size.cx/16+max(0,len(graphemes(text))-1)*self.spacing
        return self.cache[text]
    def close(self):
        self.gdi.SelectObject(self.dc,self.old);self.gdi.DeleteObject(self.font);self.gdi.DeleteDC(self.dc)


def geometry(style,width,height):
    unit=height/1920*style['scale']
    outer=min(width*(1-2*style['safe_horizontal']),width*style['max_width'])
    if style['bubble_width']>0:outer=min(outer,style['bubble_width']*unit)
    horizontal=0;maximum_scale=1
    for stage in ('entry','exit','loop'):
        x=style['animation'][stage]
        if x['kind'] in ('slide_left','slide_right'):horizontal=max(horizontal,x['distance']*unit*x['intensity'])
        if x['kind'] in ('pop','shrink','breath'):maximum_scale=max(maximum_scale,1+max(0,x['scale']-1)*x['intensity'])
    outer=(outer-2*horizontal)/maximum_scale
    effect=(style['stroke_width'] if style['stroke_enabled'] else 0)+(max(abs(style['shadow_x']),abs(style['shadow_y']))+style['shadow_blur'] if style['shadow_enabled'] else 0)
    edge=max(4,effect*unit)
    padding=style['padding_x']*unit if style['bubble']!='none' else 0
    icon=80*unit if style['bubble']=='teal_bar' else 0
    return outer,outer-2*(edge+padding)-icon,edge,unit


def keywords(style):
    return [part.strip() for part in re.split(r'[,，、;；\n]+',style.get('highlight_keywords',''))
            if part.strip() and len(part.strip())<=24]


def highlight_for(text,style):
    return next((word for word in keywords(style) if word in text),'')


def protected(text,enabled=True,style=None,*,spoken_prices=True):
    spans=[(m.start(),m.end()) for m in SPEC.finditer(text)] if enabled else []
    if enabled:spans.extend((m.start(),m.end()) for m in DECIMAL.finditer(text))
    if enabled and spoken_prices:spans.extend((m.start(),m.end()) for m in PRICE.finditer(text))
    if style:
        for word in keywords(style):
            spans.extend((m.start(),m.end()) for m in re.finditer(re.escape(word),text))
    return spans


def display_protected(text,settings,style,*,compact=False,spans=None):
    """Project original reading units after punctuation deletion; never join
    two source facts into a new price (e.g. "49块，9包" -> "49块9包")."""
    shown=display(text,settings['punctuation'])
    if compact:shown=shown.replace(' ','')
    if spans is None:
        spans=reading_units(text,style,protected(text,settings['protect_specs'],style))
    if shown==text:return shown,spans
    positions=[];cursor=0
    for character in shown:
        while text[cursor]!=character:cursor+=1
        positions.append(cursor);cursor+=1
    projected=[]
    for a,b in spans:
        left,right=bisect_left(positions,a),bisect_left(positions,b)
        if left<right:projected.append((left,right))
    return shown,projected


def measured_width(text,measure):
    word=highlight_for(text,measure.s)
    return measure.measure(text)+(measure.measure(word)*(measure.s.get('highlight_scale',1)-1) if word else 0)


def legal_cut(index,spans):return not any(a<index<b for a,b in spans)


def boundary_score(text,index):
    if not index or index==len(text):return 0
    if text[index-1] in '，,、：:':return -7
    if text[index:].startswith(('所以','但是','结果','然后','就','只要','还有','每包','我家','汕头','冷链','想周末')):return -6
    if text[index].isdigit() and not text[index-1].isdigit():return -6
    if any(text[max(0,index-1):index+1]==w for w in ('所以','但是','这个','牛肉','火锅','包邮','冷链','送货','独立','姐妹','过来')):return 20
    return 2


def lines_for(text,settings,measure,available,*,spans=None):
    shown,spans=display_protected(text,settings,measure.s,spans=spans);groups=graphemes(shown)
    if not shown:return [],[]
    width=measured_width(shown,measure)
    # Balanced presentation can choose two lines even when one line fits. The
    # soft visual target is a measured width, never a hard character splitter.
    target=measure.measure('字幕排版宽度参考')
    prefer_two=settings['balanced'] and width>target*1.12
    if width<=available and (settings['max_lines']==1 or not prefer_two):return [shown],[width]
    if settings['max_lines']==1:return None,None
    options=[]
    for _,_,index in groups[:-1]:
        if not legal_cut(index,spans):continue
        left,right=shown[:index].strip(),shown[index:].strip()
        a,b=measured_width(left,measure),measured_width(right,measure)
        if not left or not right or max(a,b)>available:continue
        orphan=settings['avoid_orphan'] and min(len(graphemes(left)),len(graphemes(right)))<2
        cost=abs(a-b)/max(1,measure.size)*.45+boundary_score(shown,index)+(20 if orphan else 0)
        options.append((cost,[left,right],[a,b]))
    if options:
        _,lines,widths=min(options,key=lambda x:x[0]);return lines,widths
    if width<=available:return [shown],[width]
    return None,None


def original_alignment(text,speech,words):
    """Map numeric reading expansions to their entire original range."""
    mapping={};similar=0
    for tag,a,b,c,d in difflib.SequenceMatcher(None,text,speech,autojunk=False).get_opcodes():
        if tag=='equal':
            similar+=b-a
            for i in range(c,d):mapping[i]=(a+i-c,a+i-c+1)
        elif tag=='replace':
            for i in range(c,d):mapping[i]=(a,b)
        elif tag=='insert':
            for i in range(c,d):mapping[i]=(max(0,a-1),min(len(text),a+1))
    if similar<.5*min(len(text),len(speech)):
        raise ValueError('配音原文与字幕原文差异过大，需要人工复核，不使用伪对齐')
    result=[]
    for word in words:
        spans=[mapping[i] for i in range(word['source_start'],word['source_end']) if i in mapping]
        if spans:result.append(dict(word,start_index=min(x[0] for x in spans),end_index=max(x[1] for x in spans)))
    return result


class SubtitleReviewError(ValueError):
    def __init__(self,message,details):
        super().__init__(message);self.review_details=details


def build(raw,style,duration,fps,*,speech=None,voice=None,width=1080,height=1920,job_id='preview'):
    began=time.perf_counter();settings=smart(style.get('smart'));text,positions=normalize_source(raw)
    source_units=reading_units(text,style,protected(text,settings['protect_specs'],style))
    raw_units=sorted(set((positions[a][0],positions[b-1][1]) for a,b in source_units))
    measured=FontMeasure(style,width,height);outer,available,edge,unit=geometry(style,width,height)
    alignment_result=None;words=[]
    if voice:
        from subtitle_alignment import alignment
        alignment_result=voice.get('external_alignment') or alignment(voice['voice_processed'],speech,voice.get('effective_voice_start',0),voice.get('effective_voice_end',voice['voice_duration']))
        words=original_alignment(text,speech,alignment_result['words'])
    align_done=time.perf_counter()
    # A DTW word has one acoustic interval. Splitting it between cards assigns
    # that same interval to both cards; preserve it as an indivisible unit,
    # including the original-text span of expanded numeric readings.
    acoustic_spans=[(w['start_index'],w['end_index']) for w in words
                    if any(c.isalnum() for c in w['text'])]
    def times(a,b):
        if words:
            matched=[w for w in words if w['start_index']<b and w['end_index']>a and any(c.isalnum() for c in w['text'])]
            if not matched:raise ValueError('字幕句缺少语音对齐结果，需要人工复核')
            return min(w['start'] for w in matched),max(w['end'] for w in matched),sum(w['confidence'] for w in matched)/len(matched)
        return a/max(1,len(text))*duration,b/max(1,len(text))*duration,None
    clauses=[];last=0
    for m in HARD.finditer(text):
        if not legal_cut(m.end(),source_units):continue
        clauses.append((last,m.end(),'hard_boundary'));last=m.end()
    if last<len(text):clauses.append((last,len(text),'end'))
    selected=[];layout_seconds=0
    for first,last,reason in clauses:
        chunk=text[first:last];spans=[(a-first,b-first) for a,b in source_units if a>=first and b<=last]
        cuts=[0]+[end for _,_,end in graphemes(chunk)
                  if legal_cut(end,spans) and legal_cut(first+end,acoustic_spans)]
        # When intelligence is disabled, retain clause boundaries and width safety.
        @lru_cache(maxsize=None)
        def layout(a,b):
            nonlocal layout_seconds
            local=[(x-a,y-a) for x,y in spans if x>=a and y<=b]
            tick=time.perf_counter();value=lines_for(chunk[a:b],settings,measured,available,spans=local);layout_seconds+=time.perf_counter()-tick;return value
        dp={0:(0,[])}
        for b in cuts[1:]:
            options=[]
            for a in reversed(cuts):
                if a>=b or a not in dp:continue
                lines,widths=layout(a,b)
                if not lines:continue
                try:start,end,conf=times(first+a,first+b)
                except ValueError:continue
                length=len(graphemes(display(chunk[a:b],settings['punctuation'])))
                seconds=end-start
                goal={'sparse':20,'normal':13,'dense':9}[settings['density']]
                cost=(length-goal)**2*.08+max(0,seconds-2.6)**2*2+max(0,.9-seconds)*4+5
                cost+=boundary_score(chunk,b)
                if b<len(chunk) and words and settings['pause_assist']:
                    left=[w['end'] for w in words if w['end_index']<=first+b]
                    right=[w['start'] for w in words if w['start_index']>=first+b]
                    if left and right:cost-=min(8,max(0,min(right)-max(left))*15)
                if not settings['enabled']:cost-=length*10
                options.append((dp[a][0]+cost,dp[a][1]+[(first+a,first+b,lines,widths,reason)]))
            if options:dp[b]=min(options,key=lambda x:x[0])
        if len(chunk) not in dp:
            measured.close();raise ValueError('字幕受保护词语/规格或字体宽度无法容纳两行，请调整字号/宽度或不拆词词库并人工复核')
        selected.extend(dp[len(chunk)][1])
    cues=[];warnings=[]
    for a,b,lines,widths,reason in selected:
        start,end,conf=times(a,b);begin=max(0,math.ceil(start*fps-1e-6));stop=min(round(duration*fps),math.ceil(end*fps-1e-6))
        if stop<=begin:measured.close();raise ValueError('字幕有效语音时长不足一帧，需人工复核')
        raw_start=positions[a][0];raw_end=positions[b-1][1]
        cues.append(dict(revision=REVISION,price_guard_revision=PRICE_GUARD,text=''.join(lines),lines=lines,highlight=highlight_for(''.join(lines),style),line_widths=widths,
                         line_widths_px=widths,safe_width_px=available,
                         source_range=[raw_start,raw_end],original_text=raw[raw_start:raw_end],
                         start_frame=begin,end_frame=stop,startMs=round(begin*1000/fps),endMs=round(stop*1000/fps),
                         timestampMs=round(start*1000),confidence=conf,
                         timestamp_source=alignment_result['source'] if words else ('preview_reading_only' if job_id=='preview' else 'unvoiced_reading_only'),
                         layout=dict(available_width=available,outer_width=outer,edge=edge,font_size=measured.size,
                                     font_family=style['font_family'],font_sha256=style.get('font_sha256','')),
                         segment_reason=reason,animation=budget(style.get('animation'),stop-begin,fps)))
    for i,cue in enumerate(cues):
        cue['word_guard_revision']=WORD_GUARD
        if i==0:cue['word_guard_source_spans']=[list(pair) for pair in raw_units]
        if i and cue['start_frame']<cues[i-1]['end_frame']:
            # Only a quantization boundary may be clamped; larger acoustic overlap needs review.
            overlap=cues[i-1]['end_frame']-cue['start_frame']
            if overlap>2:
                measured.close()
                raise SubtitleReviewError('相邻字幕语音对齐交叠，需人工复核',dict(
                    overlap_frames=overlap,fps=fps,left=cues[i-1],right=cue,
                    audio_path=voice.get('voice_processed') if voice else None,
                    alignment_cache_key=alignment_result.get('cache_key') if alignment_result else None))
            cues[i-1]['end_frame']=cue['start_frame'];cues[i-1]['endMs']=cue['startMs']
            cues[i-1]['animation']=budget(style.get('animation'),cues[i-1]['end_frame']-cues[i-1]['start_frame'],fps)
        if cue['end_frame']-cue['start_frame']<fps*.9:warnings.append('short_cue:'+str(i+1))
        if len(cue['lines'])>1:warnings.append('two_lines:'+str(i+1))
        warnings.extend(flag+':'+str(i+1) for flag in cue['animation']['flags'])
    measured.close()
    report=dict(revision=REVISION,price_guard_revision=PRICE_GUARD,word_guard_revision=WORD_GUARD,
                word_segmenter='windows_icu' if settings['protect_words'] else 'explicit_terms_only',
                protected_unit_count=len(raw_units),job_id=job_id,config_version=1,cue_count=len(cues),font=style['font_family'],
                animation=deepcopy(style.get('animation')),alignment_seconds=align_done-began if words else 0,
                alignment_cache_hit=alignment_result.get('cache_hit') if alignment_result else None,
                alignment_confidence=alignment_result.get('confidence') if alignment_result else None,
                acoustic_word_boundaries_preserved=bool(words),
                layout_seconds=layout_seconds,segmentation_seconds=time.perf_counter()-align_done-layout_seconds,
                total_seconds=time.perf_counter()-began,warnings=warnings,manual_review=False)
    validate(cues,raw,style,fps,width,height)
    return cues,report


def validate(cues,raw,style,fps,width=1080,height=1920):
    """Block unsafe output. Ordinary display punctuation is the only allowed deletion."""
    covered=''.join(c['original_text'] for c in cues)
    if covered!=raw.replace('\r',''):raise ValueError('智能字幕原文覆盖不完整，需要人工复核')
    previous=0;source_cut=0
    source_spans=protected(raw,style['smart']['protect_specs'],style)
    word_guard=bool(cues and cues[0].get('word_guard_revision')==WORD_GUARD)
    if word_guard:
        source_spans=cues[0].get('word_guard_source_spans')
        if (not isinstance(source_spans,list) or
                any(not isinstance(p,list) or len(p)!=2 or
                    any(type(n) is not int for n in p) or not 0<=p[0]<p[1]<=len(raw) for p in source_spans) or
                any(c.get('word_guard_revision')!=WORD_GUARD for c in cues)):
            raise ValueError('字幕不拆词冻结信息无效')
    for c in cues:
        if c['start_frame']<previous or c['end_frame']<=c['start_frame']:raise ValueError('字幕时间轴交叠或无效')
        previous=c['end_frame']
        if len(c['lines'])>2 or any(not x for x in c['lines']):raise ValueError('字幕行数异常')
        if max(c['line_widths'])>c['layout']['available_width']+.5:raise ValueError('字幕超出安全宽度')
        expected=display(c['original_text'],style['smart']['punctuation']).replace(' ','')
        actual=''.join(c['lines']).replace(' ','')
        if expected!=actual:raise ValueError('字幕显示内容缺失，需要人工复核')
        if c['animation']['stable_frames']<0:raise ValueError('字幕动画预算异常')
        guarded=c.get('price_guard_revision')==PRICE_GUARD
        if guarded:
            local=([(a-source_cut,b-source_cut) for a,b in source_spans
                    if source_cut<=a<b<=source_cut+len(c['original_text'])] if word_guard else
                   protected(c['original_text'],style['smart']['protect_specs'],style))
            _,spans=display_protected(c['original_text'],style['smart'],style,compact=True,spans=local)
        else:
            # Frozen captions retain their original validation contract.
            spans=protected(actual,style['smart']['protect_specs'],spoken_prices=False)
        cut=0
        for line in c['lines'][:-1]:
            cut+=len(line.replace(' ','')) if guarded else len(line)
            if not legal_cut(cut,spans):raise ValueError('字幕词语/数字规格被断开' if word_guard else '字幕数字规格被断开')
        source_cut+=len(c['original_text'])
        if guarded and not legal_cut(source_cut,source_spans):
            raise ValueError('字幕词语/数字规格被切到下一句' if word_guard else '字幕数字规格被切到下一句')
    _,_,edge,unit=geometry(style,width,height)
    tallest=max((len(c['lines'])*style['font_size']*unit*style['line_spacing'] for c in cues),default=0)
    tallest+=2*edge+(style['padding_y']*unit*2 if style['bubble']!='none' else 0)+(20*unit if style['bubble']=='speech' else 0)
    if max(tallest,style['bubble_height']*unit)>height*(1-style['safe_bottom'])-edge*2:
        raise ValueError('字幕字号/气泡高度无法容纳安全区，请调整后复核')
