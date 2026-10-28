"""Four bounded log views. Disk queries and diagnostic exports stay off Tk's thread."""
from pathlib import Path
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import runtime_diagnostics as diag

class RuntimeLogPanel(ttk.Frame):
    def __init__(self,parent,app):
        super().__init__(parent);self.app=app;self.results=queue.Queue();self.records=[];self.paused=False
        self.last=0;self.closed=False;self.query_running=False;self.rows={};self.sort_reverse=True;self.row_counter=0;self.pending_history=[]
        bar=ttk.Frame(self);bar.pack(fill='x',padx=5,pady=4)
        self.filters={}
        for key,label,width in [('number','视频',5),('batch_id','批次',14),('component','模块',10),('stage','阶段',10),('keyword','关键词',16)]:
            ttk.Label(bar,text=label).pack(side='left');v=tk.StringVar();self.filters[key]=v
            ttk.Entry(bar,textvariable=v,width=width).pack(side='left',padx=2)
        self.level=tk.StringVar(value='INFO');self.filters['level']=self.level
        ttk.Combobox(bar,textvariable=self.level,values=['DEBUG','INFO','WARNING+ERROR','WARNING','ERROR','CRITICAL'],width=15,state='readonly').pack(side='left')
        ttk.Button(bar,text='筛选 / 历史查询',command=self.search).pack(side='left',padx=3)
        bar2=ttk.Frame(self);bar2.pack(fill='x',padx=5)
        for key,label,width in [('from','开始时间',20),('to','结束时间',20),('status','状态',12)]:
            ttk.Label(bar2,text=label).pack(side='left');v=tk.StringVar();self.filters[key]=v
            ttk.Entry(bar2,textvariable=v,width=width).pack(side='left',padx=3)
        ttk.Label(bar2,text='时间格式 YYYY-MM-DDTHH:MM；阶段可输入 remotion / voice').pack(side='left')
        buttons=ttk.Frame(self);buttons.pack(fill='x',padx=5,pady=4)
        self.pause_button=ttk.Button(buttons,text='暂停显示',command=self.toggle);self.pause_button.pack(side='left')
        ttk.Button(buttons,text='清空显示',command=self.clear).pack(side='left',padx=3)
        ttk.Button(buttons,text='打开日志目录',command=lambda:__import__('app').open_local(str(diag.hub().root))).pack(side='left')
        ttk.Button(buttons,text='导出诊断报告',command=self.export).pack(side='left',padx=3)
        ttk.Button(buttons,text='日志设置',command=self.settings).pack(side='left')
        self.status=tk.StringVar(value='等待任务；历史日志可按条件查询')
        ttk.Label(self,textvariable=self.status,wraplength=1150).pack(fill='x',padx=5)
        tabs=ttk.Notebook(self);tabs.pack(fill='both',expand=True,padx=5,pady=4)
        pages=[]
        for label in ('实时日志','任务耗时','错误记录','历史日志'):
            p=ttk.Frame(tabs);tabs.add(p,text=label);pages.append(p)
        self.live=self.text(pages[0]);self.history=self.text(pages[3])
        self.timing=self.tree(pages[1],('number','status','voice','audio','selection','subtitles','preprocess','remotion','ffmpeg','elapsed','slowest','attempt_id'),
                              ('视频','状态','配音(规划时)','音频(规划时)','选片(规划时)','字幕','素材预处理','Remotion','FFmpeg','本次总秒数','最慢阶段','本次执行'))
        self.errors=self.tree(pages[2],('timestamp','number','stage','message','retried','current_status','attempt_id'),('时间','视频','失败阶段','错误摘要','已重试','当前状态','本次执行'))
        self.timing.bind('<Double-1>',lambda e:self.detail(self.timing))
        self.errors.bind('<Double-1>',lambda e:self.detail(self.errors))
        self.timing._external_double_click=lambda:self.detail(self.timing)
        self.errors._external_double_click=lambda:self.detail(self.errors)
        self.timer=self.after(250,self.poll)

    def text(self,parent):
        text=tk.Text(parent,wrap='none',font=('Consolas',10));scroll=ttk.Scrollbar(parent,command=text.yview)
        text.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');text.pack(fill='both',expand=True)
        return text
    def tree(self,parent,columns,labels):
        tree=ttk.Treeview(parent,columns=columns,show='headings');scroll=ttk.Scrollbar(parent,command=tree.yview)
        tree.configure(yscrollcommand=scroll.set);scroll.pack(side='right',fill='y');tree.pack(fill='both',expand=True)
        for c,label in zip(columns,labels):
            tree.heading(c,text=label,command=lambda key=c,t=tree:self.sort(t,key));tree.column(c,width=90 if c not in ('message','attempt_id') else 210,stretch=True)
        return tree
    def sort(self,tree,key):
        def val(i):
            x=tree.set(i,key)
            try:return (0,float(x))
            except ValueError:return (1,x)
        for n,i in enumerate(sorted(tree.get_children(),key=val,reverse=self.sort_reverse)):tree.move(i,'',n)
        self.sort_reverse=not self.sort_reverse
    def values(self):return {k:v.get().strip() for k,v in self.filters.items()}
    def toggle(self):self.paused=not self.paused;self.pause_button.configure(text='继续显示' if self.paused else '暂停显示')
    def clear(self):
        self.live.delete('1.0','end');self.history.delete('1.0','end');self.records=[]
        self.timing.delete(*self.timing.get_children());self.errors.delete(*self.errors.get_children());self.rows={}
        with diag.hub().lock:self.last=len(diag.hub().recent);self.last_mono=max((d.get('monotonic',0) for d in diag.hub().recent),default=0)
    def search(self):
        if self.query_running:return
        self.query_running=True;filters=self.values();root=diag.hub().root
        def work():
            try:diag.hub().flush();self.results.put(('search',diag.query(root,filters)))
            except Exception as exc:diag.error(exc,component='log-ui');self.results.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()
    def insert(self,widget,d):
        auto=widget.yview()[1]>.98
        widget.insert('end',f"{d.get('timestamp','')} {d.get('level','')} | 视频 {(d.get('number') or '—')} | {d.get('stage','')} | {d.get('message','')}\n")
        if int(widget.index('end-1c').split('.')[0])>2500:widget.delete('1.0','301.0')
        if auto:widget.see('end')
    def add(self,d,historical=False):
        if not historical and not diag.matches(d,self.values()):return
        self.insert(self.history if historical else self.live,d)
        if d.get('event')=='task_end' and d.get('kind')=='video':
            s=dict(d.get('stages',{}));slow=max(s,key=s.get,default='');rid='t'+d['attempt_id']
            for k in ('voice','audio','selection'):
                if k in d.get('prior_stages',{}):s[k]=d['prior_stages'][k]
            self.rows[rid]=d
            values=[d.get('number'),d.get('status')]+[f'{s[k]:.3f}' if k in s else '—' for k in ('voice','audio','selection','subtitles','preprocess','remotion','ffmpeg')]+[f"{d['elapsed']:.3f}",diag.STAGES.get(slow,slow),d['attempt_id'][:10]]
            if self.timing.exists(rid):self.timing.item(rid,values=values)
            else:self.timing.insert('','end',iid=rid,values=values)
        if d.get('event')=='exception':
            self.row_counter+=1;rid='e'+str(self.row_counter);self.rows[rid]=d
            self.errors.insert('','end',iid=rid,values=[d.get(k,'') for k in ('timestamp','number','stage','message')]+['未发现后续执行','待处理',d.get('attempt_id','')])
        if d.get('event') in ('task_start','task_end'):
            for rid in self.errors.get_children():
                old=self.rows.get(rid,{})
                if old.get('batch_id')==d.get('batch_id') and old.get('job_id')==d.get('job_id'):
                    retried=bool(old.get('attempt_id') and d.get('attempt_id')!=old.get('attempt_id') and d.get('monotonic',0)>old.get('monotonic',0))
                    if retried:self.errors.set(rid,'retried','是')
                    if retried or old.get('attempt_id')==d.get('attempt_id'):
                        status=d.get('status','执行中');self.errors.set(rid,'current_status',status)
                        old['current_status']=status;old['retried']=retried or old.get('retried',False)
        for tree in (self.timing,self.errors):
            ids=tree.get_children()
            if len(ids)>2000:
                for i in ids[:200]:tree.delete(i);self.rows.pop(i,None)
    def poll(self):
        if self.closed:return
        if not getattr(self,'_external_visible',True):
            self.timer=self.after(750,self.poll)
            return
        try:
            for _ in range(5):
                kind,value=self.results.get_nowait()
                if kind in ('search','error'):self.query_running=False
                if kind=='search':
                    self.history.delete('1.0','end')
                    self.records=value
                    self.pending_history=list(value[-2000:])
                    stats=diag.statistics(value);self.status.set(f'查询到 {len(value)} 条事件；视频执行 {stats["attempts"]} 次；状态 {stats["counts"]}；缓存命中率 {stats["cache_hit_rate"]}')
                elif kind=='export':self.status.set('诊断报告已导出：'+str(value))
                elif kind=='detail':
                    dialog,widget,details=value
                    if dialog.winfo_exists():widget.insert('end',details)
                else:self.status.set(str(value))
        except queue.Empty:pass
        for d in self.pending_history[:80]:self.add(d,True)
        del self.pending_history[:80]
        h=diag.hub()
        if h and not self.paused:
            with h.lock:recent=list(h.recent);active=list(h.active.values())
            new=[d for d in recent if d.get('monotonic',0)>getattr(self,'last_mono',0)]
            for d in new[-120:]:self.add(d)
            if recent:self.last_mono=max(d.get('monotonic',0) for d in recent)
            if active:
                self.status.set(' | '.join(f"批次 {d.get('batch_id','')[:10]} 视频 {(d.get('number') or '—')} {diag.STAGES.get(d.get('stage'),d.get('stage') or '后台执行')} {__import__('time').monotonic()-d['started_monotonic']:.1f}秒" for d in active[-4:]))
            if h.fault:self.status.set(h.fault)
        self.timer=self.after(250,self.poll)
    def detail(self,tree):
        ids=tree.selection()
        if not ids:return
        d=self.rows.get(ids[0],{})
        native=bool(getattr(self.app,'_qt_parent',None))
        if native:
            from auxiliary_dialogs_qt import log_detail
            dialog,text=log_detail(self,d);self._qt_log_detail=dialog
        else:
            dialog=tk.Toplevel(self);dialog.title('任务详情 / 完整异常');dialog.geometry('1000x650')
            text=self.text(dialog);text.insert('end',json.dumps(d,ensure_ascii=False,indent=2))
        log_root=diag.hub().root
        def related():
            rows=diag.query(log_root,{'job_id':d.get('job_id','')},limit=20000)
            rows=[row for row in rows if row.get('attempt_id')==d.get('attempt_id') and row.get('run_id')==d.get('run_id')]
            detail='\n\n关联子进程记录（末尾输出；完整输出保留在日志目录）：\n'
            for row in rows:
                if row.get('event')!='process_end':continue
                detail+=json.dumps(row,ensure_ascii=False,indent=2)+'\n'
                for name in ('stdout','stderr'):
                    paths=row.get(name+'_parts',[row.get(name+'_path','')])
                    for source in paths[-2:]:
                        path=Path(source)
                        if path.is_file() and path.resolve().is_relative_to(log_root.resolve()):
                            try:
                                with path.open('rb') as stream:
                                    stream.seek(max(0,path.stat().st_size-32768));tail=stream.read().decode('utf-8',errors='replace')
                                detail+=str(path)+'\n'+tail+'\n'
                            except OSError as exc:detail+='读取日志失败：'+str(exc)+'\n'
            self.results.put(('detail',(dialog,text,detail)))
        threading.Thread(target=related,daemon=True).start()
        if native:return
        def copy():dialog.clipboard_clear();dialog.clipboard_append(text.get('1.0','end-1c'))
        ttk.Button(dialog,text='复制详情',command=copy).pack(side='left')
        ttk.Button(dialog,text='打开日志目录',command=lambda:__import__('app').open_local(str(diag.hub().root/d.get('run_id','')))).pack(side='left')
        def jump():
            job=d.get('job_id','')
            if self.app.plan_tree.exists(job):
                self.app.tabs.select(self.app.main_tab);self.app.plan_tree.selection_set(job);self.app.plan_tree.see(job)
            else:self.status.set('该任务不在当前批次，请打开对应历史Manifest。')
        ttk.Button(dialog,text='跳转当前批次任务',command=jump).pack(side='left')
    def export(self):
        path=filedialog.asksaveasfilename(parent=self,defaultextension='.zip',initialdir=str(self.app.data_dir/'diagnostics_exports'),initialfile='diagnostic.zip',filetypes=[('诊断ZIP','*.zip')])
        if not path:return
        filters=self.values();batch=__import__('copy').deepcopy(self.app.batch)
        def work():
            try:
                from core import APP_VERSION
                result=diag.export_report(path,diag.hub().root,batch=batch,version=APP_VERSION,filters=filters)
                self.results.put(('export',result))
            except Exception as exc:diag.error(exc,component='log-export');self.results.put(('error',str(exc)))
        threading.Thread(target=work,daemon=True).start()
    def settings(self):
        if getattr(self.app,'_qt_parent',None):
            from auxiliary_dialogs_qt import log_settings
            return log_settings(self)
        win=tk.Toplevel(self);win.title('日志设置');values={};h=diag.hub()
        for n,(key,label,current) in enumerate([('retention_days','保留天数',h.retention_days),('file_mb','单文件MiB',h.file_bytes/1024**2),('total_mb','总空间MiB',h.total_bytes/1024**2)]):
            ttk.Label(win,text=label).grid(row=n,column=0,padx=8,pady=6);v=tk.StringVar(value=str(current));values[key]=v;ttk.Entry(win,textvariable=v).grid(row=n,column=1)
        debug=tk.BooleanVar(value=h.debug);ttk.Checkbutton(win,text='记录DEBUG（默认关闭）',variable=debug).grid(row=3,columnspan=2)
        def save():
            try:
                data={k:float(v.get()) for k,v in values.items()}
                if any(v<=0 for v in data.values()):raise ValueError('设置必须大于0')
                h.retention_days=data['retention_days'];h.file_bytes=int(data['file_mb']*1024**2);h.total_bytes=int(data['total_mb']*1024**2);h.debug=debug.get()
                self.app.cfg['diagnostic_settings']=dict(data,debug=debug.get())
                from core import atomic_json
                atomic_json(self.app.config_path,self.app.cfg);win.destroy()
            except Exception as exc:messagebox.showerror('日志设置',str(exc),parent=win)
        ttk.Button(win,text='保存',command=save).grid(row=4,columnspan=2,pady=8)
    def close(self):self.closed=True;self.after_cancel(self.timer)
