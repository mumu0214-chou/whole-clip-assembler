"""Native Qt top-copy editor; the same measured bitmap is used at export."""
from mac_paths import path as _portable_path
from io import BytesIO
from pathlib import Path
from copy import deepcopy
from qt_runtime import activate
activate()
from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
import top_text_styles as styles


class Preview(W.QWidget):
    moved=C.Signal(float,float)
    def __init__(self,width,height,parent=None):
        super().__init__(parent);self.canvas_w=width;self.canvas_h=height
        self.layer=None;self.background=None;self.geometry_data=None;self.dragging=False
        self.guides=(False,False);self.stacked=False;self.setMinimumSize(280,360)
        self.setCursor(C.Qt.CursorShape.OpenHandCursor)
    def viewport(self):
        scale=min((self.width()-20)/self.canvas_w,(self.height()-20)/self.canvas_h)
        return C.QRectF((self.width()-self.canvas_w*scale)/2,(self.height()-self.canvas_h*scale)/2,
                        self.canvas_w*scale,self.canvas_h*scale),scale
    def paintEvent(self,event):
        p=G.QPainter(self);p.setRenderHint(G.QPainter.RenderHint.SmoothPixmapTransform)
        area,scale=self.viewport();p.fillRect(area,G.QColor('#263442'))
        if self.background:
            targets=[area]
            if self.stacked:targets=[C.QRectF(area.x(),area.y()+n*area.height()/2,area.width(),area.height()/2) for n in range(2)]
            for target in targets:
                bw,bh=self.background.width(),self.background.height();ratio=target.width()/target.height()
                sw,sh=(bh*ratio,bh) if bw/bh>ratio else (bw,bw/ratio)
                source=C.QRectF((bw-sw)/2,(bh-sh)/2,sw,sh)
                p.drawPixmap(target,self.background,source)
        p.setPen(G.QPen(G.QColor('#708090'),1,C.Qt.PenStyle.DashLine))
        p.drawRect(area.adjusted(area.width()*.025,area.height()*.025,-area.width()*.025,-area.height()*.025))
        if self.layer and self.geometry_data:
            d=self.geometry_data;dest=C.QRectF(area.x()+d['left']*scale,area.y()+d['top']*scale,
                self.layer.width()*scale,self.layer.height()*scale)
            p.drawPixmap(dest.toRect(),self.layer);p.setPen(G.QPen(G.QColor('#5eead4'),1));p.drawRect(dest)
        p.setPen(G.QPen(G.QColor('#facc15'),1,C.Qt.PenStyle.DashLine))
        if self.guides[0]:p.drawLine(C.QPointF(area.center().x(),area.top()),C.QPointF(area.center().x(),area.bottom()))
        if self.guides[1]:p.drawLine(C.QPointF(area.left(),area.center().y()),C.QPointF(area.right(),area.center().y()))
    def mousePressEvent(self,event):
        if event.button()!=C.Qt.MouseButton.LeftButton or not self.layer:return
        area,scale=self.viewport();d=self.geometry_data
        x=(event.position().x()-area.x())/scale;y=(event.position().y()-area.y())/scale
        if d['left']<=x<=d['left']+self.layer.width() and d['top']<=y<=d['top']+self.layer.height():
            self.offset=(x-(d['left']+self.layer.width()/2),y-(d['top']+self.layer.height()/2))
            self.dragging=True;self.setCursor(C.Qt.CursorShape.ClosedHandCursor)
    def mouseMoveEvent(self,event):
        if not self.dragging:return
        area,scale=self.viewport();dx,dy=self.offset
        self.moved.emit(((event.position().x()-area.x())/scale-dx)/self.canvas_w,
                        ((event.position().y()-area.y())/scale-dy)/self.canvas_h)
    def mouseReleaseEvent(self,event):
        self.dragging=False;self.guides=(False,False);self.setCursor(C.Qt.CursorShape.OpenHandCursor);self.update()


