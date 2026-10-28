"""Editors for explicit SK path preferences; all changes stay local until Save."""
from sk_material_bias import KEY, NOTE, normalize, rules, selected_path, inventory


def merged(values,paths,kind,root,factor):
    updated={r['path'].casefold():dict(r) for r in values}
    for path in paths:
        relative=selected_path(path,kind,root)
        updated[relative.casefold()]=dict(path=relative,kind=kind,factor=factor)
    return normalize(list(updated.values()))


def qt_panel(c,cfg):
    from qt_workbench import W, C
    panel=W.QWidget();panel.setObjectName('skMaterialBias');layout=W.QVBoxLayout(panel)
    note=W.QLabel('选择想多用的目录或视频，1倍为正常，建议先试3～5倍。\n'+NOTE)
    note.setWordWrap(True);layout.addWidget(note)
    tools=W.QHBoxLayout();layout.addLayout(tools)
    folder=W.QPushButton('添加目录');files=W.QPushButton('添加素材（可多选）')
    factor=W.QDoubleSpinBox();factor.setRange(1,100);factor.setDecimals(2);factor.setValue(3);factor.setSuffix(' 倍')
    factor.setObjectName('skBiasBulkFactor')
    apply=W.QPushButton('应用到所选');remove=W.QPushButton('移除所选');clear=W.QPushButton('清空偏好')
    for widget in (folder,files,factor,apply):tools.addWidget(widget)
    tools2=W.QHBoxLayout();layout.addLayout(tools2);tools2.addWidget(remove);tools2.addWidget(clear);tools2.addStretch()
    table=W.QTableWidget(0,4);table.setObjectName('skBiasTable')
    table.setHorizontalHeaderLabels(['SK相对路径','对象','提升倍数','当前可用文件数'])
    table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
    table.horizontalHeader().setSectionResizeMode(0,W.QHeaderView.ResizeMode.Stretch)
    table.setColumnWidth(1,65);table.setColumnWidth(2,110);table.setColumnWidth(3,115)
    layout.addWidget(table);error=W.QLabel();error.setWordWrap(True);error.setStyleSheet('color:#b42318');layout.addWidget(error)

    def values():
        return normalize([dict(path=table.item(i,0).text(),kind=table.item(i,1).data(C.Qt.ItemDataRole.UserRole),
                               factor=table.cellWidget(i,2).value()) for i in range(table.rowCount())])
    def populate(rows):
        available={r['path']:r['available_files'] for r in inventory(c.clips,dict(cfg,**{KEY:rows}))}
        table.setRowCount(0)
        for i,row in enumerate(rows):
            table.insertRow(i);table.setItem(i,0,W.QTableWidgetItem(row['path']))
            kind=W.QTableWidgetItem('目录' if row['kind']=='folder' else '视频');kind.setData(C.Qt.ItemDataRole.UserRole,row['kind']);table.setItem(i,1,kind)
            spin=W.QDoubleSpinBox();spin.setRange(1,100);spin.setDecimals(2);spin.setValue(row['factor']);spin.setSuffix(' 倍');table.setCellWidget(i,2,spin)
            count=str(available[row['path']]) if c.clips else '待扫描'
            table.setItem(i,3,W.QTableWidgetItem(count));table.item(i,0).setToolTip(row['path'])
    def guard(fn):
        try:fn();error.clear()
        except ValueError as exc:error.setText(str(exc))
    def add_paths(paths,kind):populate(merged(values(),paths,kind,cfg['material_root'],factor.value()))
    def browse_folder():
        path=W.QFileDialog.getExistingDirectory(panel,'选择想多用的SK子目录',cfg['material_root'])
        if path:add_paths([path],'folder')
    def browse_files():
        paths,_=W.QFileDialog.getOpenFileNames(panel,'选择想多用的SK视频',cfg['material_root'],'视频 (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts)')
        if paths:add_paths(paths,'file')
    def selected_rows():return sorted({index.row() for index in table.selectionModel().selectedRows()})
    def apply_factor():
        for i in selected_rows():table.cellWidget(i,2).setValue(factor.value())
    def remove_rows():
        for i in reversed(selected_rows()):table.removeRow(i)
    folder.clicked.connect(lambda:guard(browse_folder));files.clicked.connect(lambda:guard(browse_files))
    apply.clicked.connect(apply_factor);remove.clicked.connect(remove_rows);clear.clicked.connect(lambda:table.setRowCount(0))
    populate(rules(cfg));panel.rules=values;panel.add_paths=add_paths;panel.table=table
    return panel


