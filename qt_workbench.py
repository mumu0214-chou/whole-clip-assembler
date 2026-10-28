"""Native Qt presentation of the existing compositor controls and commands.

Tcl remains a hidden state controller. Legacy editors run in their own process.
Qt owns the main window, layout, scrolling, table models, navigation and input.
No media operations, business randomization or data migration are implemented here.
"""
from mac_paths import path as _portable_path
from pathlib import Path
import time,tkinter as tk
from qt_runtime import activate
activate()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W

STYLE='''
QWidget {font-family:"Microsoft YaHei UI"; font-size:14px; color:#1e293b;}
QMainWindow,QScrollArea,QStackedWidget {background:#f4f6f8;}
QLabel#pageTitle {font-size:28px;font-weight:700;} QLabel#brand {font-size:20px;color:#0f766e;font-weight:700;}
QPushButton,QToolButton {background:white;border:1px solid #dce3e9;border-radius:6px;padding:8px 12px;}
QPushButton:hover,QToolButton:hover {background:#eef6f4;} QPushButton:disabled {color:#9aa6b2;background:#edf0f3;}
QPushButton[primary="true"] {background:#0f766e;color:white;border-color:#0f766e;}
QPushButton[primary="true"]:disabled {background:#b8c8c5;border-color:#b8c8c5;color:white;}
QPushButton[nav="true"] {text-align:left;border:0;background:transparent;padding:12px;}
QPushButton[nav="true"]:checked {background:#ddf1ed;color:#0f766e;font-weight:600;}
QLineEdit,QComboBox,QPlainTextEdit {background:white;border:1px solid #dce3e9;border-radius:5px;padding:6px;}
QGroupBox {border:1px solid #e2e8f0;border-radius:8px;margin-top:14px;padding:14px 8px 8px;font-weight:600;}
QGroupBox::title {subcontrol-origin:margin;left:12px;padding:0 6px;}
QTableView {background:white;alternate-background-color:#f8fafc;border:1px solid #e2e8f0;gridline-color:#eef1f4;selection-background-color:#ddf1ed;selection-color:#1e293b;}
QHeaderView::section {background:#eaf0f3;border:0;padding:9px;color:#64748b;font-weight:600;}
QScrollBar:vertical {background:#f0f3f6;width:10px;margin:0;} QScrollBar::handle:vertical {background:#c4ced6;border-radius:5px;min-height:28px;}
QScrollBar:horizontal {background:#f0f3f6;height:10px;} QScrollBar::handle:horizontal {background:#c4ced6;border-radius:5px;min-width:28px;}
QScrollBar::add-line,QScrollBar::sub-line {width:0;height:0;} QProgressBar {max-height:5px;border:0;background:#e2e8f0;}
QProgressBar::chunk {background:#0f766e;}
'''

def configure_fonts():
    """Load a system CJK face explicitly, including offscreen Qt verification."""
    font=Path(_portable_path(r'C:\Windows\Fonts\msyh.ttc'))
    if font.is_file():G.QFontDatabase.addApplicationFont(str(font))
    application=W.QApplication.instance()
    if application:
        application.setFont(G.QFont('Microsoft YaHei UI',10))
        from wheel_guard import install_qt
        install_qt(application)

class SmoothWheel(C.QObject):
    """Pixel scrolling with short eased wheel motion; touchpad pixels stay direct."""
    def __init__(self,area):
        super().__init__(area);self.area=area
        self.animation=C.QPropertyAnimation(area.verticalScrollBar(),b'value',self)
        self.animation.setDuration(110);self.animation.setEasingCurve(C.QEasingCurve.Type.OutCubic)
        area.viewport().installEventFilter(self)
    def eventFilter(self,obj,event):
        if event.type()!=C.QEvent.Type.Wheel or event.modifiers() & C.Qt.KeyboardModifier.ControlModifier:return False
        bar=self.area.verticalScrollBar()
        if bar.maximum()==0:return False
        pixel=event.pixelDelta().y()
        if pixel:
            self.animation.stop();bar.setValue(bar.value()-pixel)
        else:
            amount=event.angleDelta().y()/120*90
            if not amount:return False
            base=self.animation.endValue() if self.animation.state()==C.QAbstractAnimation.State.Running else bar.value()
            target=max(0,min(bar.maximum(),int(base-amount)))
            self.animation.stop();self.animation.setStartValue(bar.value());self.animation.setEndValue(target);self.animation.start()
        event.accept();return True

