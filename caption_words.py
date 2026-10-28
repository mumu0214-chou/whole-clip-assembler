"""Reading units for new captions; no TTS, downloads or source rewriting."""
import ctypes
from functools import lru_cache
import re

REVISION = 'caption_words_v1'
DOMAIN = ('执行标准', '牛肉丸', '小包装', '独立包装', '牛肉含量', '冷链配送')
CODE = re.compile(r'(?<![A-Za-z0-9])(?:[A-Za-z]{1,10}(?:[ \t]*/[ \t]*[A-Za-z]{1,5})?[ \t]*\d+(?:[./_\-]\d+)*|[A-Za-z][A-Za-z0-9]*(?:[./_\-][A-Za-z0-9]+)*)')


def terms(value):
    if not isinstance(value, str) or len(value) > 8000:
        raise ValueError('不拆词词库最多8000字，每行一个词')
    result = list(dict.fromkeys(w.strip() for w in re.split(r'[,，、;；\n]+', value) if w.strip()))
    if any(len(w)>64 or re.search(r'[。！？!?…\r]', w) for w in result):
        raise ValueError('不拆词每项最多64字，不包含句末标点；请填写词语而非整段文案')
    return result


@lru_cache(maxsize=1)
def _icu():
    # Windows ships ICU. Load only from System32, never the current directory.
    lib=ctypes.CDLL('icu.dll',winmode=0x800)
    ptr=ctypes.c_void_p; integer=ctypes.c_int32
    lib.ubrk_open.argtypes=[integer,ctypes.c_char_p,ctypes.POINTER(ctypes.c_uint16),integer,ctypes.POINTER(integer)]
    lib.ubrk_open.restype=ptr
    for name in ('ubrk_first','ubrk_next','ubrk_getRuleStatus'):
        fn=getattr(lib,name);fn.argtypes=[ptr];fn.restype=integer
    lib.ubrk_close.argtypes=[ptr];lib.ubrk_close.restype=None
    return lib


@lru_cache(maxsize=256)
def automatic(text):
    if not text:return ()
    try:lib=_icu()
    except (OSError,AttributeError) as exc:
        raise ValueError('系统中文分词组件不可用；可在智能排版中关闭自动完整词语保护，继续使用不拆词词库') from exc
    data=text.encode('utf-16-le');buffer=(ctypes.c_uint16*(len(data)//2)).from_buffer_copy(data)
    mapping={0:0};offset=0
    for index,char in enumerate(text):
        offset+=2 if ord(char)>0xffff else 1;mapping[offset]=index+1
    status=ctypes.c_int32();iterator=lib.ubrk_open(1,b'zh',buffer,len(buffer),ctypes.byref(status))
    if not iterator or status.value>0:
        if iterator:lib.ubrk_close(iterator)
        raise ValueError('系统中文分词失败，请检查智能排版设置')
    spans=[]
    try:
        start=lib.ubrk_first(iterator)
        while (end:=lib.ubrk_next(iterator))!=-1:
            if lib.ubrk_getRuleStatus(iterator)>=100:
                a,b=mapping[start],mapping[end]
                if b-a>1:spans.append((a,b))
            start=end
    finally:lib.ubrk_close(iterator)
    return tuple(spans)


def reading_units(text,style,base=()):
    """Hard phrases override overlapping automatic tokens, avoiding accidental
    chains that glue a whole sentence together. Punctuation stays a boundary."""
    hard=list(base)
    hard.extend((m.start(),m.end()) for m in CODE.finditer(text))
    words=sorted(set(DOMAIN+tuple(terms(style.get('protected_terms','')))),key=lambda w:(-len(w),w))
    explicit=[]
    for word in words:
        for match in re.finditer(re.escape(word),text):
            a,b=match.span()
            if not any(a<y and b>x for x,y in explicit):explicit.append((a,b))
    hard.extend(explicit)
    auto=automatic(text) if style.get('smart',{}).get('protect_words',True) else ()
    return sorted(set(hard+[(a,b) for a,b in auto if not any(a<y and b>x for x,y in hard)]))
