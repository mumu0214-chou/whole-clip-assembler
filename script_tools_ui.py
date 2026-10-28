"""Additive management tools using the existing script editor and UI worker lanes."""
from mac_paths import path as _portable_path
import json, tkinter as tk
from pathlib import Path
from tkinter import ttk, messagebox, filedialog, simpledialog
from paged_tree import PagedTree
from editing_profiles import NAMES
from external_voice import error

class ToolsWindow(tk.Toplevel):
    def __init__(self, owner, title):
        super().__init__(owner);self.owner=owner;self.app=owner.app;self.library=owner.library
        self.title(title);self.geometry('1120x700');self.minsize(850,520)
        self.status=tk.StringVar(value='');self.lane='script_io:'+str(id(self))
        self.footer=ttk.Frame(self);self.footer.pack(side='bottom',fill='x',padx=8,pady=6)
        ttk.Label(self.footer,textvariable=self.status,wraplength=1000).pack(side='left',fill='x',expand=True)
        ttk.Button(self.footer,text='取消后台操作',command=lambda:self.app.lanes.cancel(self.lane)).pack(side='right')
    def guarded(self, fn):
        try:fn()
        except Exception as exc:messagebox.showerror(self.title(),str(exc),parent=self)
    def button(self,parent,label,fn):ttk.Button(parent,text=label,command=lambda:self.guarded(fn)).pack(side='left',padx=3,pady=4)
    def background(self, worker, done):
        if self.app.lanes.running(self.lane):raise error('当前后台操作尚未结束。')
        def progress(text):self.app.events.put(('script_tools_progress',self,str(text)))
        def finished(value):
            if self.winfo_exists():done(value)
        def failed(exc):
            if self.winfo_exists():self.status.set(str(exc));messagebox.showerror(self.title(),str(exc),parent=self)
        def run():return worker(progress,self.app.lanes.cancel_event(self.lane))
        self.status.set('后台处理中，可继续编辑和查看日志。')
        self.app.lanes.submit(self.lane,run,finished,on_error=failed)
    def table(self, columns):
        area=ttk.Frame(self);area.pack(fill='both',expand=True,padx=8,pady=5)
        tree=PagedTree(area,columns=[c[0] for c in columns],show='headings',height=12)
        for key,label,width in columns:tree.heading(key,text=label);tree.column(key,width=width,minwidth=50)
        y=ttk.Scrollbar(area,orient='vertical',command=tree.yview);x=ttk.Scrollbar(area,orient='horizontal',command=tree.xview)
        tree.configure(yscrollcommand=y.set,xscrollcommand=x.set);tree.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns');x.grid(row=1,column=0,sticky='ew')
        tree.controls(area).grid(row=2,column=0,columnspan=2,sticky='ew');area.rowconfigure(0,weight=1);area.columnconfigure(0,weight=1)
        return tree

class ScriptToolsMixin:
    def add_tools(self, parent):
        bar=ttk.Frame(parent.master);bar.pack(fill='x',before=parent,padx=8)
        for label,fn in [('导出文案',self.export_scripts),('管理配音',self.manage_external),('回收站',self.open_trash)]:self._button(bar,label,fn)
        self.protocol('WM_DELETE_WINDOW',self.close_with_draft)
    def resolve_draft(self):
        from script_library import normalize_text
        if self._text()==normalize_text(self.original_text):return True
        choice=messagebox.askyesnocancel('未保存正文','是：保存后继续\n否：暂不保存，使用已保存版本\n取消：返回编辑',parent=self)
        if choice is None:return False
        if choice:
            if self.selected_id:self.edit()
            else:self.add()
        return True
    def close_with_draft(self):
        if self.resolve_draft():self.destroy()
    def manage_external(self):
        if not self.resolve_draft():return
        ids=self.tree.selection();sid=ids[0] if len(ids)==1 else None
        ExternalWindow(self,sid)
    def open_trash(self):TrashWindow(self)
    def delete(self):
        ids=list(self._selected_many())
        for sid in ids:self.library._check_profile(self.library.get(sid),self.active_profile)
        recycle_dialog(self,ids,[],lambda:self._after_delete())
    def _after_delete(self):self._set_editor(None,'');self.refresh();self.status.set('已回收文案及关联配音，详情可在回收站查看。')
    def export_scripts(self):
        ExportWindow(self)

