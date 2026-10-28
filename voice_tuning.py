"""Shared, optional tuning fields; old profiles are extended only in memory."""
from functools import lru_cache
import subprocess

STANDARD_PREVIEW_TEXT = '你好，这是一段音色试听。'
TUNING_DEFAULTS = {
    'pitch_enabled':False,'pitch_semitones':0.,
    'deesser_enabled':False,'deesser_intensity':.5,'deesser_max':.5,'deesser_frequency':.5,
    'golden_voice':None,'standard_preview_text':STANDARD_PREVIEW_TEXT,'preview_seed':None,
}
PREVIEW_PRESETS = {
    '当前自定义文本':None,
    '普通陈述':'今天和大家聊聊，这份食材可以怎么做。',
    '问句/感叹':'你看到了吗？这份食材看起来真不错！',
    '稍长连续口播':'大家好，今天继续分享食材的做法。先准备好需要的东西，接着按照自己的口味慢慢处理，最后和家人一起享用。',
}
COMPRESSION_PRESETS = {
    '自然':{'compressor_threshold':-18.,'compressor_ratio':1.5,'compressor_attack':15.,'compressor_release':120.,'compressor_makeup':0.},
    '适中':{'compressor_threshold':-20.,'compressor_ratio':2.5,'compressor_attack':10.,'compressor_release':100.,'compressor_makeup':0.},
    '强力':{'compressor_threshold':-24.,'compressor_ratio':4.,'compressor_attack':5.,'compressor_release':80.,'compressor_makeup':0.},
}


@lru_cache(maxsize=8)
def filter_capabilities(ffmpeg):
    result=subprocess.run([str(ffmpeg),'-hide_banner','-filters'],capture_output=True,
                          timeout=15,creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess,'CREATE_NO_WINDOW') else 0)
    if result.returncode: return {'pitch':False,'deesser':False}
    listing=result.stdout.decode('utf-8','replace')
    return {'pitch':' rubberband ' in listing,'deesser':' deesser ' in listing}
