"""Explicit paged packaging membership over the existing relative-path list."""
import tkinter as tk
from tkinter import ttk
from grouped_materials import package_paths
from grouped_review_ui import GroupedReviewWindow


class PackagingListWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.clips = list(app.clips)
        self.root = app.cfg['material_root']
        self.listed = {p.strip() for p in app.package_editor.get('1.0','end-1c').splitlines() if p.strip()}
        self.status = tk.StringVar(self, '')
        self.title('包装白名单 · 增加 / 移除')
        self.geometry('1120x680'); self.minsize(980, 540)
        ttk.Label(self, text='只添加已经核对确实是包装画面的素材。名单负责数量口播匹配，与安全裁切许可分别保存。',
                  wraplength=1060).pack(fill='x', padx=10, pady=8)
        tabs = ttk.Notebook(self); tabs.pack(fill='both', expand=True, padx=10)
        candidates = ttk.Frame(tabs); listed = ttk.Frame(tabs)
        tabs.add(candidates, text='可选素材'); tabs.add(listed, text='当前包装名单')
        self.candidates = GroupedReviewWindow.table(self, candidates,
            [('path','相对路径',520),('review','素材状态',220),('listed','已在名单',110)])
        self.members = GroupedReviewWindow.table(self, listed,
            [('path','相对路径',600),('available','状态',220)])
        buttons = ttk.Frame(self); buttons.pack(fill='x', padx=10, pady=6)
        for label, action in [('添加所选素材',self.add),('移除所选名单',self.remove),('检查并保存名单',self.save)]:
            ttk.Button(buttons,text=label,command=lambda action=action:self.guard(action)).pack(side='left',padx=4)
        ttk.Label(self,textvariable=self.status,wraplength=1060).pack(fill='x',padx=12,pady=7)
        self.refresh()

    def guard(self, action):
        GroupedReviewWindow.guard(self, action)

    def refresh(self):
        self.candidates.delete(*self.candidates.get_children())
        for c in self.clips:
            self.candidates.insert('','end',iid=c['relative_path'],values=(c['relative_path'],
                '已启用且复核' if c['enabled'] and c['reviewed'] else '需启用 / 整段复核',
                '是' if c['relative_path'] in self.listed else '否'))
        self.members.delete(*self.members.get_children())
        by_path={c['relative_path']:c for c in self.clips}
        for p in sorted(self.listed):
            c=by_path.get(p)
            self.members.insert('','end',iid=p,values=(p,'有效' if c and c['enabled'] and c['reviewed'] else '需重新扫描 / 启用 / 复核'))
        self.status.set(f'名单{len(self.listed)}条；增删暂存，点“检查并保存名单”生效。')

    def add(self):
        selected=set(self.candidates.selection())
        if not selected:
            raise ValueError('请在“可选素材”页选择镜头')
        cfg=dict(self.app.cfg,material_root=self.root,package_count_clips=sorted(selected))
        package_paths(cfg,self.clips)
        self.listed.update(selected);self.refresh()

    def remove(self):
        self.listed.difference_update(self.members.selection());self.refresh()

    def save(self):
        if self.app.cfg['material_root']!=self.root:
            raise ValueError('素材根目录已切换，请关闭本窗口并重新打开')
        cfg=self.app.collect_config()
        cfg['package_count_clips']=sorted(self.listed)
        package_paths(cfg,self.app.clips)
        self.app.persist_config(cfg)
        self.app.package_editor.delete('1.0','end')
        self.app.package_editor.insert('1.0','\n'.join(cfg['package_count_clips']))
        self.status.set(f'已保存{len(self.listed)}条包装名单；新方案使用，已冻结批次保持。')
