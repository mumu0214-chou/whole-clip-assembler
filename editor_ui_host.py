"""Single-toolkit editor host; no production App or scheduler is instantiated."""
from pathlib import Path
from copy import deepcopy
import faulthandler, gc, json, queue, sys, time, tkinter as tk
from tkinter import messagebox


class EditorContext(tk.Tk):
    def __init__(self,request,folder):
        super().__init__();self.withdraw();self.folder=Path(folder);self.cfg=deepcopy(request['config'])
        from wheel_guard import install_tk
        install_tk(self)
        self.data_dir=Path(request['data_dir']);self.events=queue.Queue();self.offset=0;self.closing=False
        from background_tasks import Lanes
        self.lanes=Lanes(self.events);self.editor=None;self.last_gc=time.monotonic()
        self.after(40,self.pump)
    def emit(self,event,**fields):
        with (self.folder/'events.jsonl').open('a',encoding='utf-8') as stream:
            stream.write(json.dumps(dict(event=event,**fields),ensure_ascii=False)+'\n')
    def collect_config(self):return deepcopy(self.cfg)
    def var(self,key):return tk.StringVar(self,value=str(self.cfg.get(key,'')))
    def log(self,message):self.emit('log',message=str(message))
    def worker_log(self,message):self.events.put(('log',str(message)))
    def launch_voice(self,worker,done,on_error=None):return self.lanes.submit('voice',worker,done,on_error=on_error)
    def guard(self,callback):
        try:callback()
        except Exception as exc:self.emit('error',message=str(exc));messagebox.showerror('编辑窗口',str(exc),parent=self.editor)
    def save_settings(self):
        self.emit('subtitle_save',profile_id=self.cfg['editing_profile_id'],style=self.cfg['subtitle_style'])
        self.after(1,lambda:self.editor.status.set('正在保存字幕样式…') if self.editor.winfo_exists() else None)
    def request_close(self):
        win=self.editor
        if win and win.winfo_exists():
            command=win.protocol('WM_DELETE_WINDOW')
            if command:win.tk.call(command)
            else:win.destroy()
            if win.winfo_exists():self.emit('close_cancelled');return
        self.closing=True
        for lane in list(self.lanes.active):self.lanes.cancel(lane)
    def command(self,data):
        op=data['op'];win=self.editor
        if op=='close':self.request_close()
        elif op=='raise' and win.winfo_exists():win.deiconify();win.lift();win.focus_force()
        elif op=='profile' and win.winfo_exists():self.guard(lambda:win.show_profile(data['profile_id'],data.get('spec_id')))
        elif op=='save_result' and win.winfo_exists():
            win.status.set('完整字幕样式已保存；新任务生效。' if data['ok'] else '保存失败：'+data['error'])
        elif op=='load_result' and win.winfo_exists():
            if data['ok']:win.destroy()
            else:messagebox.showerror('历史批次无法打开',data['error'],parent=win)
    def pump(self):
        path=self.folder/'commands.jsonl'
        if path.exists():
            with path.open('rb') as stream:
                stream.seek(self.offset)
                for line in stream:
                    if not line.endswith(b'\n'):break
                    self.offset+=len(line);self.guard(lambda row=json.loads(line):self.command(row))
        for _ in range(80):
            try:ev=self.events.get_nowait()
            except queue.Empty:break
            if ev[0]=='log':self.log(ev[1])
            elif ev[0]=='script_tools_progress':
                if ev[1].winfo_exists():ev[1].status.set(ev[2])
            elif ev[0] in ('lane_done','lane_error'):
                _,lane,token,callback,value,_=ev;self.lanes.complete(lane,token)
                if callback:self.guard(lambda fn=callback,v=value:fn(v))
                elif ev[0]=='lane_error':self.log(value)
        if self.editor is not None and not self.editor.winfo_exists():
            if not self.closing:self.request_close()
            preview=getattr(self.editor,'preview_thread',None)
            if not self.lanes.active and not (preview and preview.is_alive()):self.emit('closed');self.destroy();return
        # Tcl objects must be finalized on their interpreter thread, even when
        # a background file operation triggers Python's cyclic GC threshold.
        if time.monotonic()-self.last_gc>5:gc.collect();self.last_gc=time.monotonic()
        self.after(80,self.pump)


def build(context,request):
    kind=request['kind']
    if kind=='scripts':
        from script_library import ScriptLibrary
        from script_library_ui import ScriptLibraryWindow
        context.script_library=ScriptLibrary(context.data_dir/'script_library.sqlite3')
        return ScriptLibraryWindow(context,context.script_library,profile_id=request['profile_id'])
    if kind=='voices':
        from voice_manager_ui import VoiceManagerWindow
        from voice_pipeline import PROFILE_ROOT
        return VoiceManagerWindow(context,lambda:context.emit('voice_changed'),root=Path(request.get('voice_root') or PROFILE_ROOT))
    if kind=='subtitles':
        from subtitle_editor_ui import SubtitleEditor
        return SubtitleEditor(context)
    if kind=='history':
        from core import Store
        from batch_history_ui import HistoryWindow
        context.store=Store(context.data_dir)
        win=HistoryWindow(context,on_load=lambda path:None)
        # Wait for the main controller to validate/load the chosen batch before
        # closing. Busy-render rejection remains the original controller's rule.
        win.load=lambda path:context.emit('load_batch',path=str(path))
        return win
    raise ValueError('未知编辑窗口：'+kind)


def main(path):
    path=Path(path);request=json.loads(path.read_text(encoding='utf-8'))
    crash=(path.parent/'native_fault.log').open('a',encoding='utf-8')
    faulthandler.enable(crash,all_threads=True);gc.disable()
    context=None
    try:
        context=EditorContext(request,path.parent);context.editor=build(context,request)
        # Hidden utility root is not a window-manager owner. Nested editor
        # dialogs still retain their visible editor as owner.
        context.editor.transient('');context.editor.deiconify();context.editor.lift()
        context.emit('ready',kind=request['kind']);context.mainloop()
    except BaseException as exc:
        if context:context.emit('error',message=str(exc))
        raise
    finally:
        faulthandler.disable();crash.close()


if __name__=='__main__':main(sys.argv[1])