class ExportWindow(ToolsWindow):
    def __init__(self, owner):
        super().__init__(owner,'导出文案');self.geometry('780x490')
        self.selected=list(owner.tree.selection());self.profile=owner.active_profile;self.spec=owner.active_spec
        self.scope=tk.StringVar(value='已选文案' if self.selected else '当前筛选结果');self.format=tk.StringVar(value='独立 TXT')
        self.include=tk.BooleanVar(value=False);self.cross=tk.BooleanVar(value=False);self.zipped=tk.BooleanVar(value=False)
        self.directory=tk.StringVar(value=str(self.library.path.parent/'script_exports'))
        panel=ttk.Frame(self);panel.pack(fill='both',expand=True,padx=15,pady=12)
        ttk.Label(panel,text=f'当前文案库：{self.library.catalog.categories()[self.profile]} / {self.library.catalog.get(self.profile,self.spec)['name']}；跨页已选 {len(self.selected)} 篇。').pack(anchor='w',pady=7)
        for label,var,opts in [('范围',self.scope,('已选文案','当前筛选结果','当前文案库全部')),('格式',self.format,('独立 TXT','TXT 合集','CSV','XLSX'))]:
            bar=ttk.Frame(panel);bar.pack(fill='x',pady=6);ttk.Label(bar,text=label).pack(side='left');ttk.Combobox(bar,textvariable=var,values=opts,state='readonly',width=26).pack(side='left',padx=12)
        for label,var in [('包含停用文案（明确已选的停用文案始终保留）',self.include),('显式跨库导出',self.cross),('同时打包本次 ZIP',self.zipped)]:ttk.Checkbutton(panel,text=label,variable=var).pack(anchor='w',pady=4)
        ttk.Entry(panel,textvariable=self.directory).pack(fill='x',pady=5);self.button(panel,'选择保存目录',self.browse)
        ttk.Label(panel,text='每次创建新批次目录。表格同时保留无损 TXT 和正文版本清单，不覆盖旧文件。',wraplength=720).pack(fill='x',pady=8)
        self.button(panel,'确认范围并导出',self.start)
    def browse(self):
        p=filedialog.askdirectory(parent=self,initialdir=self.directory.get())
        if p:self.directory.set(p)
    def start(self):
        from script_export import snapshot,export
        rows=snapshot(self.library,scope=self.scope.get(),selected=self.selected,profile_id=self.profile,query=self.owner.query.get(),status=self.owner.status_filter.get(),include_disabled=self.include.get(),cross_library=self.cross.get(),spec_id=self.spec)
        if self.owner.selected_id in [r['script_id'] for r in rows]:
            if not self.owner.resolve_draft():return
            rows=snapshot(self.library,scope=self.scope.get(),selected=self.selected,profile_id=self.profile,query=self.owner.query.get(),status=self.owner.status_filter.get(),include_disabled=self.include.get(),cross_library=self.cross.get(),spec_id=self.spec)
        counts={NAMES[k]:sum(r['profile_id']==k for r in rows) for k in NAMES}
        scope=self.scope.get();fmt=self.format.get();directory=self.directory.get();zipped=self.zipped.get()
        if not messagebox.askokcancel('确认导出',f'{scope}：{len(rows)}篇（停用{sum(not r["enabled"] for r in rows)}篇）\n各库：{counts}\n格式：{fmt}\n目录：{directory}',parent=self):return
        self.background(lambda progress,cancel:export(rows,directory,fmt,scope=scope,zip_output=zipped,progress=progress,cancel=cancel),lambda r:self.status.set(f'完成{r["manifest"]["success"]}，跳过{r["manifest"]["skipped"]}，失败{r["manifest"]["failed"]}：{r["directory"]}'))

