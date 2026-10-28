"""Offline known-text DTW alignment, executed in the existing ASR environment."""
from mac_paths import path as _portable_path
import json
import sys
import time
import difflib
import re

from faster_whisper import WhisperModel
from faster_whisper.audio import decode_audio, pad_or_trim
from faster_whisper.tokenizer import Tokenizer

model = None
sense_model = None


def verify_transcript(text, recognized):
    from transcript_validation import verify_transcript as verify
    return verify(text,recognized)


def transcribe_content(audio,source,model_path):
    global model,sense_model
    if source=='whisper':
        if model is None:
            model=WhisperModel(model_path,device='cpu',compute_type='int8',cpu_threads=4,num_workers=1,local_files_only=True)
        segments,_=model.transcribe(audio,language='zh',beam_size=3,vad_filter=False,condition_on_previous_text=False)
        return ''.join(s.text for s in segments)
    if sense_model is None:
        from pathlib import Path
        import sherpa_onnx
        directory=Path(_portable_path(r'D:\GPTData\cache\codex-asr-models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'))
        if not (directory/'model.int8.onnx').is_file() or not (directory/'tokens.txt').is_file():
            raise ValueError('本机第二识别模型缺失；保留复核，不下载或放行')
        sense_model=sherpa_onnx.OfflineRecognizer.from_sense_voice(model=str(directory/'model.int8.onnx'),tokens=str(directory/'tokens.txt'),num_threads=3,language='zh',use_itn=False,provider='cpu',debug=False)
    stream=sense_model.create_stream();stream.accept_waveform(16000,audio);sense_model.decode_stream(stream)
    return stream.result.text


def verify_audio_content(audio,text,model_path,first='whisper'):
    from transcript_validation import TranscriptReviewError,explicit_value_conflict
    second='sensevoice' if first=='whisper' else 'whisper'
    recognized=transcribe_content(audio,first,model_path)
    try:
        result=verify_transcript(text,recognized)
        result.update(source='existing_'+first+'_transcription',decision='primary_verified')
        return result
    except TranscriptReviewError as primary_error:
        independent=transcribe_content(audio,second,model_path)
        try:result=verify_transcript(text,independent)
        except TranscriptReviewError as secondary_error:
            error=TranscriptReviewError('外部音频内容/价格/数量需复核；第一识别：'+recognized+'；第二识别：'+independent,'both_recognizers_need_review',primary_error=str(primary_error),secondary_error=str(secondary_error),primary_source=first,primary_recognized=recognized,secondary_source=second,secondary_recognized=independent)
            raise error from secondary_error
        if explicit_value_conflict(primary_error):
            raise TranscriptReviewError('外部音频价格/数量两种识别存在明确数值冲突，需复核；第一识别：'+recognized+'；第二识别：'+independent,'recognizers_value_conflict',primary_recognized=recognized,secondary_recognized=independent)
        result.update(source='existing_'+second+'_transcription',decision='independent_secondary_verified',primary_source=first,primary_recognized=recognized,primary_error=str(primary_error),secondary_source=second,secondary_recognized=independent)
        return result


def align(req):
    global model
    began = time.perf_counter()
    if model is None:
        model = WhisperModel(req['model'], device='cpu', compute_type='int8', cpu_threads=4, num_workers=1,
                             local_files_only=True)
    audio = decode_audio(req['audio'], sampling_rate=16000)
    audio = audio[round(req['start']*16000):round(req['end']*16000)]
    duration = len(audio)/16000
    tokenizer = Tokenizer(model.hf_tokenizer, model.model.is_multilingual, task='transcribe', language='zh')
    text = req['text']
    content_check=None
    if req.get('verify_content'):
        content_check=verify_audio_content(audio,req.get('raw_text') or text,req['model'])
    if duration <= 29:
        chunks = [(0, duration, 0, len(text))]
    else:
        # Recognition supplies acoustic anchors only; the known text remains authoritative.
        segments, _ = model.transcribe(audio, language='zh', beam_size=3, word_timestamps=True,
                                        vad_filter=False, condition_on_previous_text=False)
        words = [w for seg in segments for w in (seg.words or [])]
        recognized = ''.join(w.word for w in words)
        owners = [w for w in words for _ in w.word]
        matcher = difflib.SequenceMatcher(None, recognized, text, autojunk=False)
        anchors = {}
        for a, b, count in matcher.get_matching_blocks():
            for k in range(count):
                anchors[b+k] = owners[a+k]
        if len(anchors) < .72*len(text):
            raise ValueError('长音频对齐锚点不足，需要人工复核；未使用字数估时')
        chunks=[]; position=0; audio_start=0.
        while position < len(text):
            candidates=[(i,w) for i,w in anchors.items() if i>position and w.start-audio_start<26
                        and w.start-audio_start>12]
            if duration-audio_start<=29:
                chunks.append((audio_start,duration,position,len(text)));break
            if not candidates:raise ValueError('长音频缺少可靠分窗锚点，需要人工复核')
            index,word=max(candidates,key=lambda pair:pair[0])
            cut=word.start
            chunks.append((audio_start,cut,position,index));position=index;audio_start=cut
    output=[]
    for start,end,first,last in chunks:
        clip=audio[round(start*16000):round(end*16000)]
        features=model.feature_extractor(clip)
        frames=min(features.shape[-1],3000)
        encoded=model.encode(pad_or_trim(features))
        token_ids=tokenizer.encode(text[first:last])
        aligned=model.find_alignment(tokenizer,[token_ids],encoded,frames)[0]
        cursor=first
        for w in aligned:
            part=w['word']; index=text.find(part,cursor,last)
            if index<0:raise ValueError('对齐文字无法映射原文，需要人工复核')
            cursor=index+len(part)
            output.append(dict(text=part,source_start=index,source_end=cursor,
                               start=round(start+w['start'],4),end=round(start+w['end'],4),
                               confidence=float(w['probability'])))
    if not output or max(w['end'] for w in output)>duration+.25:
        raise ValueError('语音对齐时间轴异常，需要人工复核')
    meaningful=[w for w in output if any(c.isalnum() for c in w['text'])]
    zero=sum(w['end']-w['start']<.005 for w in meaningful)
    confidence=sum(w['confidence'] for w in meaningful)/max(1,len(meaningful))
    if zero>max(2,len(meaningful)*.16) or confidence<.035:
        raise ValueError(f'语音对齐可信度不足（{confidence:.3f}，零时长 {zero}），需要人工复核')
    return dict(words=output,duration=duration,confidence=confidence,seconds=time.perf_counter()-began,
                source='whisper_known_text_dtw_v1',chunks=len(chunks),content_check=content_check)


if __name__=='__main__':
    for line in sys.stdin:
        try:
            result=align(json.loads(line)); print('RESULT:'+json.dumps(result,ensure_ascii=False),flush=True)
        except Exception as exc:
            print('RESULT:'+json.dumps({'error':str(exc)},ensure_ascii=False),flush=True)
