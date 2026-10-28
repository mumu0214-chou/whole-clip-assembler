"""Native lightweight popup routes; never create a Tk child in the workbench."""
import json, os
from qt_runtime import activate
activate()
from PySide6 import QtCore as C, QtWidgets as W


class TextWindow(W.QDialog):
    def __init__(self,parent,title,text):
        super().__init__(parent);self.setWindowTitle(title);self.resize(1080,650)
        self.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True);self.alive=True
        layout=W.QVBoxLayout(self);self.text=W.QPlainTextEdit();self.text.setReadOnly(True);self.text.setPlainText(text);layout.addWidget(self.text)
        self.bar=W.QHBoxLayout();layout.addLayout(self.bar)
        self.button('复制详情',lambda:W.QApplication.clipboard().setText(self.text.toPlainText()))
        self.button('关闭',self.close)
        self.show()
    def button(self,label,fn):
        button=W.QPushButton(label);button.clicked.connect(fn);self.bar.addWidget(button);return button
    def winfo_exists(self):return self.alive
    def closeEvent(self,event):self.alive=False;event.accept()
    def insert(self,index,text):
        if self.alive:self.text.appendPlainText(text)
    def get(self,*args):return self.text.toPlainText()


def text_window(app,title,text):return TextWindow(app._qt_parent,title,text)


def timing_detail(app,item):
    win=W.QDialog(app._qt_parent);win.setWindowTitle(f"方案{item['number']}：处理明细（只读）");win.resize(1180,650)
    win.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True);layout=W.QVBoxLayout(win);fps=app.batch['config']['fps']
    label=W.QLabel(f"最终时长 {item['duration']:.3f}秒；转场总重叠 {item.get('transition_overlap_seconds',0):.3f}秒；原素材不修改。");layout.addWidget(label)
    fit=item.get('voice_fit_snapshot')
    if fit:layout.addWidget(W.QLabel(f"配音差额裁切：候选 {fit['candidate_frames']/fps:.3f}秒 → 目标 {fit['target_frames']/fps:.3f}秒；实际减时 {fit['removed_timeline_frames']/fps:.3f}秒。随机位置已冻结。"))
    table=W.QTableWidget(len(item['clips']),11);table.setHorizontalHeaderLabels(['位置','完整相对路径','角色/阶段','互斥组','成员','原长秒','原片采用区间','倍速','输出段长秒','本段之后转场','效果 / 说明'])
    table.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers);layout.addWidget(table)
    types={'none':'无','fade':'叠化','slide_left':'左推','slide_right':'右推','wipe_left':'左擦','wipe_right':'右擦','blur_fade':'模糊叠化'}
    for n,clip in enumerate(item['clips']):
        transition=clip.get('outgoing_transition',dict(type='none',frames=0));effects=clip.get('effects',{});notes=[]
        for key,name in [('mirror','镜像'),('drop','抽帧'),('color','调色'),('zoom','缩放')]:
            if effects.get(key):notes.append(name)
        if clip.get('micro_trim_applied'):notes.append(f"随机微裁{clip['micro_trim_removed_seconds']:g}秒")
        if clip.get('random_duration_applied'):notes.append(f"A/B时长目标 {clip.get('target_playback_seconds') or '可用全段'}秒")
        if clip.get('protection_reason'):notes.append(clip['protection_reason'])
        notes.extend(clip.get('edit_notes',[]))
        row=[n+1,clip.get('relative_path',clip.get('display','')),clip.get('timeline_role','旧方案'),clip.get('mutex_group','旧方案'),clip.get('member_number','—'),f"{clip['duration']:.3f}",f"{clip.get('source_in_seconds',0):.3f}～{clip.get('source_out_seconds',clip['duration']):.3f}",f"{clip.get('playback_rate',1):.3f}",f"{clip['duration_frames']/fps:.3f}",f"{types.get(transition['type'],transition['type'])} {transition['frames']/fps:.3f}秒",'；'.join(notes) or '原样']
        for col,value in enumerate(row):table.setItem(n,col,W.QTableWidgetItem(str(value)))
    table.resizeColumnsToContents();table.horizontalHeader().setStretchLastSection(True)
    bar=W.QHBoxLayout();layout.addLayout(bar)
    button=W.QPushButton('完整方案记录');button.clicked.connect(lambda:text_window(app,'冻结方案记录（只读）',json.dumps(item,ensure_ascii=False,indent=2)));bar.addWidget(button)
    close=W.QPushButton('关闭');close.clicked.connect(win.close);bar.addWidget(close);win.show();return win


