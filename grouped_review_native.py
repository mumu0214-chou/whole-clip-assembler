"""Pure Qt G review, sharing the original Coverage and approval actions."""
from qt_workbench import C,W
from grouped_materials import Coverage
from grouped_review_ui import GroupedReviewWindow

class Value:
    def __init__(self,value='',changed=None):self.value=value;self.changed=changed
    def get(self):return self.value
    def set(self,value):
        if self.value!=value:
            self.value=value
            if self.changed:self.changed()

class Rows:
    """Stable row IDs and scoped pagination without any Tcl widget or variable."""
    def __init__(self,columns):
        self.columns=columns;self.records={};self.order=[];self.chosen=set();self.page=0;self.page_size=50
        self.query=Value('',self.search);self.page_label=Value();self._filtered=None;self._external_view=None
    def changed(self):
        self._filtered=None
        if self._external_view:self._external_view()
    def filtered_ids(self):
        if self._filtered is None:
            query=self.query.get().strip().casefold()
            self._filtered=tuple(i for i in self.order if not query or query in ' '.join(map(str,self.records[i]['values'])).casefold())
        return self._filtered
    def visible_ids(self):return self.filtered_ids()[self.page*self.page_size:(self.page+1)*self.page_size]
    def _label(self):
        count=len(self.filtered_ids());pages=max(1,(count+self.page_size-1)//self.page_size)
        self.page_label.set(f'{self.page+1}/{pages}页 · {count}条 · 已选{len(self.chosen)}条')
    def search(self):self.page=0;self._filtered=None;self.chosen.intersection_update(self.filtered_ids());self.changed()
    def insert(self,parent,index,iid,values):self.records[iid]={'values':values};self.order.append(iid);self.changed()
    def delete(self,*ids):
        keys=set(ids)
        for key in keys:self.records.pop(key,None)
        self.order=[key for key in self.order if key not in keys];self.chosen.difference_update(keys);self.changed()
    def get_children(self):return tuple(self.order)
    def selection(self):return tuple(i for i in self.order if i in self.chosen)
    def selection_set(self,*ids):
        if len(ids)==1 and isinstance(ids[0],(list,tuple,set)):ids=ids[0]
        self.chosen=set(ids).intersection(self.filtered_ids());self.changed()
    def select_all(self):self.selection_set(self.filtered_ids())
    def select_page(self):self.chosen.update(self.visible_ids());self.changed()
    def clear_selection(self):self.selection_set(())
    def go(self,page):self.page=max(0,min(page,max(0,(len(self.filtered_ids())-1)//self.page_size)));self.changed()

class ReviewState:
    """Reuse the existing actions on GUI-neutral state; one business authority."""
    def __init__(self,app,clips,root,parent):
        self.app=app;self.clips=clips;self.coverage=Coverage(app.store,root);self._qt_dialog_parent=parent
        self.rows=[];self.scope_rows={};self.scope_ids={};self.pair_scope=None;self.status=Value();self.details=Value()
        self.scope_note=Value('当前范围：全部');self.pair_filter=Value('待确认');self.pages=Value('groups');self.pair_page='pairs'
        self.groups=Rows(('确认范围','范围规则','关系数','待确认','人工例外'))
        self.pairs=Rows(('片段一','片段二','命名建议','人工状态'));self.assets=Rows(('完整相对路径','镜头类型','可安全裁切','开头选片'))
        self.refresh()
    refresh=GroupedReviewWindow.refresh
    selected_scope_rows=GroupedReviewWindow.selected_scope_rows
    confirm_scopes=GroupedReviewWindow.confirm_scopes
    all_scopes=GroupedReviewWindow.all_scopes
    refresh_pairs=GroupedReviewWindow.refresh_pairs
    confirm_pairs=GroupedReviewWindow.confirm_pairs
    refresh_assets=GroupedReviewWindow.refresh_assets
    cut=GroupedReviewWindow.cut
    opening=GroupedReviewWindow.opening
    group_details=GroupedReviewWindow.group_details
    play=GroupedReviewWindow.play
    play_pair=GroupedReviewWindow.play_pair
    play_asset=GroupedReviewWindow.play_asset
    def show_scope(self):
        self.pair_scope={self.scope_ids[i] for i in self.groups.selection()}
        if not self.pair_scope:raise ValueError('请先选择确认范围')
        self.pair_filter.set('全部');self.refresh_pairs();self.pages.set('pairs')

class ReviewModel(C.QAbstractTableModel):
    def __init__(self,source,parent):
        super().__init__(parent);self.source=source;self.ids=();self.values=[];self.dirty=True;source._external_view=self.mark_dirty
    def mark_dirty(self):self.dirty=True
    def rowCount(self,parent=C.QModelIndex()):return 0 if parent.isValid() else len(self.ids)
    def columnCount(self,parent=C.QModelIndex()):return len(self.source.columns)
    def data(self,index,role=C.Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (C.Qt.ItemDataRole.DisplayRole,C.Qt.ItemDataRole.ToolTipRole):
            if 0<=index.row()<len(self.values) and 0<=index.column()<len(self.values[index.row()]):
                return str(self.values[index.row()][index.column()]).replace(' ↔ ',' 与 ')
    def headerData(self,section,orientation,role=C.Qt.ItemDataRole.DisplayRole):
        if role==C.Qt.ItemDataRole.DisplayRole and orientation==C.Qt.Orientation.Horizontal:return self.source.columns[section]
    def sync(self):
        if not self.dirty:return
        self.dirty=False;count=len(self.source.filtered_ids());self.source.page=min(self.source.page,max(0,(count-1)//self.source.page_size));self.source._label()
        ids=tuple(self.source.visible_ids());values=[self.source.records[i]['values'] for i in ids]
        self.beginResetModel();self.ids=ids;self.values=values;self.endResetModel()

class ReviewTable(W.QWidget):
    def __init__(self,source,changed,parent):
        super().__init__(parent);self.source=source;self.changed=changed;layout=W.QVBoxLayout(self)
        self.view=W.QTableView();self.model=ReviewModel(source,self.view);self.view.setModel(self.model)
        self.view.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);self.view.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setAlternatingRowColors(True);self.view.horizontalHeader().setStretchLastSection(True);layout.addWidget(self.view,1)
        self.view.selectionModel().selectionChanged.connect(self.selection)
        filters=W.QHBoxLayout();layout.addLayout(filters);filters.addWidget(W.QLabel('筛选'));self.search=W.QLineEdit();self.search.textChanged.connect(source.query.set);filters.addWidget(self.search,1)
        filters.addWidget(W.QLabel('每页'));self.size=W.QComboBox();self.size.addItems(['20','50','100','200']);self.size.setCurrentText('50');self.size.currentTextChanged.connect(self.page_size);filters.addWidget(self.size)
        self.count=W.QLabel();self.count.setWordWrap(True);filters.addWidget(self.count)
        actions=W.QHBoxLayout();layout.addLayout(actions)
        for text,fn in [('上一页',lambda:source.go(source.page-1)),('下一页',lambda:source.go(source.page+1)),('全选本页',source.select_page),('全选筛选结果',source.select_all),('取消全选',source.clear_selection)]:
            button=W.QPushButton(text);button.clicked.connect(lambda _=False,action=fn:(action(),self.sync(),changed()));actions.addWidget(button)
        self.sync()
    def page_size(self,text):self.source.page_size=int(text);self.source.go(0);self.sync()
    def selection(self):
        self.source.chosen.difference_update(self.model.ids)
        self.source.chosen.update(self.model.ids[index.row()] for index in self.view.selectionModel().selectedRows() if 0<=index.row()<len(self.model.ids))
        self.source._label();self.count.setText(self.source.page_label.get());self.changed()
    def sync(self):
        selection=self.view.selectionModel()
        if self.model.dirty:
            with C.QSignalBlocker(selection):
                self.model.sync();selection.clearSelection()
                for row,key in enumerate(self.model.ids):
                    if key in self.source.chosen:selection.select(self.model.index(row,0),C.QItemSelectionModel.SelectionFlag.Select|C.QItemSelectionModel.SelectionFlag.Rows)
        with C.QSignalBlocker(self.search):self.search.setText(self.source.query.get())
        self.count.setText(self.source.page_label.get())

class GroupedReviewDialog(W.QDialog):
    def __init__(self,app,clips,root,parent):
        super().__init__(parent);self.app=app;self.closed=False;self.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True)
        self.setWindowTitle('G组素材复核 · 按组确认与例外管理');self.resize(1250,850)
        self.source=ReviewState(app,clips,root,self);self.models=[];layout=W.QVBoxLayout(self)
        note=W.QLabel('按组核对画面后确认关系；个别重复到“关系例外”修改。关系与安全裁切分别保存，新内容仍需复核。');note.setWordWrap(True);layout.addWidget(note)
        self.tabs=W.QTabWidget();layout.addWidget(self.tabs,1);self.page_ids=['groups','pairs','assets'];self.tables=[];self.buttons=[]
        for key,label in zip(self.page_ids,['按组确认','关系例外 / 明细','安全裁切 / 开头排除']):
            page=W.QWidget();body=W.QVBoxLayout(page);self.tabs.addTab(page,label)
            if key=='groups':
                text=W.QLabel('组内核对组合与成员的覆盖关系；组间核对没有重复画面后，可一次确认所选范围。');text.setWordWrap(True);body.addWidget(text)
            elif key=='pairs':
                filters=W.QHBoxLayout();body.addLayout(filters);filters.addWidget(W.QLabel('状态'));self.state=W.QComboBox();self.state.addItems(['全部','待确认','重复','不重复','仅人工例外']);self.state.setCurrentText('待确认')
                self.state.currentTextChanged.connect(lambda text:self.guard(lambda:(self.source.pair_filter.set(text),self.source.refresh_pairs())));filters.addWidget(self.state)
                self.add_button(filters,'显示全部范围',self.source.all_scopes);self.scope_note=W.QLabel();self.scope_note.setWordWrap(True);filters.addWidget(self.scope_note,1)
            else:
                text=W.QLabel('安全裁切与开头排除分别保存。禁止作开头的片段仍可用于中后段；可多选或全选筛选结果，仅影响牛肉丸新方案。');text.setWordWrap(True);body.addWidget(text)
            table=ReviewTable(getattr(self.source,key),self.group_details if key=='groups' else lambda:None,page);self.tables.append(table);self.models.append((table.view,table.model));body.addWidget(table,1)
            actions=W.QHBoxLayout();body.addLayout(actions)
            choices=([('按规则确认所选范围',self.source.confirm_scopes),('查看所选范围明细',self.source.show_scope)] if key=='groups' else
                     [('选中设为重复',lambda:self.source.confirm_pairs('overlap')),('选中设为不重复',lambda:self.source.confirm_pairs('clear')),('选中恢复待确认',lambda:self.source.confirm_pairs('pending')),('播放片段一',lambda:self.source.play_pair('left')),('播放片段二',lambda:self.source.play_pair('right'))] if key=='pairs' else
                     [('选中镜头允许安全裁切',lambda:self.source.cut(True)),('选中镜头保持完整',lambda:self.source.cut(False)),('选中禁止作开头',lambda:self.source.opening(True)),('选中恢复可作开头',lambda:self.source.opening(False)),('播放选中镜头',self.source.play_asset)])
            for text,fn in choices:self.add_button(actions,text,fn)
            if key=='groups':self.details=W.QLabel();self.details.setWordWrap(True);body.addWidget(self.details)
        self.status=W.QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        if getattr(app,'scan_warnings',None):
            warning=W.QPlainTextEdit('异常文件不参与选片：\n'+'\n'.join(app.scan_warnings));warning.setReadOnly(True);warning.setMaximumHeight(85);layout.addWidget(warning)
        self.tabs.currentChanged.connect(lambda index:self.source.pages.set(self.page_ids[index]))
        self.timer=C.QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.sync);self.timer.start();self.sync()
        import runtime_diagnostics as diag
        diag.emit('grouped_review_open','纯Qt素材复核窗口已打开',component='qt_review',ui_revision='native_no_tcl_v2',files=len(clips),pairs=len(self.source.rows))
    def add_button(self,bar,text,fn):
        button=W.QPushButton(text);button.clicked.connect(lambda _=False:self.guard(fn));bar.addWidget(button);self.buttons.append(button)
    def group_details(self):
        self.source.group_details()
        if hasattr(self,'details'):self.details.setText(self.source.details.get())
    def guard(self,fn):
        try:fn();self.sync()
        except Exception as exc:self.source.status.set(str(exc));self.status.setText(str(exc));W.QMessageBox.critical(self,'素材复核',str(exc))
    def sync(self):
        if self.closed:return
        for table in self.tables:table.sync()
        with C.QSignalBlocker(self.state):self.state.setCurrentText(self.source.pair_filter.get())
        self.scope_note.setText(self.source.scope_note.get().replace(' ↔ ',' 与 '));self.details.setText(self.source.details.get());self.status.setText(self.source.status.get())
        with C.QSignalBlocker(self.tabs):self.tabs.setCurrentIndex(self.page_ids.index(self.source.pages.get()))
    def closeEvent(self,event):
        if not self.closed:
            self.closed=True;self.timer.stop()
            for table in self.tables:table.source._external_view=None
            self.source._qt_dialog_parent=None
        event.accept()
