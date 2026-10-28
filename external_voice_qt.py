"""Native voice selection and ID pairing over the existing library/worker lanes."""
from mac_paths import path as _portable_path
import queue
from pathlib import Path
from qt_workbench import C,W
from editing_profiles import NAMES


class ExternalVoiceSelectorDialog(W.QDialog):
    def __init__(self,owner,catalog,selected,apply):
        super().__init__(owner._qt_parent);self.closed=False;self.apply_callback=apply
        self.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True);self.setWindowTitle('外部配音 · 选择音色');self.resize(620,640)
        body=W.QVBoxLayout(self)
        note=W.QLabel('从“文案ID+音色”文件名识别音色；每条组合只用本篇绑定的配音。');note.setWordWrap(True);body.addWidget(note)
        self.all_voices=W.QCheckBox('使用全部有效绑定（原有方式）');self.all_voices.setChecked(selected is None);body.addWidget(self.all_voices)
        panel=W.QWidget();items=W.QVBoxLayout(panel);self.checks={}
        for name in sorted(set(catalog)|set(selected or [])):
            counts=catalog.get(name,dict(valid=0,bound=0));check=W.QCheckBox(f'{name}  ·  有效 {counts["valid"]} / 绑定 {counts["bound"]}')
            check.setChecked(selected is None or name in selected);self.checks[name]=check;items.addWidget(check)
        items.addStretch();scroll=W.QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(panel);body.addWidget(scroll,1)
        note=W.QLabel('取消“全部”后可多选。设置随当前剪辑方案保存，已冻结批次保留原声音。');note.setWordWrap(True);body.addWidget(note)
        self.error=W.QLabel();self.error.setWordWrap(True);body.addWidget(self.error)
        bar=W.QHBoxLayout();body.addLayout(bar)
        for label,action in [('全选',lambda:self.choose_all(True)),('清空',lambda:self.choose_all(False)),('取消',self.close),('应用',self.apply_selection)]:
            button=W.QPushButton(label);button.clicked.connect(lambda _=False,fn=action:fn());bar.addWidget(button)
        self.all_voices.toggled.connect(self.sync);self.sync()
    def sync(self):
        for check in self.checks.values():check.setEnabled(not self.all_voices.isChecked())
    def choose_all(self,value):
        self.all_voices.setChecked(False)
        for check in self.checks.values():check.setChecked(value)
        self.sync()
    def apply_selection(self):
        selected=None if self.all_voices.isChecked() else [name for name,check in self.checks.items() if check.isChecked()]
        if selected==[]:self.error.setText('至少勾选一个音色，或使用全部有效绑定。');return
        try:self.apply_callback(selected)
        except Exception as exc:self.error.setText(str(exc));return
        self.close()
    def closeEvent(self,event):self.closed=True;event.accept()


class ExternalPairingDialog(W.QDialog):
    def __init__(self,app):
        super().__init__(app._qt_parent);self.app=app;self.library=app.script_library;self.profile=app.cfg['editing_profile_id'];self.spec=app.cfg.get('product_spec_id','default')
        self.closed=False;self.busy=False;self.close_requested=False;self.last_result=None;self.messages=queue.Queue();self.lane='script_io:qt_pair:'+str(id(self))
        self.tools_config=dict(app.cfg);self.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True);self.setWindowTitle('文案ID一键配对配音库');self.resize(850,600)
        body=W.QVBoxLayout(self);note=W.QLabel(f'当前库：{self.library.catalog.categories()[self.profile]} / {self.library.catalog.get(self.profile,self.spec)['name']}。按已保存文案ID匹配同名目录，已有绑定跳过，新声音仍需复核。');note.setWordWrap(True);body.addWidget(note)
        bar=W.QHBoxLayout();body.addLayout(bar);self.directory=W.QLineEdit(app.cfg.get('external_voice_library_root') or _portable_path(r'D:\DY video\配音库'));bar.addWidget(self.directory,1)
        self.browse_button=W.QPushButton('选择配音库');self.browse_button.clicked.connect(self.browse);bar.addWidget(self.browse_button)
        self.status=W.QLabel('点击“开始配对”，进度和结果会显示在这里。');self.status.setWordWrap(True);body.addWidget(self.status)
        self.details=W.QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumBlockCount(3000);body.addWidget(self.details,1)
        actions=W.QHBoxLayout();body.addLayout(actions);self.start_button=W.QPushButton('开始配对');self.start_button.clicked.connect(self.start);actions.addWidget(self.start_button)
        self.cancel_button=W.QPushButton('取消后台操作');self.cancel_button.clicked.connect(self.cancel);self.cancel_button.setEnabled(False);actions.addWidget(self.cancel_button)
        close=W.QPushButton('关闭');close.clicked.connect(self.close);actions.addWidget(close)
        self.timer=C.QTimer(self);self.timer.setInterval(100);self.timer.timeout.connect(self.poll);self.timer.start()
    def browse(self):
        path=W.QFileDialog.getExistingDirectory(self,'选择包含文案ID文件夹的配音库',self.directory.text())
        if path:self.directory.setText(path)
    def controls(self):
        for widget in (self.start_button,self.browse_button,self.directory):widget.setEnabled(not self.busy)
        self.cancel_button.setEnabled(self.busy)
    def start(self):
        if self.busy or self.closed:return
        try:
            directory=self.directory.text().strip()
            if not Path(directory).is_dir():raise ValueError('配音库目录不存在，请选择包含文案ID文件夹的目录。')
            from core import resolve_tool
            ffmpeg=resolve_tool('ffmpeg',self.tools_config.get('ffmpeg',''));probe=resolve_tool('ffprobe',self.tools_config.get('ffprobe',''))
            self.busy=True;self.controls();self.details.clear();self.status.setText('后台配对中，可继续操作主工作台。新声音不会自动确认。')
            def worker():
                return self.library.external.pair_by_id(directory,self.profile,ffmpeg,probe,spec_id=self.spec,progress=lambda text:self.messages.put(str(text)),cancel=self.app.lanes.cancel_event(self.lane))
            self.app.lanes.submit(self.lane,worker,self.finish,on_error=self.failed)
        except Exception as exc:self.failed(exc)
    def poll(self):
        latest=None
        for _ in range(100):
            try:latest=self.messages.get_nowait()
            except queue.Empty:break
        if latest is not None and self.busy:self.status.setText(latest)
    def finish(self,result):
        self.poll();self.last_result=result;self.busy=False;self.controls()
        text=f'ID配对完成：匹配{result["matched_scripts"]}/{result["script_count"]}篇，新增{result["bound"]}，已有跳过{result["duplicate"]}，失败{result["failed"]}，目录/ID问题{len(result["issues"])}。新声音请复核。'
        if result['cancelled']:text+='已取消，已完成绑定保留，可再次续配。'
        self.status.setText(text);self.details.setPlainText('\n'.join(map(str,result['issues']+[dict(path=row['path'],error=row.get('error')) for row in result['rows'] if row['status']=='failed'])))
        self.app.log(text)
        if self.close_requested:self.close()
    def failed(self,exc):
        self.busy=False;self.controls();self.status.setText('配对失败：'+str(exc));self.details.setPlainText(str(exc))
        if self.close_requested:self.close()
    def cancel(self):
        if self.busy:self.app.lanes.cancel(self.lane);self.status.setText('正在取消；已完成绑定保留，稍后可继续配对。')
    def closeEvent(self,event):
        if self.busy:self.close_requested=True;self.cancel();event.ignore();return
        self.closed=True;self.timer.stop();event.accept()
