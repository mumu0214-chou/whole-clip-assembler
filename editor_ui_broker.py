"""Legacy editors run on one Tk event loop in an owned, isolated process.

Only editor requests/results cross this boundary. No App, production worker,
batch cleanup, model startup or renderer is created by the helper.
"""
from copy import deepcopy
from pathlib import Path
import json, os, subprocess, sys, uuid
from qt_runtime import activate
activate()
from PySide6 import QtCore as C, QtWidgets as W


def append(path, value):
    with Path(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False)+'\n')


class EditorHandle:
    def __init__(self, broker, kind, folder, process, callback=None):
        self.broker, self.kind, self.folder, self.process = broker, kind, folder, process
        self.callback = callback
        self.offset = 0
        self.ready = self.closing = self.close_cancelled = False
        self.expected_exit = False
    def winfo_exists(self):return self.process.poll() is None
    def send(self, op, **fields):
        if self.winfo_exists():append(self.folder/'commands.jsonl',dict(op=op,**fields))
    def lift(self):self.send('raise')
    def focus_set(self):pass
    def show_profile(self, profile,spec_id=None):self.send('profile',profile_id=profile,spec_id=spec_id)
    def close(self):
        if not self.closing:
            self.closing=True;self.close_cancelled=False;self.send('close')
    def destroy(self):self.close()


class EditorBroker(C.QObject):
    def __init__(self, window):
        super().__init__(window);self.window=window;self.app=window.controller;self.handles={}
        self.timer=C.QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.poll);self.timer.start()
    def open(self,kind,*,profile_id=None,on_load=None,voice_root=None):
        key=kind+(':'+getattr(on_load,'__name__','') if on_load else '')
        old=self.handles.get(key)
        if old and old.winfo_exists():
            if profile_id:old.show_profile(profile_id,self.app.collect_config().get('product_spec_id','default') if profile_id==self.app.cfg['editing_profile_id'] else 'default')
            old.lift();return old
        folder=self.app.data_dir/'editor_sessions'/uuid.uuid4().hex;folder.mkdir(parents=True)
        cfg=deepcopy(self.app.collect_config())
        request=dict(kind=kind,data_dir=str(self.app.data_dir.resolve()),config=cfg,
                     profile_id=profile_id or cfg['editing_profile_id'],voice_root=str(voice_root) if voice_root else None)
        from core import atomic_json
        atomic_json(folder/'request.json',request)
        env=os.environ.copy();env.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
        log=(folder/'host.log').open('ab')
        try:
            process=subprocess.Popen([sys.executable,str(Path(__file__).with_name('editor_ui_host.py')),str(folder/'request.json')],
                cwd=str(Path(__file__).parent),env=env,stdout=log,stderr=log,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        finally:log.close()
        handle=EditorHandle(self,kind,folder,process,on_load);self.handles[key]=handle
        title={'scripts':'文案库','voices':'音色管理','subtitles':'字幕字体 / 样式','history':'历史批次'}[kind]
        self.app.log('正在打开'+title+'；剪辑任务继续。')
        return handle
    def apply(self, handle, event):
        kind=event.get('event')
        if kind=='ready':handle.ready=True
        elif kind=='closed':handle.expected_exit=True
        elif kind=='close_cancelled':handle.closing=False;handle.close_cancelled=True
        elif kind=='log':self.app.log(event['message'])
        elif kind=='voice_changed':self.app.refresh_voice_profiles()
        elif kind=='catalog_changed':self.app.refresh_catalog_controls()
        elif kind=='load_batch':
            try:
                if handle.callback:handle.callback(Path(event['path']))
                else:self.app.open_batch_path(Path(event['path']))
                handle.send('load_result',ok=True)
            except Exception as exc:handle.send('load_result',ok=False,error=str(exc))
        elif kind=='subtitle_save':
            # Main remains the sole settings writer; never replace other unsaved
            # controls or profile documents with a helper's stale snapshot.
            try:
                if event['profile_id']!=self.app.cfg['editing_profile_id']:
                    raise ValueError('剪辑方案已切换，请重新打开字幕编辑器。')
                from subtitle_styles import normalize
                self.app.cfg['subtitle_style']=deepcopy(normalize(event['style']))
                self.app.cfg=self.app.collect_config();self.app.persist_config(self.app.cfg)
                handle.send('save_result',ok=True)
            except Exception as exc:handle.send('save_result',ok=False,error=str(exc))
        elif kind=='error':self.app.log('编辑窗口错误：'+event['message'])
    def poll(self):
        if getattr(self.window,'_ticking',False) or getattr(self.app,'_qt_callback_depth',0):return
        for key,handle in list(self.handles.items()):
            path=handle.folder/'events.jsonl'
            if path.exists():
                with path.open('rb') as stream:
                    stream.seek(handle.offset)
                    for line in stream:
                        if not line.endswith(b'\n'):break
                        handle.offset+=len(line)
                        try:self.apply(handle,json.loads(line))
                        except Exception as exc:self.app.log('编辑窗口结果处理失败：'+str(exc))
            code=handle.process.poll()
            if code is not None:
                self.handles.pop(key,None)
                if code or not handle.expected_exit:
                    message=f'编辑窗口已异常退出（{code}）。工作台及视频任务继续。\n详情：{handle.folder / "host.log"}'
                    self.app.log(message);W.QMessageBox.warning(self.window,'编辑窗口异常',message)
    def request_close(self):
        self.poll()
        for handle in self.handles.values():handle.close()
        return not self.handles
    def close_was_cancelled(self):return any(h.close_cancelled for h in self.handles.values())
