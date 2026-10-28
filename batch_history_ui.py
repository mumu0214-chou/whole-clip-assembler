"""Open unfinished batches by time without navigating raw manifest files."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from batch_history import catalog
from paged_tree import PagedTree


class HistoryWindow(tk.Toplevel):
    def __new__(cls,app,on_load=None):
        if getattr(app,'_qt_parent',None):return app._qt_parent.editors.open('history',on_load=on_load or app.open_batch_path)
        return super().__new__(cls)
    def __init__(self, app, on_load=None):
        super().__init__(app);self.app=app;self.on_load=on_load or app.open_batch_path
        self.title('历史组合 · 未完成批次');self.geometry('1100x540');self.minsize(780,390)
        ttk.Label(self,text='按创建时间倒序。普通方案未完成历史保留72小时，查看不续期；SK有效待处理任务保留至取消或替换。已生成成片请到输出目录查看。',wraplength=1050).pack(anchor='w',padx=12,pady=10)
        columns=('time','profile','unfinished','failed','last','expires')
        self.table=PagedTree(self,columns=columns,show='headings',selectmode='browse',height=14)
        for key,label,width in zip(columns,('创建时间','品类','待生成','失败/未组合','最近开始/继续','保留到'),(170,150,65,95,170,170)):
            self.table.heading(key,text=label);self.table.column(key,width=width)
        self.table.pack(fill='both',expand=True,padx=12)
        self.table.controls(self)
        self.table.bind('<Double-1>',lambda _:self.open())
        bar=ttk.Frame(self);bar.pack(fill='x',padx=12,pady=10)
        ttk.Button(bar,text='打开所选批次',command=self.open).pack(side='left',padx=3)
        ttk.Button(bar,text='刷新',command=self.refresh).pack(side='left',padx=3)
        ttk.Button(bar,text='从文件打开…',command=self.from_file).pack(side='left',padx=3)
        self.note=tk.StringVar();ttk.Label(bar,textvariable=self.note).pack(side='left',padx=15)
        self.refresh()

    def refresh(self):
        self.rows={r['id']:r for r in catalog(self.app.store)}
        self.table.delete(*self.table.get_children())
        for key,r in self.rows.items():self.table.insert('','end',iid=key,values=(r['created_at'],r['profile'],r['unfinished'],r['failed'],r['last_started_at'],r['expires_at']))
        self.note.set(f'{len(self.rows)}个未完成批次' if self.rows else '没有保留期内的未完成批次')

    def open(self):
        selected=self.table.selection()
        if not selected:return
        self.load(self.rows[selected[0]]['path'])

    def from_file(self):
        p=filedialog.askopenfilename(title='打开批次清单',initialdir=str(self.app.data_dir/'batches'),filetypes=[('批次清单','batch_manifest.json')],parent=self)
        if p:self.load(p)

    def load(self,path):
        try:
            self.on_load(Path(path));self.destroy()
        except Exception as exc:
            messagebox.showerror('历史批次无法打开',str(exc),parent=self);self.refresh()
