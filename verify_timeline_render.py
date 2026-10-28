# -*- coding: utf-8 -*-
"""Real local Remotion selftest for trim + clip speed + transition + overlay isolation.
Uses synthetic colored inputs only. Existing user media/index are never touched.
"""
from __future__ import annotations
import argparse
import copy
import math
from pathlib import Path
import struct
import subprocess
import sys
import uuid
from core import (APP_DIR, DEFAULTS, GROUPS, Store, AssemblyError, resolve_tool, remotion_environment,
                  run_capture, scan_library, save_batch, render_batch, read_json, atomic_json, sha256,
                  sequence_key, set_key, technical_qa, now)
from video_processing import PROCESSING_DEFAULTS, processing_settings
from timeline_processing import bake_timeline, timing_digest, TIMELINE_REVISION


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--data-dir',default=str(APP_DIR/'data'))
    parser.add_argument('--no-pause',action='store_true')
    args=parser.parse_args()
    data_dir=Path(args.data_dir)
    cfg=copy.deepcopy(DEFAULTS);cfg.update(read_json(data_dir/'settings.json',{}))
    ff=resolve_tool('ffmpeg',cfg['ffmpeg']);fp=resolve_tool('ffprobe',cfg['ffprobe'])
    remotion_environment(cfg)
    root=data_dir/'timeline_selftest'/uuid.uuid4().hex[:10]
    material=root/'synthetic';music_dir=root/'music';music_dir.mkdir(parents=True)
    cfg.update(PROCESSING_DEFAULTS)
    cfg.update(material_root=str(material),music_root=str(music_dir),index_path='',output_root=str(root/'output'),
               group_folders={'A':'开头','B':'中间','C':'中间2','D':'结尾'},
               count=1,c_min=1,c_max=1,min_seconds=4.,max_seconds=10.,preferred_min=5.,preferred_max=8.,
               width=320,height=568,fps=30,concurrency=1,seed='timeline-selftest',music_mode='fade',selected_music='',
               processing_enabled=True,
               processing_trim=True,processing_trim_scope='positions',processing_trim_positions='2,3',
               processing_trim_mode='both',processing_trim_head=.25,processing_trim_tail=.25,
               processing_speed_enabled=True,processing_speed_scope='positions',processing_speed_positions='2',processing_speed=1.25,
               processing_transition=True,processing_transition_scope='all',processing_transition_type='fade',processing_transition_seconds=.2,
               processing_fx=True,processing_fx_scope='positions',processing_fx_positions='3',processing_fx_type='vignette',
               top_text_enabled=False)
    for i,g in enumerate(GROUPS):
        folder=material/cfg['group_folders'][g];folder.mkdir(parents=True)
        run_capture([ff,'-v','error','-y','-f','lavfi','-i',f'testsrc2=size=320x568:rate=30:duration=2,hue=h={i*60}',
                     '-f','lavfi','-i',f'sine=frequency={200+i*100}:sample_rate=48000:duration=2',
                     '-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(folder/'1.mp4')])
    music_file=music_dir/'BGM.wav'
    run_capture([ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=1000:sample_rate=48000:duration=12','-ac','2',str(music_file)])
    store=Store(root/'state');clips,music,_=scan_library(cfg,store,print)
    clips.sort(key=lambda c:GROUPS.index(c['group']))
    for c in clips:c['reviewed']=True
    old={c['path']:c['hash'] for c in clips}
    profile_seed='synthetic-timeline-fixed'
    out,transitions,total=bake_timeline(clips,cfg,profile_seed)
    seconds=total/cfg['fps']
    assert total==183 and abs(seconds-6.1)<1e-8,(total,seconds)
    settings=processing_settings(cfg)
    item={'id':'timing-'+uuid.uuid4().hex,'number':1,'status':'planned','sequence_hash':sequence_key(clips),
          'set_hash':set_key(clips),'clips':out,'transitions':transitions,'total_frames':total,'duration':seconds,
          'source_duration_sum':8.,'frame_rounding_seconds':0.,'transition_overlap_seconds':.6,
          'profile_seed':profile_seed,'timeline_revision':TIMELINE_REVISION,'video_processing':settings,
          'file_export_id':'','source_audio':'discard',
          'top_text':{'enabled':True,'text':'TOP TEXT\nSTAYS STILL','index':1,'hash':'selftest','source':'synthetic'},
          'bgm':dict(music[0],start=0.,end=seconds,approved_end_limit=music[0]['duration'],range_id='test',natural=False,note='test tone')}
    item['timeline_digest']=timing_digest(item)
    cfg.update(top_text_font_size=38,top_text_top=15,top_text_max_width=.9)
    batch={'schema_version':1,'app_version':'1.0.5','id':'timeline_selftest_'+uuid.uuid4().hex[:8],
           'created_at':now(),'config':cfg,'seed':profile_seed,'items':[item],'warnings':[],
           'source_audio':'discard','scope':'synthetic_selftest','requested_count':1}
    bp=save_batch(batch,store)
    result=render_batch(bp,store,print);item=result['items'][0]
    if item['status']!='completed':raise AssemblyError(item.get('error','渲染未完成'))
    output=Path(item['output_path']);qa=technical_qa(output,item,cfg,fp,ff)
    assert all(sha256(Path(p))==h for p,h in old.items())
    audio=subprocess.run([ff,'-v','error','-i',str(output),'-map','0:a:0','-ar','8000','-ac','1','-f','f32le','-'],
                         capture_output=True,check=True).stdout
    samples=struct.unpack('<'+'f'*(len(audio)//4),audio)[4000:32000]
    def amp(freq):
        return math.hypot(sum(v*math.cos(2*math.pi*freq*i/8000) for i,v in enumerate(samples)),
                          sum(v*math.sin(2*math.pi*freq*i/8000) for i,v in enumerate(samples)))
    ratio=max(amp(f) for f in (200,300,400,500))/max(1e-12,amp(1000))
    assert ratio<.005,ratio
    digest=sha256(output);rerun=render_batch(bp,store,print)
    assert sha256(output)==digest and len(store.usage())==1
    report={'status':'PASS','expected_frames':183,'expected_duration':6.1,'qa':qa,
            'source_audio_ratio':ratio,'originals_unchanged':True,'resume_same_file':True,
            'output':str(output),'visual_review':'请观看转场、字幕、修剪位置；技术测试不代替主观观看。'}
    atomic_json(root/'selftest_result.json',report)
    print('\n真实渲染自测通过：4段，183帧/6.1秒，修边与局部变速、转场时长一致，仅BGM，重试不变。')
    print('成片：',output)
    return args.no_pause

if __name__=='__main__':
    try:no_pause=main();code=0
    except Exception as exc:print('\n自测未完成：',exc,file=sys.stderr);code=1;no_pause='--no-pause' in sys.argv
    if sys.stdin.isatty() and not no_pause:input('\n按回车退出……')
    raise SystemExit(code)
