"""Shared output controls, presented natively by the existing workbench."""
import tkinter as tk
from tkinter import ttk
from visual_layout import LABELS


class OutputStyleControls:
    def build_output_style_controls(self, parent):
        row = ttk.Frame(parent); row.pack(fill='x', padx=5, pady=5)
        ttk.Label(row, text='本批剪辑方式').pack(side='left')
        current = self.cfg.get('visual_layout', 'normal')
        self.visual_layout_var = tk.StringVar(value=next(k for k,v in LABELS.items() if v==current))
        self.visual_layout_control = ttk.Combobox(row, textvariable=self.visual_layout_var,
            values=tuple(LABELS), state='readonly', width=14)
        self.visual_layout_control.pack(side='left', padx=8)
        ttk.Label(row, text='全商品通用；整批统一。对叠为同一画面上下同步播放。').pack(side='left', padx=8)
        editorial=ttk.Frame(parent);editorial.pack(fill='x',padx=5,pady=5)
        self.editorial_enabled_var=tk.BooleanVar(value=self.cfg.get('editorial_enabled',True))
        ttk.Checkbutton(editorial,text='口播匹配画面 / 动作与停顿优先',variable=self.editorial_enabled_var).pack(side='left')
        from editorial_ui import open_editor
        ttk.Button(editorial,text='画面标签 / 动作范围',command=lambda:self.guard(lambda:open_editor(self))).pack(side='left',padx=8)
        from edit_style import LABELS as STYLES
        self.edit_style_var=tk.StringVar(value=next(k for k,v in STYLES.items() if v==self.cfg.get('edit_style','plain')))
        ttk.Label(editorial,text='剪辑风格').pack(side='left',padx=8)
        ttk.Combobox(editorial,textvariable=self.edit_style_var,values=tuple(STYLES),state='readonly',width=17).pack(side='left')
        self.edit_style_audio_var=tk.BooleanVar(value=self.cfg.get('edit_style_audio',True))
        ttk.Checkbutton(editorial,text='风格提示音',variable=self.edit_style_audio_var).pack(side='left',padx=6)
        self.edit_style_push_var=tk.BooleanVar(value=self.cfg.get('edit_style_push',True))
        ttk.Checkbutton(editorial,text='风格轻推近',variable=self.edit_style_push_var).pack(side='left',padx=6)

    def build_voice_speed_controls(self, parent):
        row=ttk.LabelFrame(parent, text='配音语速 · 只加速选中的音色'); row.pack(fill='x', padx=8, pady=6)
        self.voice_speed_enabled_var=tk.BooleanVar(value=self.cfg.get('voice_speed_enabled',False))
        self.voice_speed_factor_var=tk.StringVar(value=str(self.cfg.get('voice_speed_factor',1.15)))
        self.voice_speed_selected=list(self.cfg.get('voice_speed_selected',[]))
        ttk.Checkbutton(row,text='启用配音加速',variable=self.voice_speed_enabled_var).pack(side='left',padx=5,pady=5)
        ttk.Label(row,text='倍速（1～2）').pack(side='left',padx=5)
        ttk.Entry(row,textvariable=self.voice_speed_factor_var,width=7).pack(side='left')
        ttk.Button(row,text='选择要加速的音色',command=lambda:self.guard(self.select_speed_voices)).pack(side='left',padx=8)
        self.voice_speed_summary=tk.StringVar();ttk.Label(row,textvariable=self.voice_speed_summary,wraplength=760).pack(side='left',padx=5)
        self.update_speed_summary()
        pitch=ttk.Frame(parent);pitch.pack(fill='x',padx=8,pady=5)
        ttk.Label(pitch,text='配音音调（半音，-6～+6；0原调）').pack(side='left')
        self.voice_pitch_var=tk.StringVar(value=str(self.cfg.get('voice_pitch_semitones',0)))
        ttk.Entry(pitch,textvariable=self.voice_pitch_var,width=7).pack(side='left',padx=8)
        ttk.Label(pitch,text='正数变尖，负数变沉；不改变语速。音量仍在上方单独调整。').pack(side='left')

    def update_speed_summary(self):
        names=[k.split(':',1)[1] for k in self.voice_speed_selected]
        self.voice_speed_summary.set('已选：'+('、'.join(names) if names else '无；全部原速'))

    def sync_output_style(self):
        from edit_style import LABELS as STYLES
        cfg=self.cfg
        self.visual_layout_var.set(next(k for k,v in LABELS.items() if v==cfg.get('visual_layout','normal')))
        self.voice_speed_enabled_var.set(cfg.get('voice_speed_enabled',False))
        self.voice_speed_factor_var.set(str(cfg.get('voice_speed_factor',1.15)))
        self.voice_speed_selected=list(cfg.get('voice_speed_selected',[]));self.update_speed_summary()
        self.voice_pitch_var.set(str(cfg.get('voice_pitch_semitones',0)))
        self.editorial_enabled_var.set(cfg.get('editorial_enabled',True))
        self.edit_style_var.set(next(k for k,v in STYLES.items() if v==cfg.get('edit_style','plain')))
        self.edit_style_audio_var.set(cfg.get('edit_style_audio',True))
        self.edit_style_push_var.set(cfg.get('edit_style_push',True))

    def collect_output_style(self,cfg):
        from visual_layout import REVISION
        from voice_speed import REVISION as RATE,settings
        cfg.update(visual_layout_revision=REVISION,visual_layout=LABELS[self.visual_layout_var.get()],
                   voice_speed_revision=RATE,voice_speed_enabled=bool(self.voice_speed_enabled_var.get()),
                   voice_speed_factor=float(self.voice_speed_factor_var.get()),voice_speed_selected=list(self.voice_speed_selected))
        cfg['voice_pitch_semitones']=float(self.voice_pitch_var.get())
        cfg['editorial_enabled']=bool(self.editorial_enabled_var.get())
        cfg['review_first_enabled']=bool(self.review_first_var.get())
        from edit_style import LABELS as STYLES
        cfg['edit_style']=STYLES[self.edit_style_var.get()];cfg['edit_style_audio']=bool(self.edit_style_audio_var.get())
        cfg['edit_style_push']=bool(self.edit_style_push_var.get())
        settings(cfg)
        return cfg

    def speed_voice_choices(self):
        from external_voice_catalog import voice_name
        choices={}
        for r in self.script_library.external.versions():
            name=voice_name(r)
            if name and r.get('enabled') and not r.get('script_deleted'):
                choices['external:'+name]='外部配音 · '+name
            elif r.get('id') and r.get('enabled') and not r.get('script_deleted'):
                choices['external:version:'+r['id']]='外部配音 · '+(r.get('name') or r['id'][:8])+'（单个绑定）'
        for p in self.voice_profiles:
            choices['local:'+p['voice_id']]='本地音色 · '+p.get('display_name',p['voice_id'])
        for key in self.voice_speed_selected: choices.setdefault(key,key.split(':',1)[1])
        return dict(sorted(choices.items(),key=lambda pair:pair[1]))

    def select_speed_voices(self):
        choices=self.speed_voice_choices()
        if getattr(self,'_qt_parent',None) is not None:
            from qt_runtime import activate
            activate()
            from PySide6 import QtWidgets as W,QtCore as C
            dialog=W.QDialog(self._qt_parent);dialog.setWindowTitle('选择要加速的音色');dialog.resize(500,620)
            layout=W.QVBoxLayout(dialog)
            layout.addWidget(W.QLabel('勾选的音色按设置倍速播放；其他音色保持原速。'))
            search=W.QLineEdit();search.setPlaceholderText('搜索音色');layout.addWidget(search)
            listing=W.QListWidget();layout.addWidget(listing);items={}
            for key,label in choices.items():
                it=W.QListWidgetItem(label,listing);it.setFlags(it.flags()|C.Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(C.Qt.CheckState.Checked if key in self.voice_speed_selected else C.Qt.CheckState.Unchecked);items[key]=it
            search.textChanged.connect(lambda text:[it.setHidden(text.casefold() not in it.text().casefold()) for it in items.values()])
            buttons=W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Ok|W.QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
            if dialog.exec()!=W.QDialog.DialogCode.Accepted:return
            self.voice_speed_selected=[key for key,it in items.items() if it.checkState()==C.Qt.CheckState.Checked]
            self.update_speed_summary()
        else:
            win=tk.Toplevel(self);win.title('选择要加速的音色');flags={}
            for key,label in choices.items():
                flags[key]=tk.BooleanVar(value=key in self.voice_speed_selected)
                ttk.Checkbutton(win,text=label,variable=flags[key]).pack(anchor='w')
            def save():
                self.voice_speed_selected=[key for key,var in flags.items() if var.get()]
                self.update_speed_summary();win.destroy()
            ttk.Button(win,text='确定',command=save).pack()