class ExternalWindow(ToolsWindow):
    def __init__(self, owner, script_id=None):
        super().__init__(owner,'外部配音 · 每篇文案可绑定多个声音');self.script_id=script_id;self.profile=owner.active_profile;self.spec=owner.active_spec
        title=f'当前文案：{script_id}' if script_id else f'当前库：{self.library.catalog.categories()[self.profile]} / {self.library.catalog.get(self.profile,self.spec)['name']}（多篇批量绑定）'
        ttk.Label(self,text=title+'；均衡/随机在主界面选择，指定版本在这里设置。').pack(fill='x',padx=10,pady=7)
        bar=ttk.Frame(self);bar.pack(fill='x',padx=6)
        for label,fn in [('添加/批量导入',self.import_files),('试听',self.play),('命名',self.rename),('启用',lambda:self.change('enable')),('停用',lambda:self.change('disable')),('替换',lambda:self.replace(False)),('重新定位',lambda:self.replace(True))]:self.button(bar,label,fn)
        bar2=ttk.Frame(self);bar2.pack(fill='x',padx=6)
        self.button(bar2,'删除所选声音',self.delete);self.button(bar2,'刷新',self.refresh);self.button(bar2,'查看/重试失败操作',self.operations)
        self.button(bar2,'导入配音目录',self.import_directory)
        self.button(bar2,'声学复核所选版本',self.recheck)
        bar3=ttk.Frame(self);bar3.pack(fill='x',padx=6)
        self.button(bar3,'人工确认对应当前正文',lambda:self.change('confirm'));self.button(bar3,'设为本篇指定',lambda:self.change('designate'))
        self.button(bar3,'备注（可选）',self.note)
        self.button(bar3,'按文案ID一键配对配音库',self.pair_library)
        self.tree=self.table([('script','文案',90),('name','名称',160),('duration','时长',75),('state','启用/复核/文件',250),('count','成功/预留',85),('last','最近使用',160),('path','文件',440),('note','备注',180)])
        self.refresh()
    def refresh(self):
        selected=self.tree.selection();self.tree.delete(*self.tree.get_children());self.rows={}
        for r in self.library.external.versions(self.script_id):
            if self.library.get(r['script_id'])['profile_id']!=self.profile or self.library.get(r['script_id'])['spec_id']!=self.spec:continue
            self.rows[r['id']]=r
            review='人工已确认' if r.get('human_review') else '已确认' if r['review']=='confirmed' and r['bound_hash']==r['current_hash'] else '内容不一致' if r['review']=='mismatch' else '需复核'
            state=('启用' if r['enabled'] else '停用')+' / '+review+' / '+('缺失' if r['missing'] else '存在')
            self.tree.insert('','end',iid=r['id'],values=(r['display_id'],r['name'],f'{r["duration"]:.3f}s',state,f'{r["usage_count"]}/{r["reserved_count"]}',r['last_used'] or '—',r['path'],r['note']))
        self.tree.selection_set([v for v in selected if v in self.rows]);self.owner.refresh()
    def ids(self):
        ids=list(self.tree.selection())
        if not ids:raise error('请先选择配音版本。')
        return ids
    def single(self):
        ids=self.ids()
        if len(ids)!=1:raise error('此操作请选择一个声音。')
        return ids[0]
    def play(self):
        from core import open_local
        open_local(self.rows[self.single()]['path'])
    def rename(self):
        vid=self.single();name=simpledialog.askstring('命名','配音名称',initialvalue=self.rows[vid]['name'],parent=self)
        if name:self.library.external.change([vid],'rename',name);self.refresh()
    def note(self):
        vid=self.single();value=simpledialog.askstring('可选备注','平台或其他备注（不参与自动选择）',initialvalue=self.rows[vid]['note'] or '',parent=self)
        if value is not None:self.library.external.change([vid],'note',value);self.refresh()
    def change(self,action):
        ids=self.ids()
        if action=='designate':ids=[self.single()]
        # Clicking this action is the user's explicit manual decision.
        self.library.external.change(ids,action);self.refresh()
    def tools(self):
        from core import resolve_tool
        return resolve_tool('ffmpeg',self.app.cfg.get('ffmpeg','')),resolve_tool('ffprobe',self.app.cfg.get('ffprobe',''))
    def import_files(self):
        paths=filedialog.askopenfilenames(parent=self,title='选择多份外部配音（原件保留）',initialdir=_portable_path(r'D:\DY video'),filetypes=[('音频/含音轨视频','*.wav *.mp3 *.m4a *.flac *.aac *.mp4'),('所有文件','*.*')])
        if not paths:return
        ffmpeg,probe=self.tools();sid=self.script_id;profile=self.profile
        self.background(lambda progress,cancel:self.library.external.preview(paths,ffmpeg,probe,script_id=sid,profile_id=profile,spec_id=self.spec,progress=progress,cancel=cancel),lambda rows:ImportWindow(self,rows))
    def import_directory(self):
        from external_voice_catalog import library_audio_files
        directory=filedialog.askdirectory(parent=self,title='选择配音库或文案ID目录',initialdir=_portable_path(r'D:\DY video\配音库'))
        if not directory:return
        ffmpeg,probe=self.tools();profile=self.profile
        def worker(progress,cancel):
            paths=library_audio_files(directory)
            if not paths:raise error('未找到“文案ID+音色”的音频文件。')
            return self.library.external.preview(paths,ffmpeg,probe,profile_id=profile,spec_id=self.spec,progress=progress,cancel=cancel)
        self.background(worker,lambda rows:ImportWindow(self,rows))
    def pair_library(self):
        directory=self.app.cfg.get('external_voice_library_root') or _portable_path(r'D:\DY video\配音库')
        if not Path(directory).is_dir():
            directory=filedialog.askdirectory(parent=self,title='选择包含文案ID文件夹的配音库',initialdir=_portable_path(r'D:\DY video'))
            if not directory:return
        ffmpeg,probe=self.tools();profile=self.profile
        self.status.set('按当前库已保存文案ID匹配同名目录，已有绑定跳过；新声音待复核后可组合。')
        def finish(result):
            self.last_pair_result=result;self.refresh()
            self.status.set(f'ID配对完成：匹配{result["matched_scripts"]}/{result["script_count"]}篇，新增{result["bound"]}，已有跳过{result["duplicate"]}，失败{result["failed"]}，目录/ID问题{len(result["issues"])}。新声音请复核；'+('已取消，已完成绑定保留，可再次续配。' if result['cancelled'] else ''))
            if result['failed'] or result['issues']:
                messagebox.showwarning('部分音频未配对',str(result['issues']+[dict(path=r['path'],error=r.get('error')) for r in result['rows'] if r['status']=='failed'])[:2500],parent=self)
        self.background(lambda progress,cancel:self.library.external.pair_by_id(directory,profile,ffmpeg,probe,spec_id=self.spec,progress=progress,cancel=cancel),finish)
    def replace(self,relink):
        vid=self.single();p=filedialog.askopenfilename(parent=self,title='选择新音频' if not relink else '定位原文件',initialdir=_portable_path(r'D:\DY video'))
        if not p:return
        ffmpeg,probe=self.tools()
        self.background(lambda progress,cancel:self.library.external.preview([p],ffmpeg,probe,script_id=self.rows[vid]['script_id']),lambda rows:ImportWindow(self,rows,replace_id=vid,relink=relink))
    def delete(self):recycle_dialog(self,[],self.ids(),self.refresh)
    def operations(self):OperationWindow(self)
    def recheck(self):
        ids=self.ids();ffmpeg,_=self.tools()
        def worker(progress,cancel):
            result=[]
            for i,vid in enumerate(ids):
                if cancel and cancel.is_set():break
                try:result.append(self.library.external.recheck(vid,ffmpeg))
                except Exception as exc:result.append(dict(version_id=vid,error=str(exc)))
                progress(f'声学复核 {i+1}/{len(ids)}')
            return result
        self.background(worker,lambda results:(self.refresh(),self.status.set(str(results))))

