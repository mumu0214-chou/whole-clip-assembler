"""One package-count anchor, using the existing cached acoustic text alignment."""
import re
import math
from grouped_materials import GroupedError

REVISION = 'package_count_anchor_v2_clause'
_TRIGGER = re.compile(r'(?:一共)?(?:这么多|好几|多么多|好多|很多|[0-9０-９]+|[零〇一二两三四五六七八九十百千万]+)\s*包')
_SINGLE_DETAIL = re.compile(r'\s*(?:(?:[0-9０-９]+(?:[.．][0-9０-９]+)?\s*(?:kg|g|公斤|千克|克|斤))|(?:(?:才|只要|仅需|售价|价格为)?\s*[0-9０-９]+(?:[.．][0-9０-９]+)?\s*(?:元|块)))', re.IGNORECASE)


def trigger(raw):
    matches=[m for m in _TRIGGER.finditer(raw)
             if not (re.fullmatch(r'[一1１]\s*包',m.group()) and _SINGLE_DETAIL.match(raw[m.end():]))]
    if len(matches)>1:
        raise GroupedError('MULTIPLE_PACKAGE_ANCHORS','第一版只支持每篇0或1个包装数量表达')
    return matches[0] if matches else None


def clause_range(raw,match):
    """Use the whole quantity clause, preserving decimal commas in original text."""
    boundaries=[]
    for i,ch in enumerate(raw):
        if ch not in '。！？!?；;\n，,.':continue
        if ch in ',.' and i>0 and i+1<len(raw) and raw[i-1].isdigit() and raw[i+1].isdigit():continue
        boundaries.append(i)
    start=max((i+1 for i in boundaries if i<match.start()),default=0)
    end=min((i for i in boundaries if i>=match.end()),default=len(raw))
    while start<end and raw[start].isspace():start+=1
    while end>start and raw[end-1].isspace():end-=1
    return start,end


def anchor(package,cfg):
    raw=package.get('raw_text','')
    match=trigger(raw)
    if not match:
        return None
    clause_start,clause_end=clause_range(raw,match)
    voice=package.get('voice')
    if not package.get('narration_enabled') or not voice:
        raise GroupedError('VOICE_ALIGNMENT_REQUIRED','包装数量同步需要实际配音，当前模式不适用')
    from subtitle_alignment import alignment
    from smart_subtitles import normalize_source,original_alignment
    normalized,positions=normalize_source(raw)
    data=voice.get('external_alignment') or alignment(voice['voice_processed'],package['speech_text'],
                   voice['effective_voice_start'],voice['effective_voice_end'])
    words=original_alignment(normalized,package['speech_text'],data['words'])
    indices=[i for i,(a,b) in enumerate(positions) if a<clause_end and b>clause_start]
    selected=[w for w in words if indices and w['start_index']<=indices[-1] and w['end_index']>indices[0]
              and any(ch.isalnum() for ch in w['text'])]
    if not selected or data.get('source')!='whisper_known_text_dtw_v1':
        raise GroupedError('VOICE_ALIGNMENT_UNRELIABLE','包装数量表达没有可靠的声学词边界')
    required={i for i in indices if normalized[i].isalnum()}
    covered={i for w in selected for i in range(w['start_index'],w['end_index'])}
    confidence=sum(float(w.get('confidence',0)) for w in selected)/len(selected)
    if not required.issubset(covered) or confidence<.035 or any(w['end']<=w['start'] for w in selected):
        raise GroupedError('VOICE_ALIGNMENT_UNRELIABLE','包装数量表达对齐不完整、零时长或可信度不足')
    start,end=min(w['start'] for w in selected),max(w['end'] for w in selected)
    if not 0<=start<end<=voice['effective_voice_duration']+1e-5:
        raise GroupedError('VOICE_ALIGNMENT_UNRELIABLE','包装锚点时间越界')
    fps=cfg['fps']
    return dict(revision=REVISION,phrase=raw[clause_start:clause_end],source_range=[clause_start,clause_end],
                count_phrase=match.group(),count_source_range=[match.start(),match.end()],
                start_seconds=start,end_seconds=end,
                start_frame=max(0,math.floor(start*fps+1e-7)),
                end_frame=math.ceil(end*fps-1e-7),
                timestamp_source=data['source'],alignment_cache_key=data.get('cache_key'),
                alignment_confidence=confidence,words=selected)