class TopTextEditor(W.QDialog):
    def __init__(self,cfg,texts,apply,parent=None):
        super().__init__(parent);self.setWindowTitle('顶部文案 · 字体样式与可视化位置')
        self.setWindowModality(C.Qt.WindowModality.WindowModal);self.resize(1040,820)
        self.style=styles.from_config(cfg);self.apply_callback=apply
        self.canvas_w=int(cfg.get('width',1080));self.canvas_h=int(cfg.get('height',1920));self.metrics=None
        self.reference_process=None;self.ffmpeg=cfg.get('ffmpeg','ffmpeg');self._cache=None
        root=W.QHBoxLayout(self);left=W.QVBoxLayout();root.addLayout(left,1)
        self.preset=W.QComboBox()
        for key,row in styles.PRESETS.items():self.preset.addItem(row[0],key)
        self.preset.setCurrentIndex(list(styles.PRESETS).index(self.style['preset']))
        left.addWidget(W.QLabel('字体样式 · 透明底，不铺大块背景'));left.addWidget(self.preset)
        form=W.QFormLayout();left.addLayout(form);self.fields={}
        for key,title,low,high,step in [('font_size','字号（1080×1920基准）',28,140,1),
            ('max_width','文案最大宽度',.35,.95,.01),('stroke','细描边',0,8,1),
            ('x','横向位置（0～1）',0,1,.01),('y','纵向位置（0～1）',0,1,.01)]:
            spin=W.QDoubleSpinBox();spin.setRange(low,high);spin.setSingleStep(step)
            spin.setDecimals(4 if key in ('x','y') else 2 if step<1 else 0);spin.setValue(self.style[key]);self.fields[key]=spin;form.addRow(title,spin)
        self.align=W.QComboBox()
        for name,value in [('居中','center'),('左对齐','left'),('右对齐','right')]:self.align.addItem(name,value)
        self.align.setCurrentIndex(self.align.findData(self.style['align']));form.addRow('段落对齐',self.align)
        self.shadow=W.QCheckBox('轻阴影（提升复杂背景上的可读性）');self.shadow.setChecked(self.style['shadow']);left.addWidget(self.shadow)
        self.snap=W.QCheckBox('拖动时识别并吸附横、纵中线');self.snap.setChecked(True);left.addWidget(self.snap)
        positions=W.QHBoxLayout();left.addLayout(positions)
        for title,y in [('上方',.2),('画面居中',.5),('下方',.75)]:
            button=W.QPushButton(title);button.clicked.connect(lambda _,v=y:self.move_to(.5,v,False));positions.addWidget(button)
        self.choice=W.QComboBox();self.texts=texts or ['火锅季真的来了\n鲜切牛肉安排上\n汕头当地发货']
        for n,text in enumerate(self.texts):self.choice.addItem(f'第 {n+1} 条：'+text.replace('\n',' / ')[:26])
        left.addWidget(W.QLabel('预览文案（不修改文案库）'));left.addWidget(self.choice)
        self.text=W.QPlainTextEdit(self.texts[0]);left.addWidget(self.text,1)
        self.note=W.QLabel();self.note.setWordWrap(True);left.addWidget(self.note)
        background=W.QHBoxLayout();left.addLayout(background)
        choose=W.QPushButton('选择参考图片 / 视频');choose.clicked.connect(self.choose_reference);background.addWidget(choose)
        clear=W.QPushButton('清除参考');clear.clicked.connect(self.clear_reference);background.addWidget(clear)
        self.reference_stacked=W.QCheckBox('参考素材按对叠画面显示');self.reference_stacked.setChecked(cfg.get('visual_layout')=='stacked');left.addWidget(self.reference_stacked)
        left.addWidget(W.QLabel('参考画面仅用于摆放；拖动预览中的文字框即可调整位置。'))
        self.status=W.QLabel('只保存顶部文案样式；字幕独立。新方案生效，旧批次保持。');self.status.setWordWrap(True);left.addWidget(self.status)
        actions=W.QHBoxLayout();left.addLayout(actions)
        self.save_button=W.QPushButton('应用并保存到新方案');self.save_button.clicked.connect(self.save);actions.addWidget(self.save_button)
        cancel=W.QPushButton('取消');cancel.clicked.connect(self.reject);actions.addWidget(cancel)
        self.preview=Preview(self.canvas_w,self.canvas_h);self.preview.setMinimumWidth(390);root.addWidget(self.preview,1)
        self.preview.stacked=self.reference_stacked.isChecked()
        self.reference_stacked.toggled.connect(self.set_reference_layout)
        self.preview.moved.connect(lambda x,y:self.move_to(x,y,self.snap.isChecked()))
        self.timer=C.QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(65);self.timer.timeout.connect(self.refresh)
        for field in self.fields.values():field.valueChanged.connect(lambda _:self.timer.start())
        self.preset.currentIndexChanged.connect(self.select_preset);self.align.currentIndexChanged.connect(lambda _:self.timer.start())
        self.shadow.toggled.connect(lambda _:self.timer.start());self.text.textChanged.connect(lambda:self.timer.start())
        self.choice.currentIndexChanged.connect(lambda n:self.text.setPlainText(self.texts[n]))
        from wheel_guard import install_qt
        install_qt(W.QApplication.instance());self.refresh()
    def values(self):
        return styles.normalize(dict(self.style,**{k:v.value() for k,v in self.fields.items()},
            preset=self.preset.currentData(),align=self.align.currentData(),shadow=self.shadow.isChecked()))
    def select_preset(self,_):
        self.fields['stroke'].setValue(styles.PRESETS[self.preset.currentData()][3]);self.timer.start()
    def refresh(self):
        try:
            value=self.values();text=self.text.toPlainText();signature=(text,tuple((k,v) for k,v in value.items() if k not in ('x','y')))
            if signature!=self._cache:
                layer,self.metrics=styles.render(text,value,self.canvas_w,self.canvas_h)
                data=BytesIO();layer.save(data,format='PNG');pix=G.QPixmap();pix.loadFromData(data.getvalue());self.preview.layer=pix;self._cache=signature
            pos=styles.position(value,self.metrics,self.canvas_w,self.canvas_h)
            self.preview.geometry_data=pos;self.preview.update();self.save_button.setEnabled(True)
            self.note.setText('预览与导出共用相同字体与排版。'+(' 此条较长，已自动缩小以保持完整。' if self.metrics['auto_reduced'] else '')+
                ' 每条文案按实际大小保留安全边距。')
        except (ValueError,OSError) as exc:
            self.note.setText(str(exc));self.save_button.setEnabled(False);self.preview.layer=None;self.preview.update()
    def move_to(self,x,y,snap):
        if self.metrics is None:return
        pos=styles.position(self.values(),self.metrics,self.canvas_w,self.canvas_h,x,y,snap)
        for key in ('x','y'):
            self.fields[key].blockSignals(True);self.fields[key].setValue(pos[key]);self.fields[key].blockSignals(False)
        self.refresh();self.preview.guides=(pos['vertical'],pos['horizontal']);self.preview.update()
    def save(self):
        self.refresh()
        if not self.save_button.isEnabled():return
        try:self.apply_callback(deepcopy(self.values()))
        except Exception as exc:W.QMessageBox.warning(self,'未保存',str(exc));return
        self.accept()
    def clear_reference(self):
        self.stop_reference();self.preview.background=None;self.preview.update()
    def set_reference_layout(self,stacked):
        self.preview.stacked=stacked;self.preview.update()
    def choose_reference(self):
        path,_=W.QFileDialog.getOpenFileName(self,'选择参考画面',_portable_path(r'D:\DY video'),'图片或视频 (*.png *.jpg *.jpeg *.webp *.mp4 *.mov *.mkv)')
        if not path:return
        self.stop_reference()
        if Path(path).suffix.lower() in ('.png','.jpg','.jpeg','.webp'):
            pix=G.QPixmap(path)
            if pix.isNull():self.status.setText('无法读取参考图片。');return
            self.preview.background=pix;self.preview.update();return
        # A separate process avoids blocking Qt while decoding a reference frame.
        from core import resolve_tool
        try:executable=resolve_tool('ffmpeg',self.ffmpeg)
        except Exception as exc:self.status.setText(str(exc));return
        process=C.QProcess(self);self.reference_process=process
        process.finished.connect(lambda code,_:self.reference_ready(process,code))
        process.errorOccurred.connect(lambda _:self.status.setText('参考视频读取失败；仍可直接调整文字。'))
        process.start(str(executable),['-v','error','-i',path,'-frames:v','1','-vf','scale=720:-1','-f','image2pipe','-vcodec','png','pipe:1'])
        self.status.setText('正在读取参考视频首帧…')
        C.QTimer.singleShot(15000,lambda:self.reference_timeout(process))
    def reference_ready(self,process,code):
        if process is not self.reference_process:return
        pix=G.QPixmap();pix.loadFromData(bytes(process.readAllStandardOutput()))
        if code==0 and not pix.isNull():
            self.preview.background=pix;self.preview.update();self.status.setText('参考画面已载入，仅用于摆放。')
        else:self.status.setText('参考画面读取失败，请换一张图片或视频。')
        self.reference_process=None;process.deleteLater()
    def reference_timeout(self,process):
        if process is self.reference_process:
            self.stop_reference();self.status.setText('读取参考画面超时，可换一张图片。')
    def stop_reference(self):
        process=self.reference_process;self.reference_process=None
        if process is not None:process.kill();process.waitForFinished(1000);process.deleteLater()
    def done(self,result):self.timer.stop();self.stop_reference();super().done(result)


def open_editor(app):
    parent=getattr(app,'_qt_parent',None)
    if parent is None:raise ValueError('请从Qt剪辑工作台打开顶部文案可视化编辑器')
    existing=getattr(app,'top_text_editor',None)
    if existing is not None and existing.isVisible():existing.raise_();existing.activateWindow();return
    cfg=app.collect_config()
    from core import load_top_texts
    path=Path(cfg.get('top_text_library',''))
    texts=load_top_texts(path) if path.is_file() else []
    def apply(value):
        updated=app.collect_config();updated['top_text_style']=value
        app.persist_config(updated);app.cfg=updated
        app.log('顶部文案字体与位置已保存；仅用于新方案，现有批次保持。')
    app.top_text_editor=TopTextEditor(cfg,texts,apply,parent);app.top_text_editor.show()