class TableModel(C.QAbstractTableModel):
    """Only visible page rows, retaining selection, scroll and column geometry."""
    def __init__(self,source,parent=None):
        super().__init__(parent);self.source=source;self.ids=();self.values=[];self.resets=0;self.cell_updates=0
        self.columns=source.tk.splitlist(source.cget('columns'))
        self.headers=[str(source.heading(k,'text')) for k in self.columns]
        self.dirty=True;source._external_view=self.mark_dirty
    def mark_dirty(self):self.dirty=True
    def rowCount(self,parent=C.QModelIndex()):return 0 if parent.isValid() else len(self.ids)
    def columnCount(self,parent=C.QModelIndex()):return len(self.columns)
    def data(self,index,role=C.Qt.ItemDataRole.DisplayRole):
        if index.isValid() and role in (C.Qt.ItemDataRole.DisplayRole,C.Qt.ItemDataRole.ToolTipRole):
            row=self.values[index.row()];return str(row[index.column()]) if index.column()<len(row) else ''
    def headerData(self,section,orientation,role=C.Qt.ItemDataRole.DisplayRole):
        if orientation==C.Qt.Orientation.Horizontal and role==C.Qt.ItemDataRole.DisplayRole:return self.headers[section]
        return super().headerData(section,orientation,role)
    def sync(self):
        if not self.dirty:return
        self.dirty=False
        headers=[str(self.source.heading(k,'text')) for k in self.columns]
        if headers!=self.headers:
            self.headers=headers
            self.headerDataChanged.emit(C.Qt.Orientation.Horizontal,0,len(headers)-1)
        count=len(self.source.filtered_ids());self.source.page=min(self.source.page,max(0,(count-1)//self.source.page_size));self.source._label()
        ids=tuple(self.source.visible_ids());values=[tuple(self.source.records[i].get('values',())) for i in ids]
        if ids!=self.ids:
            self.beginResetModel();self.ids=ids;self.values=values;self.endResetModel();self.resets+=1
        else:
            for n,(old,new) in enumerate(zip(self.values,values)):
                if old!=new:
                    self.values[n]=new
                    for col in range(len(self.columns)):
                        if (old[col] if col<len(old) else '')!=(new[col] if col<len(new) else ''):
                            self.dataChanged.emit(self.index(n,col),self.index(n,col),[C.Qt.ItemDataRole.DisplayRole]);self.cell_updates+=1

class Presenter:
    """Bindings share the existing Tcl variables, commands and stable row IDs."""
    def __init__(self,controller):
        self.controller=controller;self.nodes={};self.models=[];self.syncers=[];self.containers=[];self.unsupported=set()
    def value(self,source,name,default=''):
        try:return source.cget(name)
        except tk.TclError:return default
    def variable(self,source,key='textvariable'):
        name=self.value(source,key)
        return str(name) if name else None
    def getvar(self,name):return self.controller.getvar(name) if name else ''
    def setvar(self,name,value):
        if name:self.controller.setvar(name,value)
    def event(self,source,sequence):
        # Scheme switches can replace a focused entry before Qt sends FocusOut.
        if source.winfo_exists():source.event_generate(sequence,when='now')
    def invoke(self,source):
        # Native modal questions can run another Qt event loop while Tcl is
        # still inside this callback. Do not recursively pump that interpreter.
        self.controller._qt_callback_depth=getattr(self.controller,'_qt_callback_depth',0)+1
        try:source.invoke();self.sync()
        finally:self.controller._qt_callback_depth-=1
    def bind_text(self,source,target,kind='entry'):
        name=self.variable(source)
        def read():return str(self.getvar(name)) if name else str(source.get())
        def write(value):
            if name:self.setvar(name,value)
            else:source.delete(0,'end');source.insert(0,value)
        if kind=='combo':
            def changed(value):write(value);self.event(source,'<<ComboboxSelected>>')
            target.currentTextChanged.connect(changed)
        else:target.textEdited.connect(write)
        def sync():
            text=read();blocked=C.QSignalBlocker(target)
            if kind=='combo':
                values=[str(v) for v in source.tk.splitlist(self.value(source,'values',()))]
                if values!=[target.itemText(i) for i in range(target.count())]:target.clear();target.addItems(values)
                target.setEditable(str(self.value(source,'state'))!='readonly')
                target.setCurrentText(text)
            elif target.text()!=text:target.setText(text)
        self.syncers.append((target,sync));sync()
    def container(self,source,target,root=False):
        children=source.winfo_children()
        packed=source.pack_slaves()
        if packed:children=list(packed)+[c for c in children if c not in packed]
        # Scrollbars are replaced by native scroll areas/table view scrollbars.
        children=[c for c in children if 'Scrollbar' not in c.winfo_class()]
        grid=any(c.winfo_manager()=='grid' for c in children)
        layout=target.layout()
        if layout is None:layout=W.QGridLayout(target) if grid else W.QVBoxLayout(target)
        else:self.clear_layout(layout)
        grid=isinstance(layout,W.QGridLayout)
        layout.setContentsMargins(8,6,8,6);layout.setSpacing(8)
        nodes=[];horizontal=None
        for child in children:
            widget=self.build(child);nodes.append(str(child))
            if grid:
                info=child.grid_info()
                if info:
                    row=int(info.get('row',0));col=int(info.get('column',0));layout.addWidget(widget,row,col,int(info.get('rowspan',1)),int(info.get('columnspan',1)))
                    if 'e' in str(info.get('sticky','')) and 'w' in str(info.get('sticky','')):layout.setColumnStretch(col,1)
                else:layout.addWidget(widget,layout.rowCount(),0)
            else:
                info=child.pack_info() if child.winfo_manager()=='pack' else {}
                if info.get('side') in ('left','right'):
                    if horizontal is None:horizontal=W.QHBoxLayout();horizontal.setSpacing(8);layout.addLayout(horizontal)
                    horizontal.addWidget(widget,1 if info.get('expand') else 0)
                else:
                    horizontal=None;layout.addWidget(widget,1 if info.get('expand') else 0)
            if not child.winfo_manager():widget.hide()
        self.containers=[entry for entry in self.containers if entry[0] is not source]
        self.containers.append((source,target,tuple(nodes)))
        if root:layout.addStretch(1)
        return target
    def clear_layout(self,layout):
        while layout.count():
            item=layout.takeAt(0)
            if item.layout():self.clear_layout(item.layout());item.layout().deleteLater()
    def build(self,source):
        key=str(source)
        if key in self.nodes:return self.nodes[key]
        cls=source.winfo_class();text=str(self.value(source,'text'))
        if hasattr(source,'expanded') and hasattr(source,'body'):
            widget=W.QWidget();layout=W.QVBoxLayout(widget);layout.setContentsMargins(0,0,0,0)
            toggle=W.QToolButton();toggle.setText(source.title);toggle.setToolButtonStyle(C.Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setCheckable(True);toggle.setChecked(source.expanded);body=self.build(source.body)
            layout.addWidget(toggle);layout.addWidget(body);body.setVisible(source.expanded)
            toggle.toggled.connect(lambda yes:(source.set_expanded(yes),body.setVisible(yes)))
            self.syncers.append((widget,lambda:self.disclosure_sync(source,toggle,body)))
        elif hasattr(source,'canvas') and hasattr(source,'body'):
            widget=W.QScrollArea();widget.setWidgetResizable(True);widget.setFrameShape(W.QFrame.Shape.NoFrame)
            widget.setWidget(self.build(source.body));widget._smooth=SmoothWheel(widget)
        elif cls=='Canvas':
            windows=[i for i in source.find_all() if source.type(i)=='window']
            if len(windows)!=1:raise RuntimeError('Qt滚动容器缺少唯一内容窗口：'+str(source))
            body=source.nametowidget(source.itemcget(windows[0],'window'))
            widget=W.QScrollArea();widget.setWidgetResizable(True);widget.setFrameShape(W.QFrame.Shape.NoFrame)
            widget.setWidget(self.build(body));widget._smooth=SmoothWheel(widget)
        elif cls in ('Frame','TFrame','Labelframe','TLabelframe'):
            widget=W.QGroupBox(text) if 'Labelframe' in cls else W.QWidget()
            self.nodes[key]=widget;return self.container(source,widget)
        elif cls in ('Button','TButton'):
            widget=W.QPushButton(text);widget.clicked.connect(lambda:self.invoke(source))
            if str(self.value(source,'style'))=='Accent.TButton':widget.setProperty('primary',True)
        elif cls in ('Label','TLabel'):
            widget=W.QLabel(text);widget.setWordWrap(True);widget.setMaximumWidth(1100)
            name=self.variable(source)
            if name:self.syncers.append((widget,lambda w=widget,n=name:w.setText(str(self.getvar(n)))))
        elif cls in ('Entry','TEntry','Spinbox','TSpinbox'):
            widget=W.QLineEdit();self.bind_text(source,widget)
            widget.setMinimumWidth(min(300,max(65,int(self.value(source,'width',10))*8)))
            widget.setSizePolicy(W.QSizePolicy.Policy.Expanding,W.QSizePolicy.Policy.Fixed)
            widget.returnPressed.connect(lambda:self.event(source,'<Return>'))
            widget.editingFinished.connect(lambda:self.event(source,'<FocusOut>'))
        elif cls=='TCombobox':
            widget=W.QComboBox();widget.setMinimumWidth(max(80,min(240,int(self.value(source,'width',12))*8)))
            self.bind_text(source,widget,'combo')
        elif cls in ('Checkbutton','TCheckbutton','Radiobutton','TRadiobutton'):
            radio='Radiobutton' in cls;widget=W.QRadioButton(text) if radio else W.QCheckBox(text)
            if radio:widget.setAutoExclusive(False)
            widget.clicked.connect(lambda:self.invoke(source))
            name=self.variable(source,'variable')
            def sync_check(w=widget,s=source,n=name,is_radio=radio):
                value=self.getvar(n);expect=self.value(s,'value') if is_radio else self.value(s,'onvalue',1)
                with C.QSignalBlocker(w):w.setChecked(str(value)==str(expect))
            self.syncers.append((widget,sync_check));sync_check()
        elif cls in ('Scale','TScale'):
            widget=W.QSlider(C.Qt.Orientation.Horizontal);name=self.variable(source,'variable')
            scale=100 if float(self.value(source,'resolution',1))<1 else 1
            widget.setRange(round(float(self.value(source,'from',0))*scale),round(float(self.value(source,'to',100))*scale))
            widget.valueChanged.connect(lambda v:self.setvar(name,v/scale))
            def slider_sync():
                with C.QSignalBlocker(widget):widget.setValue(round(float(self.getvar(name))*scale))
            self.syncers.append((widget,slider_sync))
        elif cls=='Text':
            widget=W.QPlainTextEdit();height=max(65,min(280,int(self.value(source,'height',4))*23));widget.setMinimumHeight(height)
            if source is self.controller.log_box:widget.setMaximumHeight(82)
            widget.setReadOnly(str(self.value(source,'state'))=='disabled');widget._last_text=None
            def read_text():
                value=source.get('1.0','end-1c')
                if value!=widget._last_text:
                    previous=widget._last_text
                    widget._last_text=value
                    if widget.toPlainText()!=value:
                        with C.QSignalBlocker(widget):
                            if previous and value.startswith(previous):
                                bar=widget.verticalScrollBar();follow=bar.value()>=bar.maximum()-4;position=bar.value()
                                cursor=G.QTextCursor(widget.document());cursor.movePosition(G.QTextCursor.MoveOperation.End);cursor.insertText(value[len(previous):])
                                if follow:bar.setValue(bar.maximum())
                                else:bar.setValue(position)
                            else:widget.setPlainText(value)
            def write_text():
                if widget.isReadOnly():return
                value=widget.toPlainText();source.delete('1.0','end');source.insert('1.0',value);widget._last_text=value
            widget.textChanged.connect(write_text);self.syncers.append((widget,read_text));read_text()
        elif cls=='Treeview' and hasattr(source,'records'):
            widget=W.QTableView();model=TableModel(source,widget);widget.setModel(model);self.models.append((widget,model));model.sync()
            widget.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
            widget.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
            widget.setVerticalScrollMode(W.QAbstractItemView.ScrollMode.ScrollPerPixel);widget.setAlternatingRowColors(True)
            widget.setMinimumHeight(230);widget.verticalHeader().hide();widget.verticalHeader().setDefaultSectionSize(32)
            widget.horizontalHeader().setStretchLastSection(True);widget._smooth=SmoothWheel(widget)
            for n,col in enumerate(model.columns):widget.setColumnWidth(n,int(source.column(col,'width')))
            def selected(*_):
                visible=set(model.ids);source.chosen.difference_update(visible)
                source.chosen.update(model.ids[i.row()] for i in widget.selectionModel().selectedRows())
                source._label();self.event(source,'<<TreeviewSelect>>')
            widget.selectionModel().selectionChanged.connect(selected)
            widget.doubleClicked.connect(lambda _index:self.double_click(source))
        elif cls=='Treeview':
            # Diagnostic tables are non-paged, read only, and update only on their page.
            widget=W.QTableWidget();columns=source.tk.splitlist(source.cget('columns'));widget.setColumnCount(len(columns))
            widget.setHorizontalHeaderLabels([str(source.heading(c,'text')) for c in columns]);widget.setMinimumHeight(230)
            cache=[None]
            def simple_table():
                rows=[(i,tuple(source.item(i,'values'))) for i in source.get_children()]
                if rows==cache[0]:return
                cache[0]=rows;widget.setRowCount(len(rows))
                for n,(_,values) in enumerate(rows):
                    for col,value in enumerate(values):widget.setItem(n,col,W.QTableWidgetItem(str(value)))
            self.syncers.append((widget,simple_table))
            def simple_selection():
                if cache[0] is not None:source.selection_set([cache[0][i.row()][0] for i in widget.selectionModel().selectedRows()])
            widget.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
            widget.itemSelectionChanged.connect(simple_selection)
            if hasattr(source,'_external_double_click'):widget.doubleClicked.connect(lambda _:source._external_double_click())
        elif cls=='TNotebook':
            widget=W.QTabWidget()
            for name in source.tabs():
                child=source.nametowidget(name);widget.addTab(self.build(child),str(source.tab(name,'text')))
            widget.currentChanged.connect(lambda n:source.select(source.tabs()[n]))
        elif cls=='TProgressbar':
            widget=W.QProgressBar();widget.setTextVisible(False);widget.setRange(0,0)
        elif cls=='TSeparator':widget=W.QFrame();widget.setFrameShape(W.QFrame.Shape.HLine)
        elif 'Scrollbar' in cls:widget=W.QWidget();widget.hide()
        else:
            self.unsupported.add(cls);raise RuntimeError('Qt页面中存在尚未映射的控件：'+cls+' '+str(source))
        self.nodes[key]=widget
        self.syncers.append((widget,lambda s=source,w=widget:self.state_sync(s,w)))
        return widget
    def double_click(self,source):
        callback=getattr(source,'_external_bindings',{}).get('<Double-1>')
        if callback:
            event=tk.Event();event.widget=source;event.x=event.y=0
            callback(event)
    def disclosure_sync(self,source,toggle,body):
        with C.QSignalBlocker(toggle):toggle.setChecked(source.expanded)
        toggle.setArrowType(C.Qt.ArrowType.DownArrow if source.expanded else C.Qt.ArrowType.RightArrow);body.setVisible(source.expanded)
    def state_sync(self,source,widget):
        if not source.winfo_exists():widget.hide();return
        disabled=source.instate(['disabled']) if hasattr(source,'instate') else str(self.value(source,'state'))=='disabled'
        widget.setEnabled(not disabled)
        # Read-only text remains scrollable and selectable.
        if isinstance(widget,W.QPlainTextEdit):widget.setEnabled(True);widget.setReadOnly(disabled)
        text=self.value(source,'text',None)
        if text is not None and isinstance(widget,(W.QPushButton,W.QCheckBox,W.QRadioButton)) and widget.text()!=str(text):widget.setText(str(text))
    def sync(self):
        for widget,callback in self.syncers:
            if widget.isVisible():
                try:callback()
                except tk.TclError:widget.hide()
        for widget,model in self.models:
            if widget.isVisible():
                model.sync()
                current={model.ids[i.row()] for i in widget.selectionModel().selectedRows()}
                wanted=set(model.ids)&model.source.chosen
                if current!=wanted:
                    with C.QSignalBlocker(widget.selectionModel()):
                        widget.clearSelection()
                        for n,i in enumerate(model.ids):
                            if i in wanted:widget.selectionModel().select(model.index(n,0),C.QItemSelectionModel.SelectionFlag.Select|C.QItemSelectionModel.SelectionFlag.Rows)
        for source,target,children in tuple(self.containers):
            if not target.isVisible():continue
            if not source.winfo_exists():target.hide();continue
            packed=source.pack_slaves();all_children=source.winfo_children()
            ordered=list(packed)+[c for c in all_children if c not in packed] if packed else all_children
            current=tuple(str(c) for c in ordered if 'Scrollbar' not in c.winfo_class())
            if current!=children:
                for removed in set(children)-set(current):
                    if removed in self.nodes:self.nodes[removed].hide()
                self.container(source,target)
            for child in source.winfo_children():
                widget=self.nodes.get(str(child))
                if widget is None and 'Scrollbar' not in child.winfo_class():
                    widget=self.build(child)
                    layout=target.layout()
                    if isinstance(layout,W.QGridLayout):
                        info=child.grid_info();layout.addWidget(widget,int(info.get('row',layout.rowCount())),int(info.get('column',0)))
                    else:layout.addWidget(widget)
                if widget:widget.setVisible(bool(child.winfo_manager()))

class Workbench(W.QMainWindow):
    def __init__(self,controller,*,autopump=True):
        super().__init__();configure_fonts();self.controller=controller;self.presenter=Presenter(controller);self.pages={};self.current='main';self.closed=False
        controller._qt_parent=self
        from editor_ui_broker import EditorBroker
        from qt_controller_timers import ControllerTimers
        self.editors=EditorBroker(self)
        self.controller_timers=ControllerTimers(self)
        self.setWindowTitle('整段组合器 · Qt剪辑工作台');self.resize(1440,920);self.setMinimumSize(1060,700)
        self.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True)
        from qt_dialogs import DialogBridge
        self.dialogs=DialogBridge(self)
        self.setStyleSheet(STYLE);center=W.QWidget();self.setCentralWidget(center);layout=W.QHBoxLayout(center);layout.setContentsMargins(16,16,16,12);layout.setSpacing(20)
        side=W.QWidget();side.setFixedWidth(180);nav=W.QVBoxLayout(side)
        brand=W.QLabel('整段组合器');brand.setObjectName('brand');nav.addWidget(brand);nav.addWidget(W.QLabel('批量剪辑工作台'));nav.addSpacing(20)
        self.buttons={}
        for key,row in controller.navigation.items():
            button=W.QPushButton(row['title']);button.setProperty('nav',True);button.setCheckable(True)
            button.clicked.connect(lambda _=False,k=key:self.navigate(k));nav.addWidget(button);self.buttons[key]=button
        nav.addStretch();nav.addWidget(W.QLabel('原素材保留\n方案与生成独立运行'));layout.addWidget(side)
        right=W.QVBoxLayout();layout.addLayout(right,1);self.title=W.QLabel();self.title.setObjectName('pageTitle');self.note=W.QLabel();right.addWidget(self.title);right.addWidget(self.note)
        self.stack=W.QStackedWidget();right.addWidget(self.stack,1)
        self.footer=self.presenter.build(controller.main_footer);right.addWidget(self.footer)
        controller.log_tab._external_visible=False
        self.navigate('main');self.timer=C.QTimer(self);self.timer.setInterval(16);self.timer.timeout.connect(self.tick)
        self.sync_clock=0;self.pump_samples=[];self._ticking=False;self._last_tk_error=('',0.)
        if autopump:self.timer.start()
    def navigate(self,key):
        self.current=key;row=self.controller.navigation[key]
        if key not in self.pages:
            sources={'main':self.controller.main_content,'voice':self.controller.voice_content,'env':self.controller.settings_content,'render':self.controller.render_content}
            content=self.presenter.build(sources.get(key,row['frame']));area=W.QScrollArea();area.setWidgetResizable(True);area.setFrameShape(W.QFrame.Shape.NoFrame);area.setWidget(content);area._smooth=SmoothWheel(area)
            self.pages[key]=area;self.stack.addWidget(area)
        self.stack.setCurrentWidget(self.pages[key]);self.title.setText(row['title']);self.note.setText(row['note'])
        for k,button in self.buttons.items():button.setChecked(k==key)
        self.controller.log_tab._external_visible=key=='logs';self.controller.tabs.select(row['frame'])
        self.controller.nav_var.set(key);self.controller.page_title_var.set(row['title']);self.controller.page_note_var.set(row['note']);self.presenter.sync()
    def tick(self):
        if self.closed or self._ticking or getattr(self.controller,'_qt_callback_depth',0):return
        if getattr(self.controller,'_destroyed',False):
            self.closed=True;self.timer.stop();self.close();return
        self._ticking=True
        start=time.perf_counter();deadline=start+.006
        try:
            # Hidden Tcl provides state/commands only. Qt owns callbacks and
            # timers; visible Tk editors have a separate single-toolkit host.
            if not self.controller.winfo_exists():self.closed=True;self.timer.stop();self.close();return
            selected=self.controller.tabs.select()
            key=next((k for k,row in self.controller.navigation.items() if str(row['frame'])==selected),self.current)
            if key!=self.current:self.navigate(key)
            if time.perf_counter()-self.sync_clock>.1:self.presenter.sync();self.sync_clock=time.perf_counter()
        except tk.TclError as exc:
            try:alive=bool(self.controller.winfo_exists())
            except tk.TclError:alive=False
            if not alive:self.closed=True;self.timer.stop();self.close()
            elif str(exc)!=self._last_tk_error[0] or time.perf_counter()-self._last_tk_error[1]>5:
                self._last_tk_error=(str(exc),time.perf_counter())
                import runtime_diagnostics as diag
                diag.emit('qt_child_callback_error',str(exc),'ERROR',component='qt_workbench')
                try:self.controller.log('子窗口刷新异常，主工作台保持打开：'+str(exc))
                except tk.TclError:pass
        finally:self._ticking=False
        self.pump_samples.append((time.perf_counter()-start)*1000)
        if len(self.pump_samples)>1000:del self.pump_samples[:500]
    def closeEvent(self,event):
        # Tk's Windows event source can dispatch this native event inside
        # dooneevent. Complete Qt shutdown after that interpreter call returns.
        if self._ticking:
            event.ignore()
            if not getattr(self,'_close_deferred',False):
                self._close_deferred=True;C.QTimer.singleShot(10,self.close)
            return
        self._close_deferred=False
        if self.closed:self.controller_timers.close();self.dialogs.close();event.accept();return
        if self.editors.handles:
            event.ignore()
            if not getattr(self,'_waiting_editors',False):
                self._waiting_editors=True;self.editors.request_close();C.QTimer.singleShot(100,self._close_after_editors)
            return
        self.controller.on_close()
        try:exists=self.controller.winfo_exists()
        except tk.TclError:exists=False
        if exists:event.ignore()
        else:self.closed=True;self.timer.stop();self.controller_timers.close();self.dialogs.close();event.accept()

    def _close_after_editors(self):
        self.editors.poll()
        if self.editors.close_was_cancelled():self._waiting_editors=False;return
        if self.editors.handles:C.QTimer.singleShot(100,self._close_after_editors)
        else:self._waiting_editors=False;self.close()

def run(data_dir):
    import faulthandler,uuid
    fault_root=Path(data_dir)/'native_faults';fault_root.mkdir(parents=True,exist_ok=True)
    fault=(fault_root/(uuid.uuid4().hex+'.log')).open('a',encoding='utf-8');faulthandler.enable(fault,all_threads=True)
    application=W.QApplication.instance() or W.QApplication([]);application.setStyle('Fusion');application.setFont(G.QFont('Microsoft YaHei UI',10))
    from app import App
    controller=App(data_dir,presentation_only=True)
    try:
        window=Workbench(controller);window.show();application.exec()
    except Exception:
        controller.destroy();raise
    finally:
        faulthandler.disable();fault.close()
