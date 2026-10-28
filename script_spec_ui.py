"""Specification management inside the isolated existing script editor."""
import tkinter as tk
from tkinter import ttk,simpledialog,messagebox


class ScriptSpecControls:
    def require_spec_workbench(self):
        from product_catalog import REVISION
        if self.app.cfg.get('product_spec_revision')!=REVISION:
            raise ValueError('当前工作台尚未加载规格筛选；请在正在运行的任务结束后正常重开工作台，再使用新增/切换规格，避免旧版混选文案。')

    def build_script_spec_controls(self):
        bar=ttk.Frame(self);bar.pack(fill='x',padx=10,pady=4)
        ttk.Label(bar,text='文案规格').pack(side='left')
        self.spec_choice=tk.StringVar();self.spec_box=ttk.Combobox(bar,textvariable=self.spec_choice,state='readonly',width=25)
        self.spec_box.pack(side='left',padx=4);self.spec_box.bind('<<ComboboxSelected>>',lambda _:self._guard(self.spec_changed))
        for text,fn in [('新增规格',self.new_spec),('规格改名',self.rename_spec),('品类改名',self.rename_script_category)]:
            self._button(bar,text,fn)
        self.target_spec=tk.StringVar();self.target_spec_box=ttk.Combobox(bar,textvariable=self.target_spec,state='readonly',width=23)
        self.target_spec_box.pack(side='left',padx=4);self._button(bar,'选中文案移到规格',self.move_to_spec)
        self.refresh_spec_choices()

    def refresh_spec_choices(self):
        self.spec_rows=self.library.catalog.specs(self.active_profile)
        self.spec_map={r['name']:r['spec_id'] for r in self.spec_rows}
        self.spec_box.configure(values=list(self.spec_map));self.target_spec_box.configure(values=list(self.spec_map))
        self.spec_choice.set(next(r['name'] for r in self.spec_rows if r['spec_id']==self.active_spec))
        if self.target_spec.get() not in self.spec_map:self.target_spec.set(self.spec_choice.get())

    def spec_changed(self):self.show_spec(self.spec_map[self.spec_choice.get()])

    def show_spec(self,key):
        if key==self.active_spec:return
        self.require_spec_workbench()
        self.library.catalog.get(self.active_profile,key)
        if not self.resolve_draft():self.refresh_spec_choices();return
        self._remember_draft();self.active_spec=key
        draft=self.drafts.get((self.active_profile,key),{})
        self._set_editor(draft.get('selected_id'),draft.get('text',''),draft.get('original',''))
        self.query.set('');self.tree.clear_selection();self.refresh_spec_choices();self.refresh()

    def catalog_changed(self):
        self.profile_names=self.library.catalog.categories();self.script_names={k:v+'文案' for k,v in self.profile_names.items()}
        for key,tab in self.category_tabs.items():self.categories.tab(tab,text=self.script_names[key])
        self.refresh_spec_choices();self._update_notes()
        if hasattr(self.app,'emit'):self.app.emit('catalog_changed')
        elif hasattr(self.app,'refresh_catalog_controls'):self.app.refresh_catalog_controls()

    def new_spec(self):
        self.require_spec_workbench()
        name=simpledialog.askstring('新增规格','例如19.9四包牛肉丸；新规格不会自动复制旧价格文案',parent=self)
        if name is None:return
        key=self.library.catalog.add_spec(self.active_profile,name);self.catalog_changed();self.show_spec(key)

    def rename_spec(self):
        name=simpledialog.askstring('规格改名','新规格名称',initialvalue=self.spec_choice.get(),parent=self)
        if name is not None:self.library.catalog.rename_spec(self.active_profile,self.active_spec,name);self.catalog_changed()

    def rename_script_category(self):
        name=simpledialog.askstring('品类改名','新名称（不会改变剪辑规则或历史文案ID）',initialvalue=self.profile_names[self.active_profile],parent=self)
        if name is not None:self.library.catalog.rename_category(self.active_profile,name);self.catalog_changed()

    def move_to_spec(self):
        self.require_spec_workbench()
        if not self.resolve_draft():return
        ids=self._selected_many();target=self.spec_map.get(self.target_spec.get())
        if target is None:raise ValueError('请选择目标规格')
        if not messagebox.askyesno('移动文案规格',f'将{len(ids)}篇文案及关联配音移到“{self.target_spec.get()}”？\n原文、ID、配音和使用次数保留；请先核对价格适用。',parent=self):return
        self.library.move_spec(ids,self.active_profile,target,expected_spec=self.active_spec)
        self._set_editor(None,'');self.refresh();self.status.set('已移动文案及关联配音，使用历史保留。')