class ImportWindow(ToolsWindow):
    def __init__(self, owner, rows, replace_id=None, relink=False):
        super().__init__(owner,'导入预览 · 分配文案后一次确认');self.rows=rows;self.replace_id=replace_id;self.relink=relink
        self.confirmed=tk.BooleanVar(value=False);self.linked=tk.BooleanVar(value=relink);self.exported=None
        self.status.set('任意文件名可以手动分配。重复内容保留原状态，不会自动启用。多音轨必须先选择音轨。')
        bar=ttk.Frame(self);bar.pack(fill='x',padx=8)
        choices=self.library.list(profile_id=owner.profile,spec_id=owner.spec);self.choices={r['script_id']+' · '+r['raw_text'][:38]:r['script_id'] for r in choices}
        self.assignment=tk.StringVar(value=next(iter(self.choices),''))
        ttk.Combobox(bar,textvariable=self.assignment,values=list(self.choices),state='readonly',width=60).pack(side='left')
        self.button(bar,'分配给所选文件',self.assign)
        self.button(bar,'关联导出清单（可选）',self.manifest)
        self.tree=self.table([('path','源文件',410),('script','文案',100),('class','分类',95),('state','详情',400)])
        options=ttk.Frame(self);options.pack(fill='x',padx=8)
        ttk.Checkbutton(options,text='已核对本批声音确实对应所分配的文案',variable=self.confirmed).pack(anchor='w')
        ttk.Checkbutton(options,text='高级：仅关联原文件（默认复制托管）',variable=self.linked).pack(anchor='w')
        self.button(options,'为所选文件选择音轨',self.track)
        self.button(options,'确认导入有效已分配项',self.start)
        self.refresh()
    def refresh(self):
        selected=self.tree.selection();self.tree.delete(*self.tree.get_children())
        counts={k:sum(r['classification']==k for r in self.rows) for k in ('新绑定','重复','待分配','失效')}
        for i,r in enumerate(self.rows):self.tree.insert('','end',iid=str(i),values=(r['path'],r.get('script_id') or '待分配',r['classification'],r.get('error') or ('请选择音轨' if r['status']=='track_choice' else '重复：'+r.get('duplicate_state','') if r.get('duplicate_id') else '已校验')))
        self.tree.selection_set(selected);self.title('导入预览：'+str(counts))
    def assign(self):
        sid=self.choices.get(self.assignment.get())
        if not sid:raise error('当前库没有可分配文案。')
        for idx in self.tree.selection():
            r=self.rows[int(idx)];r['script_id']=sid
            if r['status']!='invalid':r['classification']='新绑定';r.pop('duplicate_id',None)
        self.refresh()
    def track(self):
        ids=self.tree.selection()
        if len(ids)!=1:raise error('请选择一个多音轨文件。')
        i=int(ids[0]);r=self.rows[i]
        if r['status']!='track_choice':raise error('该文件不需要音轨选择。')
        tracks=r['tracks'];details='\n'.join(f'{t["index"]}：{t.get("codec_name")} {t.get("tags",{})}' for t in tracks)
        stream=simpledialog.askinteger('选择音轨',details+'\n输入音轨编号',parent=self)
        if stream is None:return
        ffmpeg,probe=self.owner.tools();p=r['path'];sid=r.get('script_id')
        def done(new):new.update(script_id=sid,classification='新绑定' if sid else '待分配');self.rows[i]=new;self.refresh()
        self.background(lambda progress,cancel:self.library.external.probe(p,ffmpeg,probe,stream=stream),done)
    def manifest(self):
        p=filedialog.askopenfilename(parent=self,title='选择 export_manifest.json',initialdir=str(self.library.path.parent/'script_exports'),filetypes=[('清单','*.json')])
        if p:
            data=json.loads(Path(p).read_text(encoding='utf-8'));self.exported={r['internal_id']:r for r in data['records']};self.status.set('已关联导出清单，正文版本差异会标记需复核。')
    def start(self):
        rows=[dict(r) for r in self.rows if r.get('script_id') and r['status']=='valid'];skipped=len(self.rows)-len(rows)
        if not rows:raise error('没有有效且已分配的文件。')
        if not messagebox.askokcancel('确认本批绑定',f'处理{len(rows)}份，待分配/失效/未选音轨{skipped}份不处理。\n重复内容不会新增版本，原件保留。',parent=self):return
        managed=not self.linked.get();confirmed=self.confirmed.get();manifest=self.exported;vid=self.replace_id;relink=self.relink
        def worker(progress,cancel):
            result=[]
            for i,r in enumerate(rows):
                if cancel and cancel.is_set():break
                try:
                    target=self.library.get(r['script_id']);self.library._check_profile(target,self.owner.profile,self.owner.spec)
                    exported=(manifest or {}).get(target['script_uid'])
                    value=self.library.external.replace(vid,r,relink=relink,confirmed=confirmed) if vid else self.library.external.bind(r,managed=managed,confirmed=confirmed,exported=exported,expected_script=target)
                    if confirmed and (vid or value.get('status')=='bound'):
                        self.library.external.change([vid or value['version_id']],'confirm')
                    result.append(dict(path=r['path'],result=value))
                except Exception as exc:result.append(dict(path=r['path'],error=str(exc)))
                progress(f'导入 {i+1}/{len(rows)}')
            return result
        def done(result):
            failures=[r for r in result if 'error' in r];self.owner.refresh();self.status.set(f'已处理{len(result)}份；失败{len(failures)}份；未处理{len(rows)-len(result)+skipped}份。'+(' '+str(failures) if failures else ''))
        self.background(worker,done)

