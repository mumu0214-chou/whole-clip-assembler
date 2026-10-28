"""Catalog controls on the existing main window, without Tk popups in Qt."""
from tkinter import ttk,simpledialog


def ask_name(app,title,initial=''):
    parent=getattr(app,'_qt_parent',None)
    if parent:
        from qt_workbench import W
        text,ok=W.QInputDialog.getText(parent,title,'名称：',text=initial)
        return text if ok else None
    return simpledialog.askstring(title,'名称：',initialvalue=initial,parent=app)


class CatalogControls:
    def catalog_names(self):return self.script_library.catalog.categories()

    def build_spec_controls(self,parent,category_box):
        import tkinter as tk
        self.category_selector=category_box
        self.category_selector.configure(values=list(self.catalog_names().values()))
        self.profile_name.set(self.catalog_names()[self.cfg['editing_profile_id']])
        row=ttk.Frame(parent);row.pack(fill='x',padx=5,pady=5)
        ttk.Button(row,text='修改品类名称',command=lambda:self.guard(self.rename_category)).pack(side='left',padx=3)
        ttk.Label(row,text='当前规格').pack(side='left',padx=6)
        self.product_spec_var=tk.StringVar()
        self.product_spec_selector=ttk.Combobox(row,textvariable=self.product_spec_var,state='readonly',width=24)
        self.product_spec_selector.pack(side='left',padx=3)
        self.product_spec_selector.bind('<<ComboboxSelected>>',lambda _:self.guard(self.switch_product_spec))
        for label,fn in [('新增规格',self.add_product_spec),('修改规格名称',self.rename_product_spec)]:
            ttk.Button(row,text=label,command=lambda f=fn:self.guard(f)).pack(side='left',padx=3)
        ttk.Label(row,text='文案和配音仅使用当前规格；素材及剪辑规则按品类共用。').pack(side='left',padx=6)
        self.refresh_catalog_controls()

    def refresh_catalog_controls(self):
        if not hasattr(self,'product_spec_var'):return
        profile=self.cfg['editing_profile_id'];names=self.catalog_names()
        self.category_selector.configure(values=list(names.values()));self.profile_name.set(names[profile])
        rows=self.script_library.catalog.specs(profile);self.product_spec_choices={r['name']:r['spec_id'] for r in rows}
        selected=self.cfg.get('product_spec_id','default')
        self.product_spec_selector.configure(values=list(self.product_spec_choices))
        self.product_spec_var.set(next((r['name'] for r in rows if r['spec_id']==selected),rows[0]['name']))
        if hasattr(self,'script_scope_var'):self.script_scope_var.set(names[profile]+' / '+self.product_spec_var.get())
        for key,button in getattr(self,'script_category_buttons',{}).items():button.configure(text=names[key]+'文案')

    def selected_spec(self):
        key=self.product_spec_choices.get(self.product_spec_var.get())
        if not key:raise ValueError('请选择有效规格')
        return key

    def rename_category(self):
        profile=self.cfg['editing_profile_id'];name=ask_name(self,'修改品类名称',self.catalog_names()[profile])
        if name is None:return
        self.script_library.catalog.rename_category(profile,name);self.refresh_catalog_controls()
        self.cfg=self.collect_config();self.persist_config(self.cfg)

    def add_product_spec(self):
        name=ask_name(self,'新增规格（例如19.9四包牛肉丸）')
        if name is None:return
        key=self.script_library.catalog.add_spec(self.cfg['editing_profile_id'],name)
        self.cfg['product_spec_id']=key;self.refresh_catalog_controls();self.switch_product_spec()

    def rename_product_spec(self):
        name=ask_name(self,'修改规格名称',self.product_spec_var.get())
        if name is None:return
        self.script_library.catalog.rename_spec(self.cfg['editing_profile_id'],self.selected_spec(),name)
        self.refresh_catalog_controls();self.cfg=self.collect_config();self.persist_config(self.cfg)

    def switch_product_spec(self):
        self.cfg=self.script_library.catalog.freeze(self.cfg,self.selected_spec())
        self.cfg=self.collect_config();self.persist_config(self.cfg)
        self.script_scope_var.set(self.cfg['editing_profile_name']+' / '+self.cfg['product_spec_name'])
        self.log('已选择规格：'+self.cfg['product_spec_name']+'；新方案只选此规格文案和配音，正在剪辑的任务保持。')
