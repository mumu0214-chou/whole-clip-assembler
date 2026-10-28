# -*- coding: utf-8 -*-
"""Optional 4-second REAL Remotion integration check in the user's local environment."""
from pathlib import Path
import copy
import math
import struct
import subprocess
import sys
import uuid

from core import *


def main():
    data = APP_DIR / 'data'
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update(read_json(data / 'settings.json', {}))
    ffmpeg = resolve_tool('ffmpeg', cfg['ffmpeg'])
    resolve_tool('ffprobe', cfg['ffprobe'])
    node, dep_root = remotion_environment(cfg)
    print('Remotion工程：', dep_root)
    root = data / 'render_selftest' / uuid.uuid4().hex[:8]
    material = root / '素材'
    music_dir = root / '音乐'
    music_dir.mkdir(parents=True)
    cfg.update(material_root=str(material), music_root=str(music_dir), output_root=str(root / '输出'),
               index_path='', count=1, min_seconds=4., max_seconds=4., preferred_min=4., preferred_max=4.,
               music_mode='natural', selected_music='', c_min=1, c_max=1,
               width=320, height=480, fps=30, concurrency=1, seed='selftest', plan_seconds_per_video=2.)
    cfg.update(PROCESSING_DEFAULTS)
    cfg['group_folders'] = {'A':'开头','B':'中间','C':'中间2','D':'结尾'}
    colors = ['red', 'green', 'blue', 'orange']
    for group, color, tone in zip(GROUPS, colors, [200,300,400,500]):
        folder = material / cfg['group_folders'][group]
        folder.mkdir(parents=True)
        run_capture([ffmpeg,'-v','error','-nostdin','-y','-f','lavfi','-i',f'color={color}:size=320x480:rate=30:duration=1',
                     '-f','lavfi','-i',f'sine=frequency={tone}:duration=1:sample_rate=48000',
                     '-c:v','libx264','-threads','1','-pix_fmt','yuv420p','-c:a','aac',str(folder/'1.mp4')])
    run_capture([ffmpeg,'-v','error','-nostdin','-y','-f','lavfi','-i','sine=frequency=1000:duration=4:sample_rate=48000',
                 '-ac','2',str(music_dir/'测试音乐.wav')])
    store = Store(root/'state')
    clips, music, warnings = scan_library(cfg,store,print)
    original_hashes = {c['path']:sha256(Path(c['path'])) for c in clips}
    for c in clips:
        c['reviewed']=True
    ranges=[{'id':'test4sec','music_hash':music[0]['hash'],'start':0.,'end':4.,'confirmed':True}]
    batch=plan_batch(clips,music,ranges,cfg,store,print)
    p=save_batch(batch,store)
    result=render_batch(p,store,print)
    item=result['items'][0]
    if item['status']!='completed':
        raise AssemblyError(item.get('error','渲染未完成'))
    output=Path(item['output_path'])
    decoded=subprocess.run([ffmpeg,'-v','error','-i',str(output),'-map','0:a:0','-ac','1','-ar','8000','-f','f32le','-'],
                           capture_output=True,check=True).stdout
    audio=struct.unpack('<'+'f'*(len(decoded)//4),decoded)
    def amp(samples,freq):
        real=sum(x*math.cos(2*math.pi*freq*i/8000) for i,x in enumerate(samples))
        imag=sum(x*math.sin(2*math.pi*freq*i/8000) for i,x in enumerate(samples))
        return math.hypot(real,imag)
    ratios=[]
    for sec,tone in enumerate([200,300,400,500]):
        segment=audio[int((sec+.25)*8000):int((sec+.75)*8000)]
        ratio=amp(segment,tone)/max(amp(segment,1000),1e-12)
        if ratio>.005:
            raise AssemblyError(f'音轨隔离验证未通过：第{sec+1}段比值{ratio}')
        ratios.append(ratio)
    if any(sha256(Path(p))!=h for p,h in original_hashes.items()):
        raise AssemblyError('原素材指纹发生变化')
    # Run again. Receipts should avoid a second render and usage must not double count.
    output_before=sha256(output)
    resume=render_batch(p,store,print)
    assert sha256(output)==output_before
    assert len(store.usage())==1
    atomic_json(root/'selftest_result.json',{'real_remotion_render':'PASS','audio_isolation_ratios':ratios,
                'original_files_unchanged':True,'resume_idempotency':True,'output':str(output)})
    print('\n真实Remotion渲染、原声隔离、完整时长、恢复幂等测试通过。')
    print('测试成片：',output)
    print('不会修改你的真实素材、音乐或正式索引。')

if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print('\n验证未完成：',exc,file=sys.stderr)
        code=1
    else:
        code=0
    if sys.stdin.isatty():
        input('\n按回车退出……')
    raise SystemExit(code)
