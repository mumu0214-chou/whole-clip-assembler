# -*- coding: utf-8 -*-
"""Optional real Remotion export-processing selftest. Generates synthetic inputs only."""
from __future__ import annotations
import copy
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import uuid
from core import *
from video_processing import PROCESSING_DEFAULTS, file_md5

def main():
    cfg=copy.deepcopy(DEFAULTS)
    cfg.update(read_json(APP_DIR/'data'/'settings.json',{}))
    ff=resolve_tool('ffmpeg',cfg['ffmpeg']);fp=resolve_tool('ffprobe',cfg['ffprobe'])
    remotion_environment(cfg)
    root=APP_DIR/'data'/'processing_selftest'/uuid.uuid4().hex[:10]
    materials=root/'synthetic';music_dir=root/'music';music_dir.mkdir(parents=True)
    cfg.update(material_root=str(materials),music_root=str(music_dir),index_path='',output_root=str(root/'output'),
               group_folders={'A':'开头','B':'中间','C':'中间2','D':'结尾'},
               count=1,c_min=1,c_max=1,min_seconds=3.2,max_seconds=3.2,preferred_min=3.2,preferred_max=3.2,
               width=320,height=480,fps=30,concurrency=1,seed='processing_selftest',music_mode='natural',selected_music='',
               top_text_enabled=False,plan_seconds_per_video=2,attempts_per_video=60)
    cfg.update(PROCESSING_DEFAULTS)
    cfg.update(processing_enabled=True,processing_color=True,processing_brightness=1.05,processing_contrast=1.05,
               processing_saturation=.9,processing_mirror=True,processing_speed_enabled=True,processing_speed=1.25,
               processing_drop=True,processing_drop_every=10,processing_fps_enabled=True,processing_fps=25,
               processing_hash_refresh=True)
    colors=['red','green','orange','pink']
    for group,color,tone in zip(GROUPS,colors,[200,300,400,500]):
        folder=materials/cfg['group_folders'][group];folder.mkdir(parents=True)
        run_capture([ff,'-v','error','-y','-f','lavfi','-i',f'color={color}:size=320x480:rate=30:duration=1,drawbox=x=0:y=0:w=160:h=480:color=blue:t=fill',
                     '-f','lavfi','-i',f'sine=frequency={tone}:duration=1:sample_rate=48000',
                     '-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(folder/'1.mp4')])
    run_capture([ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=1000:sample_rate=48000:duration=3.2','-ac','2',str(music_dir/'tone.wav')])
    # Overlay stays readable while the underlying left/right pattern is mirrored.
    text_file=root/'test_text.txt';text_file.write_text('EXPORT TEST\n1 2 3',encoding='utf-8')
    cfg.update(top_text_enabled=True,top_text_library=str(text_file),top_text_font_size=38,top_text_top=15)
    store=Store(root/'state')
    clips,music,warnings=scan_library(cfg,store,print)
    for c in clips:c['reviewed']=True
    old={c['path']:c['hash'] for c in clips}
    ranges=[{'id':'full','music_hash':music[0]['hash'],'start':0.,'end':3.2,'confirmed':True}]
    batch=plan_batch(clips,music,ranges,cfg,store,print);bp=save_batch(batch,store)
    finished=render_batch(bp,store,print);item=finished['items'][0]
    if item['status']!='completed':raise AssemblyError(item.get('error','渲染失败'))
    out=Path(item['output_path']);meta=probe(out,fp)
    assert meta['fps_native']=='25/1' and int(meta['nb_frames'])==80
    assert all(sha256(Path(p))==h for p,h in old.items())
    samples_bytes=subprocess.run([ff,'-v','error','-i',str(out),'-map','0:a:0','-ar','8000','-ac','1','-f','f32le','-'],capture_output=True,check=True).stdout
    samples=struct.unpack('<'+'f'*(len(samples_bytes)//4),samples_bytes)[4000:16000]
    def amplitude(f):
        return math.hypot(sum(v*math.cos(2*math.pi*f*i/8000) for i,v in enumerate(samples)),
                          sum(v*math.sin(2*math.pi*f*i/8000) for i,v in enumerate(samples)))
    ratio=max(amplitude(x) for x in (200,300,400,500))/max(1e-12,amplitude(1000))
    assert ratio<.005, f'原声音轨隔离失败：{ratio}'
    before=sha256(out);rerun=render_batch(bp,store,print)
    assert sha256(out)==before and len(store.usage())==1
    atomic_json(root/'selftest_result.json',{'real_remotion_processing':'PASS','duration':meta['duration'],'fps':meta['fps_native'],
        'frames':meta['nb_frames'],'original_audio_leak_ratio':ratio,'originals_unchanged':True,
        'resume_idempotent':True,'output_md5':file_md5(out),'output':str(out),
        'visual_review':'请自行查看字幕未被镜像、背景左右翻转和色彩变化；程序不冒充人工审美验收。'})
    print('\n真实渲染、25fps/1.25倍速、BGM隔离、原片保护、重复恢复检查通过。')
    print('测试片：',out)
    print('请再观看确认新增顶部文案方向正确。')

if __name__=='__main__':
    try:main()
    except Exception as exc:print('\n自测未完成：',exc,file=sys.stderr);code=1
    else:code=0
    if sys.stdin.isatty():input('\n按回车退出……')
    raise SystemExit(code)
