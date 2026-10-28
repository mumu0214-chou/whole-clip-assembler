"""Descriptive audio measurements, never a speaker similarity score."""
import json
import subprocess
import numpy as np
from voice_pipeline import measure_loudness


def audio_features(path,ffmpeg):
    data=subprocess.run([ffmpeg,'-v','error','-nostdin','-i',str(path),'-ac','1','-ar','16000',
                         '-f','f32le','-'],capture_output=True,check=True,timeout=120).stdout
    x=np.frombuffer(data,dtype='<f4').astype(float);rate=16000
    if not len(x):raise ValueError('音频为空。')
    pitches=[];spectral=np.zeros(513);frames=0
    for start in range(0,max(1,len(x)-1024),320):
        chunk=x[start:start+1024]
        if len(chunk)<1024:continue
        rms=np.sqrt(np.mean(chunk**2))
        if rms<.008:continue
        frame=(chunk-chunk.mean())*np.hanning(1024)
        power=np.abs(np.fft.rfft(frame))**2;spectral+=power;frames+=1
        ac=np.fft.irfft(np.abs(np.fft.rfft(frame,n=2048))**2)[:1024]
        low,high=int(rate/600),int(rate/55)
        lag=low+int(np.argmax(ac[low:high]))
        if ac[0]>0 and ac[lag]/ac[0]>.45:
            shift=0.
            if 1<lag<len(ac)-1:
                denominator=ac[lag-1]-2*ac[lag]+ac[lag+1]
                if abs(denominator)>1e-12:shift=.5*(ac[lag-1]-ac[lag+1])/denominator
            pitches.append(rate/(lag+shift))
    freq=np.fft.rfftfreq(1024,1/rate);total=max(float(spectral.sum()),1e-20)
    result={'f0_method':'64ms自相关，55–600Hz；只统计置信度>0.45帧，非说话人相似度',
            'f0_median_hz':round(float(np.median(pitches)),2) if pitches else None,
            'f0_mean_hz':round(float(np.mean(pitches)),2) if pitches else None,
            'voiced_frames':len(pitches)}
    for name,lo,hi in [('low',80,350),('presence',2500,6000),('high',6500,8000)]:
        ratio=float(spectral[(freq>=lo)&(freq<hi)].sum())/total
        result[name+'_energy_db_relative']=round(float(10*np.log10(max(ratio,1e-20))),2)
    result.update(measure_loudness(path,ffmpeg))
    for k,v in result.items():
        if isinstance(v,float) and not np.isfinite(v):result[k]=None
    return result


def diagnostic_hints(stats):
    base=stats.get('baseline',{});out=stats['indextts_processed'];hints=[]
    raw=stats['indextts_raw'];ref=stats.get('reference',base)
    if raw.get('f0_median_hz') is not None and ref.get('f0_median_hz') is not None:
        delta=raw['f0_median_hz']-ref['f0_median_hz']
        hints.append(f'Raw中位基频比参考{"高" if delta>=0 else "低"}{abs(delta):.2f}Hz；仅比较已估计的有声帧。')
    if raw.get('duration_seconds') and ref.get('duration_seconds'):
        hints.append(f'Raw时长{raw["duration_seconds"]:.2f}秒，参考{ref["duration_seconds"]:.2f}秒；文本不同时，不能据此判断语速差。')
    for key,label in [('presence_energy_db_relative','Presence频段'),('high_energy_db_relative','高频')]:
        if out.get(key) is not None and raw.get(key) is not None:
            delta=out[key]-raw[key]
            hints.append(f'Processed的{label}相对能量比Raw{"高" if delta>=0 else "低"}{abs(delta):.2f}dB。')
    for key,label,threshold in [('f0_median_hz','基频中位数',8),('integrated_lufs','综合响度',1),
                                ('low_energy_db_relative','低频相对能量',2),
                                ('presence_energy_db_relative','Presence相对能量',2),
                                ('high_energy_db_relative','高频相对能量',2)]:
        if base.get(key) is not None and out.get(key) is not None:
            delta=out[key]-base[key]
            hints.append(f'{label}比基准{"高" if delta>=0 else "低"}{abs(delta):.2f}；'
                         +('可作为试听对照线索。' if abs(delta)>=threshold else '该项差异较小。'))
    hints.append('这些测量受文本、发音、录音与情绪影响，不能换算为音色相似百分比；不自动修改参数。')
    return hints