def tk_panel(c,cfg,parent):
    import tkinter as tk
    from tkinter import ttk,filedialog
    panel=ttk.Frame(parent);rows=rules(cfg)
    ttk.Label(panel,text='选择想多用的目录或视频，1倍为正常，建议先试3～5倍。\n'+NOTE,wraplength=790).pack(fill='x',padx=8,pady=8)
    tools=ttk.Frame(panel);tools.pack(fill='x',padx=8)
    factor=tk.StringVar(value='3');error=tk.StringVar()
    body=ttk.Frame(panel);body.pack(fill='both',expand=True,padx=8,pady=8)
    table=ttk.Treeview(body,columns=('path','kind','factor','count'),show='headings',selectmode='extended')
    for key,title,width in (('path','SK相对路径',380),('kind','对象',65),('factor','提升倍数',90),('count','当前可用文件数',120)):
        table.heading(key,text=title);table.column(key,width=width,stretch=key=='path')
    bar=ttk.Scrollbar(body,orient='vertical',command=table.yview);bar.pack(side='right',fill='y');table.configure(yscrollcommand=bar.set);table.pack(fill='both',expand=True)
    def populate(values):
        nonlocal rows
        rows=normalize(values)
        for item in table.get_children():table.delete(item)
        available={r['path']:r['available_files'] for r in inventory(c.clips,dict(cfg,**{KEY:rows}))}
        for i,row in enumerate(rows):table.insert('','end',iid=str(i),values=(row['path'],'目录' if row['kind']=='folder' else '视频',f'{row["factor"]:g}倍',available[row['path']] if c.clips else '待扫描'))
    def guard(fn):
        try:fn();error.set('')
        except ValueError as exc:error.set(str(exc))
    def add_paths(paths,kind):populate(merged(rows,paths,kind,cfg['material_root'],factor.get()))
    def browse_folder():
        path=filedialog.askdirectory(parent=panel,initialdir=cfg['material_root'],title='选择想多用的SK子目录')
        if path:add_paths([path],'folder')
    def browse_files():
        paths=filedialog.askopenfilenames(parent=panel,initialdir=cfg['material_root'],title='选择想多用的SK视频',filetypes=[('视频','*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.ts')])
        if paths:add_paths(paths,'file')
    def apply_factor():
        values=[dict(r) for r in rows]
        for i in table.selection():values[int(i)]['factor']=factor.get()
        populate(values)
    def remove_rows():
        chosen={int(i) for i in table.selection()};populate([r for i,r in enumerate(rows) if i not in chosen])
    for label,fn in (('添加目录',browse_folder),('添加素材（可多选）',browse_files)):
        ttk.Button(tools,text=label,command=lambda fn=fn:guard(fn)).pack(side='left',padx=3)
    ttk.Spinbox(tools,from_=1,to=100,textvariable=factor,width=6).pack(side='left',padx=3)
    for label,fn in (('应用到所选',apply_factor),('移除所选',remove_rows),('清空偏好',lambda:populate([]))):
        ttk.Button(tools,text=label,command=lambda fn=fn:guard(fn)).pack(side='left',padx=3)
    ttk.Label(panel,textvariable=error,foreground='#b42318',wraplength=790).pack(fill='x')
    populate(rows);panel.rules=lambda:normalize(rows);panel.add_paths=add_paths;panel.table=table
    return panel