def recycle_dialog(owner,script_ids,version_ids,done):
    preview=owner.library.external.delete_preview(script_ids,version_ids)
    window=ToolsWindow(owner,'确认联动回收');window.geometry('850x560')
    originals=tk.BooleanVar(value=False);resources=preview['resources']
    summary=f'文案{len(preview["scripts"])}篇，配音{len(preview["versions"])}版本，唯一资源{len(resources)}份。\n可回收托管{sum(r["mode"]=="managed" and not r["protected"] for r in resources)}份；引用保护{sum(bool(r["protected"]) for r in resources)}份。\n冻结任务和历史引用保留；磁盘回收不等于已释放空间。'
    ttk.Label(window,text=summary,wraplength=810).pack(fill='x',padx=12,pady=10)
    paths=tk.Text(window,height=12,wrap='word');paths.pack(fill='both',expand=True,padx=12,pady=5)
    paths.insert('1.0','高级原件范围（仅以下真实绑定路径，无通配删除）：\n'+'\n'.join((r['source_path'] or r['path'])+' '+r['protected'] for r in resources));paths.configure(state='disabled')
    ttk.Checkbutton(window,text='高级：连上述外部原件也移入可恢复回收区（被引用的文件仍保留）',variable=originals).pack(anchor='w',padx=12,pady=8)
    def start():
        remove=originals.get()
        def finish(result):done();window.status.set(f'回收操作 {result["operation_id"]}：{result["state"]}，失败{len(result["errors"])}项；可在回收站重试或恢复。')
        window.background(lambda progress,cancel:owner.library.external.recycle(script_ids,version_ids,delete_originals=remove),finish)
    window.button(window,'确认回收全部所选关系',start)

