"""Persistent Remotion still/animation preview; caption-only, never invokes TTS."""
from mac_paths import path as _portable_path
from pathlib import Path
from copy import deepcopy
import json
import os
import subprocess
import threading
import time
import uuid
import queue
from subtitle_fonts import freeze, media

ROOT=Path(_portable_path(r'D:\GPTData\temp\assembler_subtitle_preview'))

def preview_dimensions(cfg, width=225):
    if cfg.get('material_format') != 'sk_diverse':
        return (width, round(width*16/9))
    return (width, max(2, round(width*int(cfg['height'])/int(cfg['width']))))

class PreviewRenderer:
    def __init__(self,cfg):
        self.cfg=dict(cfg);self.process=None;self.lock=threading.Lock();self.closed=False
        ROOT.mkdir(parents=True,exist_ok=True)
        self.folder=ROOT/uuid.uuid4().hex;self.folder.mkdir()
        self.last_seconds=0
    def render(self,style,text,*,output=None,width=1080,height=1920,extra=None,cues=None,duration=6.,fps=30):
        from core import remotion_environment, local_media_server, APP_DIR
        with self.lock:
            if self.closed:raise RuntimeError('字幕预览器已关闭')
            style=freeze(style);paths={};key=media(style,paths)
            if self.process is None:
                node,dep=remotion_environment(self.cfg);self.dep=str(dep)
                env=dict(os.environ,TEMP=str(ROOT),TMP=str(ROOT))
                self.errors=(self.folder/'renderer.log').open('a',encoding='utf-8')
                self.process=subprocess.Popen([node,str(APP_DIR/'renderer/preview.cjs')],stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=self.errors,text=True,encoding='utf-8',env=env,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.messages=queue.Queue()
                def read(process,messages):
                    for line in process.stdout:
                        if line.startswith('RESULT:'):messages.put(json.loads(line[7:]))
                    messages.put({'error':'字幕预览进程退出，详见 '+str(self.folder/'renderer.log')})
                threading.Thread(target=read,args=(self.process,self.messages),daemon=True).start()
            video=(extra or {}).get('mode')=='video'
            output=Path(output) if output else self.folder/(uuid.uuid4().hex+('.mp4' if video else '.png'))
            output.parent.mkdir(parents=True,exist_ok=True)
            with local_media_server(paths) as base:
                if key:style['fontSrc']=base+key
                if cues is None:
                    from smart_subtitles import build
                    cues,self.last_diagnostics=build(text,style,duration,fps,width=width,height=height)
                props=dict(width=width,height=height,fps=fps,durationInFrames=round(duration*fps),subtitleProfile={'style':style},subtitles=cues)
                self.last_cues=deepcopy(cues)
                request=dict(dependencyRoot=self.dep,browser=self.cfg.get('browser',''),output=str(output),props=props)
                if not video:request['frame']=min(round(duration*fps)-1,max(0,cues[0]['start_frame']+round(.3*fps))) if cues else 0
                if extra:request.update(deepcopy(extra))
                self.process.stdin.write(json.dumps(request,ensure_ascii=False)+'\n');self.process.stdin.flush()
                try:result=self.messages.get(timeout=120)
                except queue.Empty:
                    self.process.kill();self.process=None
                    raise RuntimeError('字幕预览子进程超过120秒，已结束本编辑器预览；生产进程不受影响')
                if result.get('error'):raise RuntimeError(result['error'])
                self.last_seconds=result['seconds'];return output
    def playback(self,style,text):
        duration=min(18.,max(4.,len(text)/5))
        pw,ph=preview_dimensions(self.cfg)
        rw,rh=preview_dimensions(self.cfg,540);rh=round(rh/2)*2
        path=self.render(style,text,width=rw,height=rh,duration=duration,fps=30,extra={'mode':'video'})
        import shutil
        ffmpeg=self.cfg.get('ffmpeg') or shutil.which('ffmpeg') or _portable_path(r'D:\AAA\bin\ffmpeg.exe')
        gif=path.with_suffix('.gif')
        subprocess.run([ffmpeg,'-v','error','-y','-i',str(path),'-filter_complex',
                       f'fps=12,scale={pw}:{ph}:flags=lanczos,split[a][b];[a]palettegen=max_colors=128:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5',
                       '-loop','0',str(gif)],check=True,
                       timeout=30,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return gif,self.last_cues
    def close(self):
        self.closed=True
        # Closing stdin requests a graceful shutdown after the current still.
        if self.process:
            try:self.process.stdin.close()
            except (OSError,ValueError):pass
