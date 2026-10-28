# -*- coding: utf-8 -*-
"""Small Voice Profile editor for the current Tk assembler window."""
from __future__ import annotations
from mac_paths import path as _portable_path

from pathlib import Path
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
if os.name == 'nt':
    import winsound

from core import resolve_tool
from voice_pipeline import PROFILE_ROOT, IndexTTS25Provider, VoiceError, discover_profiles
from voice_profile_manager import (FLOAT_FIELDS, INT_FIELDS, PROFILE_DEFAULTS,
                                   disable_profile, preview_profile, save_profile,
                                   record_preview_playback)
from voice_profile_history import (voice_id_locked,profile_history,restore_draft,clone_values,
                                   save_golden,protect_preview,release_preview)
from voice_tuning import PREVIEW_PRESETS, COMPRESSION_PRESETS, filter_capabilities


class VoiceManagerWindow(tk.Toplevel):
    def __init__(self, app, on_change, root: Path = PROFILE_ROOT):
        super().__init__(app)
        self.app = app
        self.root = Path(root)
        self.on_change = on_change
        self.existing_path = None
        self.fields = {}
        self.entries = {}
        self.player_path = None
        self.current_preview = None
        self.previous_preview = None
        self.candidate_paths = []
        self.preview_seed = None
        self.golden_voice = None
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.title('音色调试工作台 · IndexTTS-2.5')
        self.geometry('1050x820')
        self.minsize(900, 700)
        self.transient(app)
        self._build()
        self.refresh()

    def _build(self):
        wrap = ttk.Frame(self)
        wrap.pack(fill='both', expand=True, padx=12, pady=10)
        left = ttk.Frame(wrap)
        left.pack(side='left', fill='y', padx=(0, 12))
        ttk.Label(left, text='已保存音色', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(0, 5))
        self.listbox = tk.Listbox(left, width=25, height=25, exportselection=False)
        self.listbox.pack(fill='y', expand=True)
        self.listbox.bind('<Double-1>', lambda event: self.edit_selected())
        row = ttk.Frame(left); row.pack(fill='x', pady=6)
        ttk.Button(row, text='新建音色', command=self._guard(self.new)).pack(side='left', padx=2)
        ttk.Button(row, text='编辑选中', command=self._guard(self.edit_selected)).pack(side='left', padx=2)
        ttk.Label(left, text='双击也可编辑。\n不可用音色不会进入正式生产。',
                  wraplength=190).pack(anchor='w', pady=8)
        right_wrap = ttk.Frame(wrap)
        right_wrap.pack(side='left', fill='both', expand=True)
        canvas = tk.Canvas(right_wrap, highlightthickness=0)
        scroll = ttk.Scrollbar(right_wrap, orient='vertical', command=canvas.yview)
        scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        right = ttk.Frame(canvas)
        window = canvas.create_window(0, 0, window=right, anchor='nw')
        right.bind('<Configure>', lambda _:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda event:canvas.itemconfigure(window, width=event.width))
        self.editor_canvas = canvas
        self.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120),'units'))

        identity = ttk.LabelFrame(right, text='基本信息与参考音频')
        identity.pack(fill='x', pady=(0, 6))
        for row_index, (label, key) in enumerate((('音色名称', 'display_name'),
                                                  ('音色ID', 'voice_id'),
                                                  ('配置版本', 'profile_version'))):
            self._field(identity, row_index, label, key)
        self._field(identity, 3, '参考音频', 'reference_source', width=47)
        ttk.Button(identity, text='选择文件', command=self._guard(self.choose_reference)).grid(
            row=3, column=2, padx=5, pady=3)
        ttk.Label(identity, text='参考音频原文（留档，可选）').grid(row=4, column=0, sticky='nw', padx=6, pady=4)
        self.reference_text = tk.Text(identity, height=3, width=48, wrap='word')
        self.reference_text.grid(row=4, column=1, columnspan=2, sticky='ew', padx=5, pady=3)
        identity.columnconfigure(1, weight=1)
        self.profile_status = tk.StringVar(value='草稿')
        ttk.Label(identity,textvariable=self.profile_status).grid(row=6,column=0,columnspan=3,sticky='w',padx=7)
        language = ttk.Frame(identity)
        language.grid(row=5, column=1, columnspan=2, sticky='w', padx=5, pady=(2, 6))
        for label, key in (('参考语言', 'reference_language'), ('目标语言', 'target_language')):
            ttk.Label(language, text=label).pack(side='left', padx=(0, 4))
            var = tk.StringVar()
            self.fields[key] = var
            ttk.Combobox(language, textvariable=var, values=('zh', 'en', 'ja', 'es', 'ar', 'yue', 'ko') if key == 'reference_language' else ('zh', 'en', 'ja', 'es', 'ar'),
                         state='readonly', width=7).pack(side='left', padx=(0, 16))
        ttk.Label(language, text='zh＝中文').pack(side='left')

        tts = ttk.LabelFrame(right, text='IndexTTS-2.5')
        tts.pack(fill='x', pady=6)
        self._small_grid(tts, [('语速', 'speed'), ('分段停顿秒', 'pause')], columns=2)
        Tooltip(self.entries['pause'],'使用IndexTTS interval_silence：控制模型分段之间的静音毫秒数，不保证每个标点固定停顿。')
        ttk.Label(tts,text='1.0正常；大于1更快。停顿使用interval_silence：模型分段间静音，不保证每个标点固定停顿。',wraplength=700).grid(row=6,column=0,columnspan=3,sticky='w',padx=7)
        self.emotion_labels = {'speaker':'跟随音色参考', 'audio':'独立语气参考',
                               'text':'情绪描述', 'vector':'高级情绪向量'}
        ttk.Label(tts, text='语气控制方式').grid(row=2, column=0, sticky='w', padx=7, pady=4)
        self.fields['emotion_mode'] = tk.StringVar(value=self.emotion_labels['speaker'])
        mode=ttk.Combobox(tts,textvariable=self.fields['emotion_mode'],values=tuple(self.emotion_labels.values()),state='readonly',width=22)
        mode.grid(row=2,column=1,sticky='w',padx=7)
        mode.bind('<<ComboboxSelected>>',lambda _:self._emotion_visibility())
        self.emotion_audio = ttk.Frame(tts);self.emotion_audio.grid(row=3,column=0,columnspan=3,sticky='ew')
        self._field(self.emotion_audio,0,'语气参考音频','emotion_reference_source',width=47)
        ttk.Button(self.emotion_audio,text='选择文件',command=self._guard(self.choose_emotion_reference)).grid(row=0,column=2,padx=5)
        self.strength_frame=ttk.Frame(tts);self.strength_frame.grid(row=7,column=0,columnspan=3,sticky='ew')
        self._field(self.strength_frame,0,'语气参考强度','emotion_alpha')
        shortcuts=ttk.Frame(self.strength_frame);shortcuts.grid(row=1,column=0,columnspan=2,sticky='w')
        for value in (.3,.5,.7,1.):
            ttk.Button(shortcuts,text=str(value),command=lambda v=value:self.fields['emotion_alpha'].set(str(v))).pack(side='left',padx=2)
        ttk.Label(self.strength_frame,text='强度越高，越强调语气参考；不一定越像原说话人。').grid(row=2,column=0,columnspan=2,sticky='w',padx=7)
        self.emotion_advanced = ttk.Frame(tts)
        self.emotion_advanced.grid(row=5, column=0, columnspan=3, sticky='ew')
        self.emotion_text_frame=ttk.Frame(tts);self.emotion_text_frame.grid(row=8,column=0,columnspan=3,sticky='ew')
        self._field(self.emotion_text_frame,0,'情绪描述','emotion_text',width=52)
        self._field(self.emotion_advanced, 0, '情绪向量', 'emotion_vector', width=52)
        ttk.Label(self.emotion_advanced, text='向量顺序：高兴、愤怒、悲伤、害怕、反感、低落、惊讶、平静；每项0～1。').grid(row=1, column=0, columnspan=2, sticky='w', padx=6)
        self.advanced_var = tk.BooleanVar(value=False)
        self.vector_toggle=ttk.Checkbutton(tts, text='展开高级情绪向量', variable=self.advanced_var,
                        command=lambda:self.emotion_advanced.grid() if self.advanced_var.get() else self.emotion_advanced.grid_remove())
        self.vector_toggle.grid(row=4, column=0, columnspan=3, sticky='w', padx=7, pady=4)
        self.emotion_advanced.grid_remove()
        processing = ttk.LabelFrame(right, text='音色塑形 · Pitch / 低频 / Presence / 高频')
        processing.pack(fill='x', pady=6)
        self.postprocess_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(processing, text='启用后处理', variable=self.postprocess_var).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=7, pady=5)
        self.pitch_var=tk.BooleanVar(value=False)
        self.pitch_toggle=ttk.Checkbutton(processing,text='启用音高微调（保时长）',variable=self.pitch_var)
        self.pitch_toggle.grid(row=1,column=0,columnspan=2,sticky='w',padx=7)
        self.fields['pitch_semitones']=tk.StringVar(value='0.0')
        ttk.Spinbox(processing,from_=-3,to=3,increment=.1,textvariable=self.fields['pitch_semitones'],width=12).grid(row=1,column=2,sticky='w')
        ttk.Label(processing,text='半音；通常-1.5～+1.5').grid(row=1,column=3,sticky='w')
        self._small_grid(processing, [
            ('低频厚度dB', 'bass_gain'), ('低频Hz', 'bass_frequency'),
            ('高频空气感dB', 'treble_gain'), ('高频Hz', 'treble_frequency')
        ], columns=4, start_row=2)
        presence = ttk.LabelFrame(processing, text='人声存在感 / Presence EQ · 咬字、靠前、尖亮')
        presence.grid(row=4, column=0, columnspan=4, sticky='ew', padx=7, pady=5)
        self.presence_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(presence, text='启用Presence', variable=self.presence_var).grid(
            row=0, column=0, columnspan=3, sticky='w', padx=7, pady=3)
        self._small_grid(presence, [('增益dB（-6～+6）', 'presence_gain_db'),
                                   ('频率Hz（2500～6000）', 'presence_frequency_hz'),
                                   ('Q（0.5～2.0）', 'presence_q')], columns=3, start_row=1)
        ttk.Label(processing, text='低频调厚度；Presence调咬字与靠前感；高频调空气感与齿音。').grid(
            row=5, column=0, columnspan=4, sticky='w', padx=7, pady=3)
        self.deesser_var=tk.BooleanVar(value=False)
        deesser_wrap=ttk.Frame(right);deesser_wrap.pack(fill='x',pady=5)
        deesser=ttk.LabelFrame(deesser_wrap,text='齿音控制（默认关闭）')
        self.deesser_advanced_var=tk.BooleanVar(value=False)
        ttk.Checkbutton(deesser_wrap,text='展开高级齿音控制',variable=self.deesser_advanced_var,
                        command=lambda:deesser.pack(fill='x') if self.deesser_advanced_var.get() else deesser.pack_forget()).pack(anchor='w')
        self.deesser_toggle=ttk.Checkbutton(deesser,text='启用De-Esser',variable=self.deesser_var)
        self.deesser_toggle.grid(row=0,column=0,columnspan=3,sticky='w',padx=7)
        self._small_grid(deesser,[('处理强度0～1','deesser_intensity'),('最大抑制0～1','deesser_max'),('相对频率0～1','deesser_frequency')],columns=3,start_row=1)
        compressor_wrap=ttk.Frame(right);compressor_wrap.pack(fill='x',pady=5)
        compressor=ttk.LabelFrame(compressor_wrap,text='动态压缩')
        self.compressor_advanced_var=tk.BooleanVar(value=False)
        ttk.Checkbutton(compressor_wrap,text='展开动态压缩 / 快捷预设',variable=self.compressor_advanced_var,
                        command=lambda:compressor.pack(fill='x') if self.compressor_advanced_var.get() else compressor.pack_forget()).pack(anchor='w')
        quick=ttk.Frame(compressor);quick.grid(row=0,column=0,columnspan=3,sticky='w')
        for label in COMPRESSION_PRESETS:
            ttk.Button(quick,text=label,command=lambda name=label:self._compression(name)).pack(side='left',padx=3)
        self._small_grid(compressor,[('阈值dB','compressor_threshold'),('压缩比','compressor_ratio'),('启动ms','compressor_attack'),('释放ms','compressor_release'),('补偿dB','compressor_makeup')],columns=3,start_row=1)
        loud=ttk.LabelFrame(right,text='响度');loud.pack(fill='x',pady=5)
        self._small_grid(loud,[('目标LUFS','target_lufs'),('真峰值dBTP','true_peak'),('采样率Hz','sample_rate')],columns=3)
        ttk.Label(loud,text='LUFS控制处理后响度；EQ/Presence不负责调总音量。正式视频还会叠加Voice Gain。',wraplength=700).grid(row=2,column=0,columnspan=3,sticky='w',padx=7)

        preview = ttk.LabelFrame(right, text='临时试听')
        preview.pack(fill='x', pady=6)
        preview_content=ttk.Frame(preview);preview_content.pack(fill='x')
        preset_row=ttk.Frame(preview);preset_row.pack(fill='x')
        for label in PREVIEW_PRESETS:
            ttk.Button(preset_row,text=label,command=lambda name=label:self._preset(name)).pack(side='left',padx=3)
        ttk.Button(preset_row,text='设为标准文本',command=self._guard(self._standard_text)).pack(side='left',padx=3)
        ttk.Label(preview_content, text='试听文本').pack(side='left', padx=6)
        self.preview_text = tk.StringVar(value='你好，这是一段音色试听。')
        ttk.Entry(preview_content, textvariable=self.preview_text, width=48).pack(side='left', fill='x', expand=True, padx=5, pady=5)
        ttk.Button(preview_content, text='生成试听', command=self._guard(self.preview)).pack(side='left', padx=6)
        ttk.Button(preview_content, text='停止/取消试听', command=self._guard(self.cancel_preview)).pack(side='left', padx=3)
        self.standard_text=PROFILE_DEFAULTS['standard_preview_text']
        playback=ttk.Frame(preview);playback.pack(fill='x')
        ttk.Button(playback,text='播放Raw',command=self._guard(lambda:self.play_variant('raw'))).pack(side='left',padx=3)
        ttk.Button(playback,text='播放Processed',command=self._guard(lambda:self.play_variant('processed'))).pack(side='left',padx=3)
        self.ab_a=tk.StringVar(value='参考音频');self.ab_b=tk.StringVar(value='Processed')
        ttk.Combobox(playback,textvariable=self.ab_a,values=('参考音频','Golden Voice','外部基准','上一版试听'),state='readonly',width=13).pack(side='left',padx=3)
        ttk.Button(playback,text='播放A',command=self._guard(self.play_a)).pack(side='left',padx=3)
        ttk.Combobox(playback,textvariable=self.ab_b,values=('Raw','Processed'),state='readonly',width=10).pack(side='left')
        ttk.Button(playback,text='播放B',command=self._guard(lambda:self.play_variant(self.ab_b.get().lower()))).pack(side='left',padx=3)
        candidates=ttk.Frame(preview);candidates.pack(fill='x',pady=4)
        self.candidate_count=tk.StringVar(value='3')
        ttk.Combobox(candidates,textvariable=self.candidate_count,values=('1','3','5'),state='readonly',width=3).pack(side='left',padx=3)
        ttk.Button(candidates,text='生成候选',command=self._guard(self.generate_candidates)).pack(side='left',padx=3)
        ttk.Button(candidates,text='强度0.3/0.5/0.7/1.0',command=self._guard(self.compare_strength)).pack(side='left',padx=3)
        ttk.Button(candidates,text='保存当前试听为Golden',command=self._guard(self.save_current_golden)).pack(side='left',padx=3)
        candidate_actions=ttk.Frame(preview);candidate_actions.pack(fill='x',pady=3)
        self.candidate_choice=tk.StringVar(value='')
        self.candidate_box=ttk.Combobox(candidate_actions,textvariable=self.candidate_choice,values=(),state='readonly',width=15)
        self.candidate_box.pack(side='left',padx=3)
        ttk.Button(candidate_actions,text='播放候选',command=self._guard(self.play_candidate)).pack(side='left',padx=3)
        ttk.Button(candidate_actions,text='采用此候选',command=self._guard(self.adopt_candidate)).pack(side='left',padx=3)
        ttk.Button(candidate_actions,text='结束比较/清理旧试听',command=self._guard(self.release_comparison)).pack(side='left',padx=3)
        diagnostic = ttk.Frame(right); diagnostic.pack(fill='x', pady=3)
        ttk.Label(diagnostic, text='基准音频（可选）').pack(side='left', padx=6)
        self.webui_baseline = tk.StringVar(value='')
        ttk.Entry(diagnostic, textvariable=self.webui_baseline, width=45).pack(side='left', fill='x', expand=True, padx=4)
        ttk.Button(diagnostic, text='选择', command=self._guard(self.choose_baseline)).pack(side='left', padx=3)
        ttk.Button(diagnostic, text='一致性诊断', command=self._guard(self.diagnose)).pack(side='left', padx=3)

        buttons = ttk.Frame(right); buttons.pack(fill='x', pady=7)
        for label, command in (('保存草稿', self.save_draft),
                               ('保存并设为可用', self.save_ready),
                               ('另存为新音色',self.clone),
                               ('历史版本',self.history),
                               ('禁用', self.disable)):
            ttk.Button(buttons, text=label, command=self._guard(command)).pack(side='left', padx=4)
        self.status = tk.StringVar(value='')
        ttk.Label(right, textvariable=self.status, wraplength=720, foreground='#8b3b25').pack(
            fill='x', pady=5)
        self.new()
        try:caps=filter_capabilities(self._tools()[1])
        except Exception:caps={'pitch':False,'deesser':False}
        if not caps['pitch']:self.pitch_toggle.configure(state='disabled')
        if not caps['deesser']:self.deesser_toggle.configure(state='disabled')
        if not all(caps.values()):self.status.set('当前FFmpeg不支持的音高/齿音功能已禁用。')

    def _field(self, parent, row, label, key, width=30):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky='w', padx=6, pady=3)
        var = tk.StringVar()
        self.fields[key] = var
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, sticky='ew', padx=5, pady=3)
        if key == 'voice_id': self.voice_id_entry = entry

    def _small_grid(self, parent, fields, columns=4, start_row=0):
        for i, (label, key) in enumerate(fields):
            row, column = divmod(i, columns)
            ttk.Label(parent, text=label).grid(row=start_row+row*2, column=column,
                                                 sticky='w', padx=7, pady=(4, 0))
            var = tk.StringVar()
            self.fields[key] = var
            entry=ttk.Entry(parent,textvariable=var,width=15)
            entry.grid(row=start_row+row*2+1,column=column,sticky='ew',padx=7,pady=(0,5))
            self.entries[key]=entry
            parent.columnconfigure(column, weight=1)

    def _guard(self, fn):
        def action():
            try:
                fn()
            except Exception as exc:
                import runtime_diagnostics as diag
                diag.error(exc,component="voice-ui")
                self.status.set(str(exc))
                messagebox.showerror('音色管理', str(exc), parent=self)
        return action

    def _provider(self):
        provider=IndexTTS25Provider(log=self.app.worker_log)
        provider.cancel_event=self.app.lanes.cancel_event('voice')
        provider.resource_timeout=self.app.cfg.get('resource_wait_timeout_seconds',300)
        provider.request_timeout=self.app.cfg.get('tts_timeout_seconds',900)
        return provider

    def _launch(self,worker,done):
        def finished(value):
            if self.winfo_exists():done(value)
            else:
                paths=value if isinstance(value,list) else [value]
                for path in paths:
                    if isinstance(path,(str,Path)) and Path(path).suffix.lower()=='.wav':release_preview(path)
        def failed(exc):
            if self.winfo_exists():self.status.set(str(exc)+'；详情见运行日志。')
        return self.app.launch_voice(worker,finished,failed)

    def _tools(self):
        ffmpeg = resolve_tool('ffmpeg', self.app.var('ffmpeg').get().strip())
        ffprobe = resolve_tool('ffprobe', self.app.var('ffprobe').get().strip())
        return ffprobe, ffmpeg

    def refresh(self):
        self.profiles = discover_profiles(self.root)
        self.listbox.delete(0, 'end')
        for profile in self.profiles:
            status = ('可用' if profile.get('available') else {'draft':'草稿','migrated_pending_review':'待审核','disabled':'禁用','invalid':'校验未通过'}.get(profile.get('validation_status'),'待审核'))
            self.listbox.insert('end', f"{profile.get('display_name',profile.get('voice_id','?'))}  ·  {status}")

    def new(self):
        self.existing_path = None
        for key, var in self.fields.items():
            var.set('' if key == 'reference_source' else str(PROFILE_DEFAULTS.get(key, '')))
        self.reference_text.delete('1.0', 'end')
        self.postprocess_var.set(True)
        self.presence_var.set(False)
        self.pitch_var.set(False);self.deesser_var.set(False)
        self.preview_seed=None;self.golden_voice=None
        self.standard_text=PROFILE_DEFAULTS['standard_preview_text'];self.preview_text.set(self.standard_text)
        self.profile_status.set('状态：草稿')
        self.fields['emotion_mode'].set(self.emotion_labels['speaker'])
        self.voice_id_entry.configure(state='normal')
        self._emotion_visibility()
        self.status.set('新音色：请选择你最终确认的参考音频；原文可选，试听满意后设为可用。')

    def edit_selected(self):
        selection = self.listbox.curselection()
        if not selection:
            raise VoiceError('请先在左侧选择一个音色。')
        profile = self.profiles[selection[0]]
        path = Path(profile['profile_path'])
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        self._load_values(data,path)

    def _load_values(self,data,path):
        self.existing_path = path
        for key, var in self.fields.items():
            if key == 'reference_source':
                reference = Path(data['reference_source']) if data.get('reference_source') else path.parent / str(data.get('reference_audio', 'reference.wav'))
                var.set(str(reference) if reference.is_file() else '')
            elif key == 'emotion_reference_source':
                name = data.get('emotion_reference_audio', '')
                var.set(data.get('emotion_reference_source') or (str(path.parent / name) if path and name and (path.parent / name).is_file() else ''))
            elif key == 'emotion_mode':
                var.set(self.emotion_labels.get(data.get(key, 'speaker'), self.emotion_labels['speaker']))
            else:
                var.set(str(data.get(key, PROFILE_DEFAULTS.get(key, ''))))
        self.reference_text.delete('1.0', 'end')
        self.reference_text.insert('1.0', data.get('reference_text', ''))
        self.postprocess_var.set(bool(data.get('postprocess_enabled', True)))
        self.presence_var.set(bool(data.get('presence_enabled', False)))
        self.pitch_var.set(bool(data.get('pitch_enabled',False)));self.deesser_var.set(bool(data.get('deesser_enabled',False)))
        self.preview_seed=data.get('preview_seed');self.golden_voice=data.get('golden_voice')
        self.standard_text=data.get('standard_preview_text',PROFILE_DEFAULTS['standard_preview_text'])
        self.preview_text.set(self.standard_text)
        self.voice_id_entry.configure(state='readonly' if path and voice_id_locked(path) else 'normal')
        self.profile_status.set('状态：'+{'draft':'草稿','ready':'可用','migrated_pending_review':'待审核','disabled':'禁用','invalid':'校验未通过'}.get(data.get('validation_status'),'待审核'))
        self._emotion_visibility()
        self.status.set(data.get('validation_message') or
                        f"当前状态：{data.get('validation_status', '旧版未校验')}。")

    def choose_reference(self):
        selected = filedialog.askopenfilename(
            parent=self, title='选择用户确认的原始参考音频', initialdir=_portable_path(r'D:\DY video'),
            filetypes=[('音频文件', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus *.wma'),
                       ('所有文件', '*.*')])
        if selected:
            self.fields['reference_source'].set(selected)

    def choose_baseline(self):
        selected = filedialog.askopenfilename(parent=self, title='选择基准音频',
            initialdir=_portable_path(r'D:\DY video'), filetypes=[('音频文件', '*.wav *.mp3 *.m4a'), ('所有文件', '*.*')])
        if selected:
            self.webui_baseline.set(selected)

    def choose_emotion_reference(self):
        selected = filedialog.askopenfilename(parent=self, title='选择语气参考音频', initialdir=_portable_path(r'D:\DY video'),
            filetypes=[('音频文件', '*.wav *.mp3 *.m4a *.aac *.flac *.ogg *.opus'), ('所有文件', '*.*')])
        if selected:
            self.fields['emotion_reference_source'].set(selected)
            self.fields['emotion_mode'].set(self.emotion_labels['audio'])
            self._emotion_visibility()

    def diagnose(self):
        if self.app.lanes.running("voice"):
            raise VoiceError('请等待当前任务结束后再运行音色诊断。')
        from voice_diagnostics import run_voice_diagnostic
        ffprobe, ffmpeg = self._tools()
        raw = self._collect()
        text = self.preview_text.get().strip()
        baseline = Path(self.webui_baseline.get().strip()) if self.webui_baseline.get().strip() else None
        if self.ab_a.get()=='Golden Voice':
            if not self.existing_path:raise VoiceError('当前音色尚无Golden Voice。')
            baseline=self.existing_path.parent/'golden_voice.wav'
        elif self.ab_a.get()=='参考音频':baseline=None
        elif self.ab_a.get()=='上一版试听':
            if not self.previous_preview:raise VoiceError('尚无上一版试听。')
            baseline=self.previous_preview
        existing = self.existing_path
        self.status.set('正在生成独立RAW与后处理诊断文件，不会进入正式任务。')
        def worker():
            provider = self._provider()
            return run_voice_diagnostic(raw, target_text=text, ffprobe=ffprobe, ffmpeg=ffmpeg,
                                        provider=provider, existing_path=existing,
                                        webui_baseline=baseline)
        def done(path):
            self.status.set(f'诊断已保存：{path}')
            if os.name == 'nt': os.startfile(str(path))
        self._launch(worker, done)

    def _collect(self) -> dict:
        data = {key: var.get().strip() for key, var in self.fields.items()}
        data['reference_text'] = self.reference_text.get('1.0', 'end-1c').strip()
        data['postprocess_enabled'] = bool(self.postprocess_var.get())
        data['presence_enabled'] = bool(self.presence_var.get())
        data.update(pitch_enabled=self.pitch_var.get(),deesser_enabled=self.deesser_var.get(),
                    standard_preview_text=self.standard_text,preview_seed=self.preview_seed,golden_voice=self.golden_voice)
        data['emotion_mode'] = {label:key for key,label in self.emotion_labels.items()}.get(data['emotion_mode'], data['emotion_mode'])
        return data

    def _save(self, enable: bool):
        if self.app.lanes.running("voice"):
            raise VoiceError('本窗口的后台操作仍在执行，请稍后保存。')
        ffprobe, ffmpeg = self._tools()
        raw=self._collect();existing=self.existing_path
        if enable:
            self.status.set('正在验证当前参数、参考音频、真实TTS及Processed完整解码…')
            self._launch(lambda:save_profile(raw,enable=True,root=self.root,existing_path=existing,
                            ffprobe=ffprobe,ffmpeg=ffmpeg,provider=self._provider()),self._saved)
            return
        self._launch(lambda:save_profile(raw,enable=False,root=self.root,existing_path=existing,ffprobe=ffprobe,ffmpeg=ffmpeg),self._saved)

    def _saved(self,result):
        self.existing_path = Path(result['profile_path'])
        self._load_values(result,self.existing_path)
        self.refresh()
        self.on_change()
        if result['available']:
            self.status.set(f"{result['display_name']}已保存并设为可用；主界面可立即勾选。")
        elif result['validation_status'] == 'invalid':
            self.status.set('音色未启用：' + result['validation_message'])
        else:
            self.status.set(f"{result['display_name']}已保存为草稿，正式生产暂不可选。")

    def save_draft(self): self._save(False)
    def save_ready(self): self._save(True)

    def disable(self):
        if not self.existing_path:
            raise VoiceError('请先选择已保存的音色。')
        if self.app.lanes.running("voice"):
            raise VoiceError('本窗口的后台操作仍在执行，请稍后禁用。')
        disable_profile(self.existing_path, self.root)
        self.refresh()
        self.on_change()
        self.status.set('该音色已禁用，主界面不能用于新配音。')

    def preview(self):
        if self.app.lanes.running("voice"):
            raise VoiceError('请等待当前任务结束后再试听。')
        ffprobe, ffmpeg = self._tools()
        raw = self._collect()
        text = self.preview_text.get().strip()
        existing = self.existing_path
        self.stop_preview()
        self._retain_previous()
        self.status.set('正在使用 IndexTTS-2.5 生成临时试听。')
        def worker():
            provider = self._provider()
            return preview_profile(raw, ffprobe=ffprobe, ffmpeg=ffmpeg, text=text,
                                   provider=provider, existing_path=existing, root=self.root,
                                   log=self.app.worker_log)
        def done(path):
            if self.winfo_exists():
                try:
                    self.current_preview=Path(path)
                    self.play_preview(path)
                    self.status.set(f'试听已生成；当前播放文件：{Path(path).resolve()}')
                except Exception as exc:
                    self.status.set(f'试听已生成，但播放失败：{exc}')
                    self.app.worker_log(f'试听播放失败：{exc}')
            else:
                release_preview(path)
        self._launch(worker, done)

    def cancel_preview(self):
        self.app.lanes.cancel("voice")
        self.stop_preview()
        self.status.set("试听取消已提交；正在推理时将安全结束后丢弃结果，正式视频继续。")

    def stop_preview(self):
        if os.name == 'nt' and self.player_path is not None:
            winsound.PlaySound(None, 0)
        elif getattr(self, '_preview_process', None) is not None:
            if self._preview_process.poll() is None:
                self._preview_process.terminate()
            self._preview_process = None
        self.player_path = None

    def play_preview(self, path):
        self.stop_preview()
        target = Path(path).resolve()
        if os.name != 'nt':
            import subprocess, sys
            if sys.platform != 'darwin':
                raise VoiceError('当前平台没有内置试听播放器，请打开生成的音频文件。')
            self._preview_process = subprocess.Popen(['afplay', str(target)])
        else:
            winsound.PlaySound(str(target), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        self.player_path = target
        if (target.parent/'preview_log.json').is_file():
            record_preview_playback(target, log=self.app.worker_log)

    def _emotion_visibility(self):
        mode={label:key for key,label in self.emotion_labels.items()}.get(self.fields['emotion_mode'].get(),'speaker')
        for widget,visible in ((self.emotion_audio,mode=='audio'),(self.strength_frame,mode!='speaker'),
                               (self.emotion_text_frame,mode=='text'),(self.vector_toggle,mode=='vector')):
            widget.grid() if visible else widget.grid_remove()
        if mode!='vector' or not self.advanced_var.get():self.emotion_advanced.grid_remove()
        else:self.emotion_advanced.grid()

    def _compression(self,name):
        for key,value in COMPRESSION_PRESETS[name].items():self.fields[key].set(str(value))

    def _preset(self,name):
        if PREVIEW_PRESETS[name]:self.preview_text.set(PREVIEW_PRESETS[name])

    def _standard_text(self):
        self.standard_text=self.preview_text.get().strip()
        self.status.set('标准试听文本已更新到当前草稿；保存配置后长期保留。')

    def _retain_previous(self):
        if self.previous_preview and self.previous_preview not in self.candidate_paths:release_preview(self.previous_preview)
        self.previous_preview=self.current_preview
        if self.previous_preview:protect_preview(self.previous_preview)

    def play_variant(self,variant):
        if not self.current_preview:raise VoiceError('请先生成试听。')
        trace=json.loads((self.current_preview.parent/'preview_log.json').read_text(encoding='utf-8'))
        self.play_preview(trace[variant+'_path'])

    def play_a(self):
        choice=self.ab_a.get()
        path=(self.fields['reference_source'].get() if choice=='参考音频' else
              str(self.existing_path.parent/'golden_voice.wav') if choice=='Golden Voice' and self.existing_path else
              self.webui_baseline.get() if choice=='外部基准' else self.previous_preview if choice=='上一版试听' else None)
        if not path or not Path(path).is_file():raise VoiceError('A基准文件尚未选择或不存在。')
        # winsound supports WAV. Convert external compressed references into a protected temporary WAV.
        if Path(path).suffix.lower()!='.wav':
            from voice_profile_manager import PREVIEW_ROOT,_run
            import uuid
            from file_mutex import file_mutex
            folder=PREVIEW_ROOT/uuid.uuid4().hex
            with file_mutex(PREVIEW_ROOT/'.lifecycle.lock'):
                folder.mkdir(parents=True)
                (folder/'.protected').write_text('外部A基准转换中',encoding='utf-8')
            target=folder/'preview.wav'
            try:
                _run([self._tools()[1],'-v','error','-nostdin','-y','-i',path,'-ac','1','-ar','48000',target])
            except Exception:
                release_preview(target)
                raise
            protect_preview(target,'外部A基准试听');self.candidate_paths.append(target);path=target
        self.play_preview(path)

    def save_current_golden(self):
        if not self.existing_path or not self.current_preview:raise VoiceError('请先保存音色草稿并生成试听。')
        target=save_golden(self.existing_path,self.current_preview,self._tools()[1])
        self.golden_voice='golden_voice.wav';self.status.set('Golden Voice已保存并受清理保护：'+str(target))

    def _generate_many(self,strength=False):
        if self.app.lanes.running("voice"):raise VoiceError('请等待当前任务结束。')
        import secrets
        raw=self._collect();ffprobe,ffmpeg=self._tools();text=self.preview_text.get().strip();existing=self.existing_path
        if strength and raw['emotion_mode']=='speaker':raise VoiceError('请先选择独立语气参考、情绪描述或向量模式。')
        variations=(.3,.5,.7,1.) if strength else range(int(self.candidate_count.get()))
        seed=secrets.randbits(31)
        self._retain_previous();self.stop_preview()
        self.status.set('顺序生成对照试听；每次仅一个GPU推理…')
        def worker():
            paths=[]
            provider=self._provider()
            if hasattr(provider,'ensure_ready'):
                provider.ensure_ready()
                if not provider._health().get('sampling_supported',False):
                    raise VoiceError('当前Worker未确认支持真实采样变化；未生成假随机候选。')
            try:
                for i,value in enumerate(variations):
                    values=dict(raw,preview_seed=seed if strength else seed+i)
                    if strength:values['emotion_alpha']=value
                    path=preview_profile(values,ffprobe=ffprobe,ffmpeg=ffmpeg,text=text,provider=provider,
                                         existing_path=existing,root=self.root,log=self.app.worker_log)
                    protect_preview(path,'强度比较中' if strength else '候选比较中');paths.append(path)
            except Exception:
                for path in paths:release_preview(path)
                raise
            return paths
        def done(paths):
            for old in self.candidate_paths:
                if old not in (self.previous_preview,self.current_preview):release_preview(old)
            self.candidate_paths=paths;self.current_preview=paths[-1]
            labels=[f'强度{v}' if strength else f'候选{i+1}' for i,v in enumerate(variations)]
            self.candidate_box.configure(values=labels);self.candidate_box.current(0)
            self.status.set('候选已生成；可逐个试听，采用只更新当前草稿参数。')
        self._launch(worker,done)

    def generate_candidates(self):self._generate_many(False)
    def compare_strength(self):self._generate_many(True)

    def play_candidate(self):
        index=self.candidate_box.current()
        if index<0:raise VoiceError('请先选择候选。')
        self.play_preview(self.candidate_paths[index])

    def adopt_candidate(self):
        index=self.candidate_box.current()
        if index<0:raise VoiceError('请先选择候选。')
        path=self.candidate_paths[index]
        trace=json.loads((path.parent/'preview_log.json').read_text(encoding='utf-8'))
        if trace['voice_id']!=self.fields['voice_id'].get().strip():
            raise VoiceError('候选属于其他音色；请返回对应音色或重新生成。')
        data=dict(trace['executed_value'],reference_source=trace['reference_path'],
                  emotion_reference_source=trace['executed_value'].get('emotion_reference_path',''))
        data.update({k:self._collect()[k] for k in ('display_name','voice_id','profile_version','golden_voice','standard_preview_text')})
        self._load_values(data,self.existing_path);self.current_preview=path
        self.preview_text.set(trace['target_text'])
        self.status.set('已采用候选种子和完整参数到当前草稿；尚未保存。')

    def release_comparison(self):
        self.stop_preview()
        for path in [self.previous_preview,*self.candidate_paths]:
            if path:release_preview(path)
        self.previous_preview=None;self.candidate_paths=[]
        self.candidate_box.configure(values=());self.candidate_choice.set('')
        from voice_profile_manager import PREVIEW_ROOT
        from temp_lifecycle import PREVIEW_NAMES,prune_transient_runs
        prune_transient_runs(PREVIEW_ROOT,PREVIEW_NAMES,kind='voice_preview',keep=self.current_preview.parent if self.current_preview else None)
        self.status.set('比较已结束，旧的未保护试听已清理。')

    def clone(self):
        name=simpledialog.askstring('另存为新音色','新音色名称',parent=self)
        if not name:return
        vid=simpledialog.askstring('另存为新音色','新的音色ID（英文）',parent=self)
        if not vid:return
        raw=clone_values(self._collect(),name,vid)
        self.existing_path=None
        for key,var in self.fields.items():
            if key in raw:var.set(str(raw[key]))
        self.golden_voice=None;self.voice_id_entry.configure(state='normal');self._save(False)

    def history(self):
        if not self.existing_path:raise VoiceError('请先选择已保存音色。')
        versions=profile_history(self.existing_path)
        dialog=tk.Toplevel(self);dialog.title('历史版本 · 恢复到草稿')
        listing=tk.Listbox(dialog,width=70,height=12);listing.pack(fill='both',expand=True,padx=10,pady=8)
        for path in versions:listing.insert('end',path.name)
        def view():
            if not listing.curselection():return
            source=versions[listing.curselection()[0]]
            viewer=tk.Toplevel(dialog);viewer.title(source.name)
            text=tk.Text(viewer,width=75,height=25,wrap='word');text.pack(fill='both',expand=True)
            text.insert('1.0',source.read_text(encoding='utf-8-sig'));text.configure(state='disabled')
        ttk.Button(dialog,text='查看选中版本参数',command=self._guard(view)).pack(pady=3)
        def restore():
            if not listing.curselection():return
            data=restore_draft(self.existing_path,versions[listing.curselection()[0]])
            self._load_values(data,self.existing_path);self.profile_status.set('状态：恢复草稿（尚未保存）')
            self.status.set('历史参数已恢复到当前草稿，未启用；试听后可保存草稿或明确设为可用。');dialog.destroy()
        ttk.Button(dialog,text='恢复选中版本到草稿',command=self._guard(restore)).pack(pady=8)

    def destroy(self):
        self.app.lanes.cancel("voice")
        self.stop_preview()
        for path in [self.current_preview,self.previous_preview,*self.candidate_paths]:
            if path:release_preview(path)
        super().destroy()


class Tooltip:
    def __init__(self,widget,text):
        self.widget=widget;self.text=text;self.tip=None
        widget.bind('<Enter>',self.show,add='+');widget.bind('<Leave>',self.hide,add='+')
    def show(self,event=None):
        self.hide();self.tip=tk.Toplevel(self.widget);self.tip.overrideredirect(True)
        self.tip.geometry(f'+{self.widget.winfo_rootx()}+{self.widget.winfo_rooty()+self.widget.winfo_height()+4}')
        ttk.Label(self.tip,text=self.text,wraplength=420,background='#fff6d5',padding=8).pack()
    def hide(self,event=None):
        if self.tip:self.tip.destroy();self.tip=None