class TrashWindow(ToolsWindow):
    def __init__(self,owner):
        super().__init__(owner,'回收站 · 不自动永久清空')
        bar=ttk.Frame(self);bar.pack(fill='x',padx=8)
        for label,fn in [('恢复所选',self.restore),('重试回收失败项',self.retry),('永久清理所选',self.purge),('清空可清理项',lambda:self.purge(True)),('刷新',self.refresh)]:self.button(bar,label,fn)
        self.tree=self.table([('date','删除时间',180),('scripts','文案/预览',280),('versions','配音版本',80),('files','已回收/暂留',110),('bytes','登记大小',90),('state','操作状态/失败',280)])
        self.refresh()
    def refresh(self):
        self.rows={r['id']:r for r in self.library.external.trash()};self.tree.delete(*self.tree.get_children())
        for op,r in self.rows.items():
            p=r['payload'];files=p.get('files',[])
            self.tree.insert('','end',iid=op,values=(r['created'],'; '.join(s['script_id']+':'+s['raw_text'][:25] for s in p['scripts']) or '单独声音',len(p['versions']),f'{sum(f["state"]=="moved" for f in files)}/{sum(bool(x["protected"]) for x in p["resources"])}',sum(x['bytes'] or 0 for x in p['resources']),r['state']+' '+r['error']))
    def run(self,action,all_items=False):
        ids=list(self.rows) if all_items else list(self.tree.selection())
        if not ids:raise error('请选择回收项。')
        if action=='purge' and not messagebox.askyesno('永久清理',f'永久清理{len(ids)}组？仍有任务、其他绑定或历史引用的组会跳过。此操作不可恢复。',parent=self):return
        def worker(progress,cancel):
            results=[]
            for i,op in enumerate(ids):
                if cancel and cancel.is_set():break
                try:result=self.library.external.purge(op) if action=='purge' else self.library.external.recover(op,restore=action=='restore');results.append((op,result))
                except Exception as exc:results.append((op,dict(error=str(exc))))
                progress(f'处理回收组 {i+1}/{len(ids)}')
            return results
        def done(results):self.refresh();self.owner.refresh();self.status.set(str(results))
        self.background(worker,done)
    def restore(self):self.run('restore')
    def retry(self):self.run('retry')
    def purge(self,all_items=False):self.run('purge',all_items)

