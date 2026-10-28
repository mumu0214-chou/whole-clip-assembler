"""Native picture tagging editor; no Tk child windows in the Qt host."""
from editorial import Tags,TAGS,key


def open_editor(app):
    if not app.clips:raise ValueError('请先扫描素材，再标记画面和动作范围')
    from qt_runtime import activate
    activate()
    from PySide6 import QtWidgets as W,QtCore as C
    if not getattr(app,'_qt_parent',None):
        raise ValueError('画面标签编辑器请使用默认Qt工作台打开')
    existing=getattr(app,'picture_editor',None)
    if existing is not None and existing.isVisible():existing.raise_();return
    dialog=W.QDialog(app._qt_parent);app.picture_editor=dialog
    dialog.setWindowTitle('画面标签 / 完整动作范围');dialog.resize(1080,680)
    layout=W.QVBoxLayout(dialog)
    layout.addWidget(W.QLabel('音效分类可作初始建议；保存后成为独立标签。动作起止填原片秒数，不填写表示未知。双击文件可查看原片。'))
    layout.addWidget(W.QLabel('标签可多选（逗号分隔）：'+'，'.join(TAGS.values())))
    search=W.QLineEdit();search.setPlaceholderText('搜索文件');layout.addWidget(search)
    table=W.QTableWidget(len(app.clips),6);table.setHorizontalHeaderLabels(['文件','时长','画面标签','动作开始(秒)','动作结束(秒)','标签来源']);layout.addWidget(table)
    store=Tags(app.data_dir,app.cfg['material_root']);rows=store.rows();clips=list(app.clips)
    reverse={v:k for k,v in TAGS.items()}
    from ball_scene_rotation import capture_config,categories
    live=dict(app.cfg);live.pop('ball_scene_bindings_snapshot',None);live.pop('editorial_snapshot',None)
    suggestions=categories(capture_config(live,app.data_dir))
    for i,c in enumerate(clips):
        explicit=key(c) in rows;note=rows.get(key(c),dict(tags=list(suggestions.get(c['hash'],()))));core=note.get('action_core') or ('','')
        origin='独立标签' if explicit else '音效分类建议' if note.get('tags') else '未标注'
        values=[c.get('relative_path',c['path']),str(c['duration']),','.join(TAGS[t] for t in note.get('tags',[])),str(core[0]),str(core[1]),origin]
        for j,value in enumerate(values):
            cell=W.QTableWidgetItem(value)
            if j<2 or j==5:cell.setFlags(cell.flags() & ~C.Qt.ItemFlag.ItemIsEditable)
            table.setItem(i,j,cell)
    table.horizontalHeader().setSectionResizeMode(0,W.QHeaderView.ResizeMode.Stretch)
    table.setColumnWidth(2,280)
    search.textChanged.connect(lambda text:[table.setRowHidden(i,text.casefold() not in c.get('relative_path',c['path']).casefold()) for i,c in enumerate(clips)])
    def preview(row,column):
        if column==0:
            from core import open_local
            open_local(clips[row]['path'])
    table.cellDoubleClicked.connect(preview)
    buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Save|W.QDialogButtonBox.StandardButton.Cancel);layout.addWidget(buttons)
    def save():
        try:
            changes=[]
            for i,c in enumerate(clips):
                label=table.item(i,2).text().strip();start=table.item(i,3).text().strip();end=table.item(i,4).text().strip()
                tags=[reverse[v.strip()] for v in label.replace('，',',').split(',') if v.strip()]
                core=[float(start),float(end)] if start or end else None
                if label or start or end or key(c) in rows:changes.append((c,dict(tags=tags,action_core=core,source='manual_editor')))
            store.save(changes);app.log(f'已保存{len(changes)}条画面标签；仅供新方案和明确更新后的待生成条目使用。');dialog.accept()
        except Exception as exc:W.QMessageBox.warning(dialog,'请检查',str(exc))
    buttons.accepted.connect(save);buttons.rejected.connect(dialog.reject);dialog.show()