def voice_selector(app):
    win=W.QDialog(app._qt_parent);win.setWindowTitle('批量选择音色');win.resize(850,550)
    win.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True);layout=W.QVBoxLayout(win)
    layout.addWidget(W.QLabel('仅选择既有可用音色；审核状态保持。'))
    table=W.QListWidget();table.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection);layout.addWidget(table)
    profile_id=app.cfg['editing_profile_id']
    for profile in app.voice_profiles:
        if not profile.get('available'):continue
        item=W.QListWidgetItem(profile.get('display_name',profile['voice_id']));item.setData(C.Qt.ItemDataRole.UserRole,profile['voice_id']);table.addItem(item)
        if app.voice_checks[profile['voice_id']].get():item.setSelected(True)
    bar=W.QHBoxLayout();layout.addLayout(bar)
    def apply():
        try:
            if app.cfg['editing_profile_id']!=profile_id:raise ValueError('剪辑方案已切换，请重新打开音色选择窗口。')
            selected={i.data(C.Qt.ItemDataRole.UserRole) for i in table.selectedItems()}
            available={p['voice_id'] for p in app.voice_profiles if p.get('available')}
            if not selected.issubset(available):raise ValueError('音色可用状态已变化，请刷新后重选。')
            for key,var in app.voice_checks.items():var.set(key in selected)
            app._update_video_policy_preview();win.close()
        except Exception as exc:W.QMessageBox.warning(win,'音色选择',str(exc))
    for label,fn in [('全选',table.selectAll),('取消选择',table.clearSelection),('应用所选音色',apply),('关闭',win.close)]:
        button=W.QPushButton(label);button.clicked.connect(fn);bar.addWidget(button)
    win.show();return win


def log_detail(panel,data):
    app=panel.app;win=text_window(app,'任务详情 / 完整异常',json.dumps(data,ensure_ascii=False,indent=2))
    win.button('打开日志目录',lambda:__import__('app').open_local(str(__import__('runtime_diagnostics').hub().root/data.get('run_id',''))))
    def jump():
        job=data.get('job_id','')
        if app.plan_tree.exists(job):app.tabs.select(app.main_tab);app.plan_tree.selection_set(job);app.plan_tree.see(job)
        else:panel.status.set('该任务不在当前批次，请打开对应历史Manifest。')
    win.button('跳转当前批次任务',jump);return win,win


def log_settings(panel):
    import runtime_diagnostics as diag
    app=panel.app;hub=diag.hub();win=W.QDialog(app._qt_parent);win.setWindowTitle('日志设置')
    win.setAttribute(C.Qt.WidgetAttribute.WA_DeleteOnClose,True);form=W.QFormLayout(win);values={}
    for key,label,current in [('retention_days','保留天数',hub.retention_days),('file_mb','单文件MiB',hub.file_bytes/1024**2),('total_mb','总空间MiB',hub.total_bytes/1024**2)]:
        field=W.QDoubleSpinBox();field.setRange(.001,100000);field.setDecimals(3);field.setValue(current);form.addRow(label,field);values[key]=field
    debug=W.QCheckBox('记录DEBUG（默认关闭）');debug.setChecked(hub.debug);form.addRow(debug)
    def save():
        try:
            data={k:v.value() for k,v in values.items()}
            cfg=app.collect_config();cfg['diagnostic_settings']=dict(data,debug=debug.isChecked());app.persist_config(cfg)
            hub.retention_days=data['retention_days'];hub.file_bytes=int(data['file_mb']*1024**2);hub.total_bytes=int(data['total_mb']*1024**2);hub.debug=debug.isChecked();win.close()
        except Exception as exc:W.QMessageBox.warning(win,'日志设置',str(exc))
    button=W.QPushButton('保存');button.clicked.connect(save);form.addRow(button);win.show();panel._qt_log_settings=win;return win