class OperationWindow(ToolsWindow):
    def __init__(self,owner):
        super().__init__(owner,'未完成外部配音操作');self.tree=self.table([('kind','类型',90),('date','时间',180),('state','状态',100),('error','原因',620)])
        self.button(self,'重试所选导入/归类',self.retry);self.refresh()
    def refresh(self):
        with self.library._connect() as db:self.rows={r['id']:dict(r) for r in db.execute("SELECT * FROM external_operations WHERE state IN ('pending','failed','partial','restoring')")}
        self.tree.delete(*self.tree.get_children())
        for op,r in self.rows.items():self.tree.insert('','end',iid=op,values=(r['kind'],r['created'],r['state'],r['error']))
    def retry(self):
        ids=list(self.tree.selection());ffmpeg,probe=self.owner.tools()
        def worker(progress,cancel):
            results=[]
            for op in ids:
                r=self.rows[op]
                if r['kind']=='organize':
                    try:
                        payload=json.loads(r['payload'])
                        result=self.library.external.organize_library(payload['root'],resume_op=op);results.append((op,result))
                    except Exception as exc:results.append((op,str(exc)))
                    continue
                if r['kind']!='import':results.append((op,'回收/恢复请到回收站重试；替换请重新选择文件'));continue
                try:
                    result=self.library.external.retry_import(op,ffmpeg,probe);results.append((op,result))
                except Exception as exc:results.append((op,str(exc)))
            return results
        self.background(worker,lambda r:(self.refresh(),self.owner.refresh(),self.status.set(str(r))))
