"""Native sound-effect import and manual clip bindings, with background file work."""
from mac_paths import path as _portable_path
from pathlib import Path
import time,_tkinter
from qt_runtime import activate
activate()
from PySide6 import QtCore as C,QtWidgets as W
from clip_sfx import SFXLibrary,SFXError

class BatchGainDialog(W.QDialog):
    def __init__(self,scopes,gain,default_scope,parent=None):
        super().__init__(parent);self.setWindowTitle('批量修改音效音量');self.resize(560,230)
        layout=W.QVBoxLayout(self)
        form=W.QFormLayout();layout.addLayout(form)
        self.scope=W.QComboBox()
        for label,ids in scopes:self.scope.addItem(f'{label}（{len(ids)}条）',list(ids))
        self.scope.setCurrentIndex(default_scope);form.addRow('修改范围',self.scope)
        self.gain=W.QDoubleSpinBox();self.gain.setRange(-60,12);self.gain.setDecimals(2);self.gain.setSuffix(' dB');self.gain.setValue(gain)
        form.addRow('统一设置为',self.gain)
        self.summary=W.QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        note=W.QLabel('只修改绑定音量，保留起点偏移和播放方式。全部范围包含停用音效；已冻结批次不变。');note.setWordWrap(True);layout.addWidget(note)
        self.buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(W.QDialogButtonBox.StandardButton.Ok).setText('应用音量')
        self.buttons.button(W.QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.scope.currentIndexChanged.connect(self.update_summary);self.gain.valueChanged.connect(self.update_summary);self.update_summary()
    def selected_ids(self):return list(self.scope.currentData() or [])
    def update_summary(self):
        count=len(self.selected_ids());self.summary.setText(f'将把 {count} 条片段音效绑定统一设为 {self.gain.value():.2f} dB。')
        self.buttons.button(W.QDialogButtonBox.StandardButton.Ok).setEnabled(count>0)

class ClipSFXDialog(W.QDialog):
    def __init__(self,controller,parent=None):
        super().__init__(parent);self.controller=controller;self.library=SFXLibrary(controller.data_dir);self.busy=False;self.asset_rows=[];self.binding_rows=[]
        self.setWindowTitle('牛肉丸片段音效 · 手动绑定场景');self.resize(1120,720)
        outer=W.QVBoxLayout(self)
        outer.addWidget(W.QLabel('音效跟随已绑定片段：煮汤 → 煮沸声，掰开 → 爆汁声。音效长就裁断音效，视频长度保持；短音效默认只播放一次。'))
        scene_hint=W.QLabel('画面轮换：爆汁=掰开，沸腾=煮汤，展示=展示，咀嚼声=吃。全部素材参与，四类按整批时长4:3:2:1软平衡，单条可缺类；关闭音效播放仍保留场景分类。')
        scene_hint.setWordWrap(True);outer.addWidget(scene_hint)
        bar=W.QHBoxLayout();outer.addLayout(bar);self.buttons=[]
        for label,command in [('添加音效',self.import_audio),('试听音效',self.listen),('修改名称',self.rename),('启用 / 停用',self.toggle_enabled),('批量修改音量',self.batch_gain),('刷新',self.refresh)]:
            button=W.QPushButton(label);button.clicked.connect(lambda _=False,fn=command:self.guard(fn));bar.addWidget(button);self.buttons.append(button)
        self.assets=self.table(('音效名称','时长(秒)','状态','绑定片段数'));outer.addWidget(self.assets)
        self.assets.itemSelectionChanged.connect(self.refresh_bindings)
        form=W.QHBoxLayout();outer.addLayout(form)
        form.addWidget(W.QLabel('绑定音量'));self.gain=W.QDoubleSpinBox();self.gain.setRange(-60,12);self.gain.setValue(-12);self.gain.setSuffix(' dB');form.addWidget(self.gain)
        form.addWidget(W.QLabel('片段开始后'));self.offset=W.QDoubleSpinBox();self.offset.setRange(0,3600);self.offset.setDecimals(3);self.offset.setSuffix(' 秒');form.addWidget(self.offset)
        form.addWidget(W.QLabel('播放方式'));self.mode=W.QComboBox();self.mode.addItem('播放一次','once');self.mode.addItem('循环铺满片段','loop');form.addWidget(self.mode)
        for label,command in [('选择视频片段并绑定',self.bind_files),('应用到所选绑定',self.apply_options),('移除所选绑定',self.unbind)]:
            button=W.QPushButton(label);button.clicked.connect(lambda _=False,fn=command:self.guard(fn));form.addWidget(button);self.buttons.append(button)
        self.bindings=self.table(('对应视频片段','音量(dB)','起点偏移(秒)','播放方式','文件'));outer.addWidget(self.bindings)
        self.bindings.itemSelectionChanged.connect(self.load_options)
        self.status=W.QLabel('添加音效后选中音效，再绑定对应视频片段。工作台“播放片段音效”控制新方案的播放；关闭仍保留场景分类，已冻结方案保持。');self.status.setWordWrap(True);outer.addWidget(self.status)
        self.refresh()
    def table(self,headers):
        table=W.QTableWidget();table.setColumnCount(len(headers));table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows);table.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
        table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);table.horizontalHeader().setStretchLastSection(True);table.verticalHeader().hide();table.setAlternatingRowColors(True);return table
    def selected_asset(self):
        rows=self.assets.selectionModel().selectedRows()
        if len(rows)!=1:raise SFXError('请选中一个音效。')
        return self.asset_rows[rows[0].row()]
    def selected_assets(self):
        return [self.asset_rows[row.row()] for row in self.assets.selectionModel().selectedRows()]
    def selected_bindings(self):
        return [self.binding_rows[r.row()] for r in self.bindings.selectionModel().selectedRows()]
    def guard(self,fn):
        try:fn()
        except Exception as exc:self.status.setText(str(exc));W.QMessageBox.warning(self,'片段音效',str(exc))
    def refresh(self):
        selected={asset['id'] for asset in self.selected_assets()}
        self.asset_rows=self.library.assets()
        with C.QSignalBlocker(self.assets):
            self.assets.setRowCount(len(self.asset_rows));self.assets.clearSelection()
            for row,asset in enumerate(self.asset_rows):
                for col,value in enumerate((asset['name'],f"{asset['duration']:.3f}",'启用' if asset['enabled'] else '停用',asset['binding_count'])):self.assets.setItem(row,col,W.QTableWidgetItem(str(value)))
                if asset['id'] in selected:self.assets.selectionModel().select(self.assets.model().index(row,0),C.QItemSelectionModel.SelectionFlag.Select|C.QItemSelectionModel.SelectionFlag.Rows)
        self.assets.resizeColumnsToContents();self.refresh_bindings()
    def refresh_bindings(self):
        selected={binding['id'] for binding in self.selected_bindings()}
        try:asset=self.selected_asset()
        except SFXError:asset=None
        self.binding_rows=self.library.bindings(asset['id']) if asset else []
        with C.QSignalBlocker(self.bindings):
            self.bindings.setRowCount(len(self.binding_rows));self.bindings.clearSelection()
            for row,binding in enumerate(self.binding_rows):
                values=(binding['clip_label'],binding['gain_db'],binding['offset_seconds'],'循环铺满' if binding['mode']=='loop' else '播放一次',binding['clip_path'])
                for col,value in enumerate(values):self.bindings.setItem(row,col,W.QTableWidgetItem(str(value)))
                if binding['id'] in selected:self.bindings.selectionModel().select(self.bindings.model().index(row,0),C.QItemSelectionModel.SelectionFlag.Select|C.QItemSelectionModel.SelectionFlag.Rows)
        self.bindings.resizeColumnsToContents()
    def load_options(self):
        rows=self.selected_bindings()
        if len(rows)==1:
            binding=rows[0];self.gain.setValue(binding['gain_db']);self.offset.setValue(binding['offset_seconds']);self.mode.setCurrentIndex(self.mode.findData(binding['mode']))
    def work(self,worker,message):
        if self.busy:raise SFXError('正在处理音效文件，请等待完成。')
        self.busy=True;self.status.setText(message)
        for button in self.buttons:button.setEnabled(False)
        def done(value):
            self.busy=False
            for button in self.buttons:button.setEnabled(True)
            self.refresh();self.status.setText('已完成。仅影响新方案，已冻结批次保持原音效。')
            if isinstance(value,dict) and 'id' in value:
                for n,row in enumerate(self.asset_rows):
                    if row['id']==value['id']:self.assets.selectRow(n);break
        def error(exc):
            self.busy=False
            for button in self.buttons:button.setEnabled(True)
            self.status.setText(str(exc));W.QMessageBox.warning(self,'音效处理失败',str(exc))
        try:self.controller.lanes.submit('clip-sfx',worker,done,on_error=error)
        except Exception:
            self.busy=False
            for button in self.buttons:button.setEnabled(True)
            raise
    def import_audio(self):
        path=W.QFileDialog.getOpenFileName(self,'添加音效',_portable_path(r'D:\DY video'),'音效 (*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus)')[0]
        if not path:return
        name,ok=W.QInputDialog.getText(self,'音效名称','例如：水开煮沸 / 掰开爆汁',text=Path(path).stem)
        if not ok:return
        from core import resolve_tool
        ffmpeg=resolve_tool('ffmpeg',self.controller.cfg['ffmpeg'])
        self.work(lambda:self.library.import_audio(path,name,ffmpeg),'正在导入并校验音效，原文件保留…')
    def bind_files(self):
        from editing_profiles import BALL, ball_profile
        key=self.controller.cfg['editing_profile_id']
        asset=self.selected_asset();cfg=self.controller.profile_state['items'][key if ball_profile(key) else BALL]
        files=W.QFileDialog.getOpenFileNames(self,'选择对应场景的视频片段（可多选）',cfg.get('material_root',_portable_path(r'D:\DY video\人工处理素材\牛肉丸')),'视频片段 (*.mp4 *.mov *.mkv *.m4v *.avi *.webm)')[0]
        if not files:return
        options=dict(gain_db=self.gain.value(),offset_seconds=self.offset.value(),mode=self.mode.currentData())
        from core import probe,resolve_tool
        ffprobe=resolve_tool('ffprobe',self.controller.cfg['ffprobe'])
        def bind():
            clips=[]
            for file in files:
                metadata=probe(Path(file),ffprobe)
                if not metadata.get('has_video'):raise SFXError('所选文件不是有效视频片段：'+file)
                clips.append(dict(path=file,display=Path(file).name))
            self.library.bind(asset['id'],clips,**options)
        self.work(bind,'正在校验并绑定片段；不裁剪视频…')
    def apply_options(self):
        rows=self.selected_bindings()
        if not rows:raise SFXError('请选中需要调整的绑定。')
        self.library.update_options([r['id'] for r in rows],gain_db=self.gain.value(),offset_seconds=self.offset.value(),mode=self.mode.currentData());self.refresh()
    def batch_gain(self):
        if self.busy:raise SFXError('正在处理音效文件，请等待完成。')
        selected_assets={asset['id'] for asset in self.selected_assets()};all_rows=self.library.bindings()
        scopes=[('选中的片段绑定',[r['id'] for r in self.selected_bindings()]),
                ('选中音效的全部绑定',[r['id'] for r in all_rows if r['asset_id'] in selected_assets]),
                ('所有音效的全部绑定',[r['id'] for r in all_rows])]
        if not all_rows:raise SFXError('还没有片段音效绑定，请先绑定视频片段。')
        default_scope=1 if selected_assets else (0 if scopes[0][1] else 2)
        dialog=BatchGainDialog(scopes,self.gain.value(),default_scope,self)
        if dialog.exec()!=W.QDialog.DialogCode.Accepted:return
        gain=dialog.gain.value();count=self.library.update_gain(dialog.selected_ids(),gain)
        self.refresh();self.gain.setValue(gain)
        self.status.setText(f'已批量修改 {count} 条绑定音量为 {gain:.2f} dB；起点偏移/播放方式和已冻结批次保持。')
    def unbind(self):
        rows=self.selected_bindings()
        if not rows:raise SFXError('请选中需要移除的绑定。')
        self.library.unbind([r['id'] for r in rows]);self.refresh()
    def rename(self):
        asset=self.selected_asset();name,ok=W.QInputDialog.getText(self,'修改音效名称','名称',text=asset['name'])
        if ok:self.library.rename(asset['id'],name);self.refresh()
    def toggle_enabled(self):
        asset=self.selected_asset();self.library.set_enabled(asset['id'],not asset['enabled']);self.refresh()
    def listen(self):
        from app import open_local
        open_local(self.selected_asset()['path'])
    def closeEvent(self,event):
        if self.busy:self.status.setText('文件操作仍在进行，完成后可关闭。');event.ignore()
        else:event.accept()

def open_manager(controller):
    parent=getattr(controller,'_qt_parent',None)
    existing=getattr(controller,'clip_sfx_window',None)
    if existing is not None and existing.isVisible():existing.raise_();existing.activateWindow();return existing
    application=W.QApplication.instance() or W.QApplication([])
    dialog=ClipSFXDialog(controller,parent);controller.clip_sfx_window=dialog
    if parent is not None:dialog.setModal(False);dialog.show()
    else:
        # Compatibility Tk main window: Qt's nested dialog loop services Tk timers.
        timer=C.QTimer(dialog)
        def service():
            deadline=time.perf_counter()+.006
            try:
                for _ in range(40):
                    if time.perf_counter()>deadline or not controller.tk.dooneevent(_tkinter.ALL_EVENTS|_tkinter.DONT_WAIT):break
                if not controller.winfo_exists():timer.stop();dialog.reject()
            except Exception:timer.stop();dialog.reject()
        timer.timeout.connect(service);timer.start(16);dialog.exec();timer.stop()
    return dialog
