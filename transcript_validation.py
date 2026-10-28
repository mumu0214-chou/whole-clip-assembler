"""Shared external-audio content checks; no models, prompts or media mutation."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import difflib
from functools import lru_cache
import os
import re
import unicodedata

REVISION='external_transcript_v4_number_boundaries'
VARIANTS=str.maketrans({'兩':'两','萬':'万','億':'亿','點':'点','〇':'零',
    '壹':'一','贰':'二','貳':'二','叁':'三','參':'三','肆':'四','伍':'五',
    '陆':'六','陸':'六','柒':'七','捌':'八','玖':'九','拾':'十','佰':'百','仟':'千',
    '個':'个','塊':'块','於':'于','沒':'没'})
DIGITS={c:i for i,c in enumerate('零一二三四五六七八九')};DIGITS['两']=2
SMALL={'十':10,'百':100,'千':1000}
CJK=r'[零一二两三四五六七八九十百千万亿]+(?:点[零一二两三四五六七八九]+)?'
ATOM=rf'(?:\d+(?:\.\d+)?|{CJK})'
TOKEN=re.compile(rf'{ATOM}分之{ATOM}|\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?|{CJK}|半(?=斤|公斤|千克|克|包|袋)')
UNITS=re.compile(r'公斤|千克|毫升|分钟|小时|斤|克|升|元|米|块|角|包|袋|箱|份|个|条|瓶|盒|件|折|%|秒|天|月|年|次|套|遍|种')
LEXICAL_ONE=('起','直','样','点','些','般','定','共','口','下','会','并','边',
             '开','涮','看','吃','想','问','说','听','来','到','买','摆','煮','拌')
MASS={'斤','公斤','千克','克','毫升','升'}
PROTECTED=re.compile(r'不是|不做|不含|不加|没有|低于|不到|不超过|至少|最少|最多')

class TranscriptReviewError(ValueError):
    def __init__(self,message,kind,**details):
        super().__init__(message);self.kind=kind;self.details=details

def explicit_value_conflict(error):
    """A clear equal-length price/weight disagreement needs human review."""
    expected=error.details.get('expected',[]);actual=error.details.get('actual',[])
    if error.kind!='numeric_difference' or len(expected)!=len(actual):return False
    return any((e['value']!=a['value'] and e['unit']==a['unit'] and e['unit'] in MASS|{'currency'}) or (e['unit'] and a['unit'] and e['unit']!=a['unit']) for e,a in zip(expected,actual))

def canonical_text(text):
    text=unicodedata.normalize('NFKC',text)
    # Windows' text conversion handles traditional ASR output without a model
    # download or a hand-written replacement of arbitrary vocabulary.
    if os.name=='nt':
        text=simplify_chinese(text)
    return text.translate(VARIANTS)

@lru_cache(maxsize=2048)
def simplify_chinese(text):
    import ctypes
    function=ctypes.windll.kernel32.LCMapStringEx
    function.argtypes=[ctypes.c_wchar_p,ctypes.c_ulong,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_wchar_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_ssize_t]
    function.restype=ctypes.c_int
    size=function('zh-CN',0x02000000,text,-1,None,0,None,None,0)
    if not size:return text
    output=ctypes.create_unicode_buffer(size)
    return output.value if function('zh-CN',0x02000000,text,-1,output,size,None,None,0) else text

def number_value(token):
    token=token.replace(',','')
    if '分之' in token:
        denominator,numerator=token.split('分之',1)
        return number_value(numerator)/number_value(denominator)
    if token=='半':return Decimal('.5')
    if token[0].isdigit():return Decimal(token)
    if '点' in token:
        whole,fraction=token.split('点',1)
        if not fraction or any(c not in DIGITS for c in fraction):raise ValueError('invalid decimal')
        return number_value(whole)+Decimal('0.'+''.join(str(DIGITS[c]) for c in fraction))
    if all(c in DIGITS for c in token):return Decimal(''.join(str(DIGITS[c]) for c in token))
    total=section=0;pending=None;previous_unit=10000
    for c in token:
        if c in DIGITS:
            if pending is not None and c!='零' and pending!=0:raise ValueError('adjacent spoken numbers')
            pending=DIGITS[c]
        elif c in SMALL:
            scale=SMALL[c]
            if scale>=previous_unit:raise ValueError('non-descending Chinese number')
            section+=(1 if pending is None else pending)*scale
            pending=None;previous_unit=scale
        elif c in ('万','亿'):
            scale=10000 if c=='万' else 100000000
            section+=pending or 0
            if c=='亿':total=(total+section or 1)*scale
            else:total+=(section or 1)*scale
            section=0;pending=None;previous_unit=10000
        else:raise ValueError('invalid numeral')
    return Decimal(total+section+(pending or 0))

def decimal_string(value):
    return format(value.normalize(),'f')

def numeric_facts(text):
    text=re.sub(r'(?<=\d)点(?=\d)','.',canonical_text(text));facts=[];consumed=-1
    for match in TOKEN.finditer(text):
        if match.start()<consumed:continue
        token=match.group();tail=text[match.end():]
        if len(token)==1 and text[:match.start()].endswith(('周','星期','礼拜')) and not (UNITS.match(tail) and UNITS.match(tail).group() in MASS):
            continue
        if token=='一' and tail.startswith(LEXICAL_ONE) and not tail.startswith(('点零','点一','点二','点三','点四','点五','点六','点七','点八','点九')):
            continue
        # A punctuation boundary is meaningful: 60-70 stays two numbers.
        end=match.end()
        colloquial=re.match(r'([一二两三四五六七八九])([一二两三四五六七八九])(十|百|千)',token)
        value=number_value(token) if not colloquial else None
        if token[0].isdigit() and tail.startswith(('万','亿')):
            value*=10000 if tail[0]=='万' else 100000000
            token+=tail[0];end+=1;tail=tail[1:];consumed=end
        unit_match=UNITS.match(tail);unit=unit_match.group() if unit_match else ''
        if unit in ('元','米','块'):unit='currency'
        if unit=='%':value/=100
        if unit in MASS and tail[len(unit_match.group()):].startswith('半'):
            value+=Decimal('.5');consumed=end+len(unit_match.group())+1
        values=[Decimal(DIGITS[colloquial[1]]*SMALL[colloquial[3]]),Decimal(DIGITS[colloquial[2]]*SMALL[colloquial[3]])] if colloquial else [value]
        if colloquial and colloquial.end()<len(token):values.append(number_value(token[colloquial.end():]))
        facts.extend(dict(value=decimal_string(v),unit=unit,token=token,start=match.start(),end=end) for v in values)
    return facts

def spoken_signature(text,digit_spelling=False):
    """Retain units and range separators while tolerating spoken segmentation."""
    from voice_pipeline import normalize_speech
    text=canonical_text(text)
    if digit_spelling:
        # Only expand explicit Arabic source digits; an ASR integer 991 must
        # not be silently reinterpreted as a price of 99 plus one jin.
        text=re.sub(r'\d{2,}',lambda m:''.join('零一二三四五六七八九'[int(c)] for c in m.group()),text)
    text=re.sub(r'(?<=[0-9零一二两三四五六七八九十百千万亿])\s*[-~～—–至到]\s*(?=[0-9零一二两三四五六七八九十百千万亿])','到',text)
    text=re.sub(r'半(?=斤|公斤|千克|克)','0.5',text)
    text=canonical_text(normalize_speech(text)).replace('两','二')
    pattern=rf'[零一二三四五六七八九十百千万亿点]+(?:到[零一二三四五六七八九十百千万亿点]+)*(?:公斤|千克|斤|克|元|米|块|包|袋|箱|份|个|条|次|遍|套|%)?'
    parts=[]
    for match in re.finditer(pattern,text):
        value=match.group()
        if len(value)==1 and text[:match.start()].endswith(('周','星期','礼拜')):continue
        if value=='一' and text[match.end():].startswith(LEXICAL_ONE):continue
        parts.append(re.sub(r'(元|米|块)$','currency',value))
    return parts

def source_spoken_signatures(text):
    """Each source Arabic integer may be read as a value or digit by digit."""
    from itertools import product
    matches=list(re.finditer(r'(?<![\d.])\d{2,}(?![\d.])',canonical_text(text)))
    if len(matches)>6:return [spoken_signature(text)]
    variants=[];text=canonical_text(text)
    for choices in product((False,True),repeat=len(matches)):
        parts=[];position=0
        for match,digits in zip(matches,choices):
            parts.append(text[position:match.start()])
            parts.append(''.join('零一二三四五六七八九'[int(c)] for c in match.group()) if digits else match.group())
            position=match.end()
        parts.append(text[position:]);variants.append(spoken_signature(''.join(parts)))
    return variants

def verify_transcript(text,recognized):
    from voice_pipeline import normalize_speech
    expected_text=canonical_text(text);checked_recognized=canonical_text(recognized)
    corrections=[]
    # '99' may be spoken digit by digit and recognized as the word 久久.
    # Require exact neighboring text from this supplied script, never globally
    # turn an unrelated 久久 into a price.
    context_expected=re.sub(r'[^\w]','',expected_text)
    context_actual=re.sub(r'[^\w]','',checked_recognized)
    # A place name is not a livestock count. Correct this known ASR
    # homophone only with the supplied place name and matching neighbors.
    for occurrence in re.finditer('汕头',context_expected):
        left=context_expected[max(0,occurrence.start()-4):occurrence.start()]
        right=context_expected[occurrence.end():occurrence.end()+4]
        if len(left)<2 or len(right)<2:continue
        pattern=re.compile(re.escape(left)+r'(?P<place>三头|山头)'+re.escape(right))
        def replace_place(match):
            corrections.append(dict(expected='汕头',recognized=match.group('place'),left=left,right=right))
            return left+'汕头'+right
        context_actual=pattern.sub(replace_place,context_actual)
    for occurrence in re.finditer(r'(?<!\d)99(?!\d)',context_expected):
        left=context_expected[max(0,occurrence.start()-5):occurrence.start()]
        right=context_expected[occurrence.end():occurrence.end()+5]
        if len(left)<2 and len(right)<2:continue
        pattern=re.compile(re.escape(left)+r'(?P<number>久久)'+re.escape(right))
        def replace_homophone(match):
            corrections.append(dict(expected='99',recognized='久久',left=left,right=right))
            a,b=match.span('number');return context_actual[match.start():a]+'九九'+context_actual[b:match.end()]
        context_actual=pattern.sub(replace_homophone,context_actual)
    if corrections:checked_recognized=context_actual
    if '十包' in normalize_speech(expected_text):checked_recognized=checked_recognized.replace('食包','十包').replace('时包','十包')
    normalize=lambda value:re.sub(r'[^\w]','',normalize_speech(value)).lower()
    expected=normalize(expected_text);actual=normalize(checked_recognized)
    similarity=difflib.SequenceMatcher(None,expected,actual,autojunk=False).ratio()
    result=dict(recognized=recognized,checked_recognized=checked_recognized,similarity=similarity,source='existing_whisper_transcription',revision=REVISION,numeric_homophone_corrections=corrections)
    if similarity<.80:
        raise TranscriptReviewError(f'外部音频与正文识别内容不一致，需要复核；相似度{similarity:.3f}，识别：{recognized}','content_difference')
    if Counter(PROTECTED.findall(expected_text))!=Counter(PROTECTED.findall(checked_recognized)):
        raise TranscriptReviewError(f'外部音频的否定/价格限定词识别结果需复核；识别：{recognized}','protected_statement')
    parse_error=None
    ef=[];af=[]
    try:ef=numeric_facts(expected_text);af=numeric_facts(checked_recognized)
    except (ValueError,InvalidOperation,ZeroDivisionError) as exc:parse_error=str(exc)
    equivalent=not parse_error and [f['value'] for f in ef]==[f['value'] for f in af]
    if equivalent:
        for e,a in zip(ef,af):
            if e['unit'] and a['unit'] and e['unit']!=a['unit']:equivalent=False
    mode='numeric_values'
    if not equivalent:
        actual_signature=spoken_signature(checked_recognized)
        signatures=source_spoken_signatures(expected_text)
        # Arabic ITN can merge a price and a quantity. Require another real,
        # non-ITN recognition rather than accepting a changed written value.
        changed_arabic=not parse_error and any(f['token'][0].isdigit() for f in af) and [f['value'] for f in ef]!=[f['value'] for f in af]
        if actual_signature in signatures and not changed_arabic:
            equivalent=True;mode='spoken_number_boundaries'
    if not equivalent:
        kind='numeric_missing' if len(af)<len(ef) else 'numeric_difference'
        if parse_error:kind='numeric_parse'
        raise TranscriptReviewError(f'外部音频的价格/数量识别结果需复核：预期{ef}，识别{af}；识别：{recognized}',kind,expected=ef,actual=af,parse_error=parse_error)
    result.update(numbers_expected=[f['value'] for f in ef],numbers_actual=[f['value'] for f in af],numeric_facts_expected=ef,numeric_facts_actual=af,numeric_equivalence=mode,spoken_numbers_expected=spoken_signature(expected_text),spoken_numbers_actual=spoken_signature(checked_recognized))
    return result
