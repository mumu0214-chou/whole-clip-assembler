# -*- coding: utf-8 -*-
"""Tkinter desktop app for manually curated whole-clip assembly. Python 3.10+."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from typing import Any

from video_processing import PROCESSING_DEFAULTS, UNIQVID_URL, processing_settings, processing_warnings
from timeline_processing import TIMELINE_DEFAULTS
from voice_pipeline import (MODES, VoiceError, IndexTTS25Provider, PROFILE_ROOT,
                            ensure_mambo_template, discover_profiles, parse_scripts,
                            bind_scripts, prepare_script_packages)
from script_library import ScriptLibrary
from editing_profile_ui import EditingProfileMixin
from workbench_ui import progress_visible
import editing_profiles
from video_plan_policy import REVISION as VIDEO_POLICY_REVISION, MODES as VIDEO_MODES, MODE_LABELS, build_schedule, mode_counts
from video_plan_policy import apportion
from temp_lifecycle import (PREVIEW_NAMES, DIAGNOSTIC_NAMES, cleanup_complete_jobs,
                            cleanup_stale_partials, cleanup_test_runs,
                            cleanup_orphan_job, manual_cleanup, prune_transient_runs)
from voice_profile_manager import PREVIEW_ROOT
from voice_diagnostics import DIAGNOSTIC_ROOT

from core import (APP_DIR, APP_VERSION, DEFAULTS, GROUPS, STAGE_GROUPS, STAGE_RULE_VERSIONS, BGM_DIRECTORY_RULE, groups_for_config,
                  AssemblyError, Store, atomic_json, upgrade_render_settings,
                  read_json, scan_library, plan_batch, save_batch, render_batch, now,
                  resolve_tool, run_capture, remotion_environment, instance_lock, validate_config, load_top_texts,
                  music_candidates)


def open_local(path: str):
    p = Path(path)
    if not p.exists():
        raise AssemblyError(f'路径不存在：{p}')
    if os.name == 'nt':
        os.startfile(str(p))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(p)])
    else:
        subprocess.Popen(['xdg-open', str(p)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


from render_queue_ui import RenderQueueControls

class App(RenderQueueControls,EditingProfileMixin, tk.Tk):
    def __init__(self, data_dir: Path, *, presentation_only=False):
        super().__init__()
        from wheel_guard import install_tk
        install_tk(self)
        self.presentation_only=presentation_only
        if presentation_only:
            self.withdraw()
            from qt_controller_timers import prepare
            prepare(self)
        self.data_dir = Path(data_dir)
        completed_cleanup = cleanup_complete_jobs(self.data_dir)
        partial_cleanup = cleanup_stale_partials(self.data_dir)
        preview_root, diagnostic_root = Path(PREVIEW_ROOT), Path(DIAGNOSTIC_ROOT)
        preview_dirs = sorted((p for p in preview_root.iterdir() if p.is_dir()),
                              key=lambda p:p.stat().st_mtime, reverse=True) if preview_root.is_dir() else []
        diagnostic_dirs = sorted((p for p in diagnostic_root.iterdir() if p.is_dir()),
                                 key=lambda p:p.stat().st_mtime, reverse=True) if diagnostic_root.is_dir() else []
        preview_cleanup = prune_transient_runs(preview_root, PREVIEW_NAMES, kind='voice_preview',
                                               keep=preview_dirs[0] if preview_dirs else None,
                                               log_file=self.data_dir/'cleanup.jsonl')
        diagnostic_cleanup = prune_transient_runs(diagnostic_root, DIAGNOSTIC_NAMES, kind='voice_diagnostic',
                                                  keep=diagnostic_dirs[0] if diagnostic_dirs else None,
                                                  log_file=self.data_dir/'cleanup.jsonl')
        test_root = self.data_dir/'test_runs'
        test_dirs = sorted((p for p in test_root.iterdir() if p.is_dir()),
                           key=lambda p:p.stat().st_mtime, reverse=True) if test_root.is_dir() else []
        test_cleanup = cleanup_test_runs(self.data_dir, keep=test_dirs[0] if test_dirs else None)
        reports = (completed_cleanup, partial_cleanup, preview_cleanup, diagnostic_cleanup, test_cleanup)
        self.startup_cleanup_report = (sum(r[0] for r in reports), sum(r[1] for r in reports))
        self.store = Store(data_dir)
        from batch_history import migrate
        self.history_folder_report=migrate(self.store)
        self.script_library = ScriptLibrary(self.data_dir / 'script_library.sqlite3')
        self.config_path = self.data_dir / 'settings.json'
        self.music_path = self.data_dir / 'music_ranges.json'
        self.cfg = copy.deepcopy(DEFAULTS)
        from bgm_balance import upgrade_settings
        saved=read_json(self.config_path,{})
        updated=upgrade_settings(saved)
        if updated!=saved:atomic_json(self.config_path,updated)
        self.cfg.update(updated)
        self.cfg = upgrade_render_settings(self.cfg)
        self.cfg, self.profile_state = editing_profiles.initialize(self.cfg)
        from visual_controls import migrate_for_editor
        self.cfg = migrate_for_editor(self.cfg)
        ensure_mambo_template()
        self.voice_profiles = discover_profiles()
        self.voice_provider = None
        self.planning_provider = None
        self.script_overrides = read_json(self.data_dir / 'script_overrides.json', {})
        self.custom_readings = read_json(self.data_dir / 'normalizer_custom.json', {})
        self.ranges = read_json(self.music_path, [])
        self.clips, self.music = [], []
        self.batch = None
        self.batch_path = None
        self.busy = False
        self.render_batch_state = None
        self.render_batch_path = None
        self.render_running = False
        from render_queue import RenderQueue
        self.video_queue=RenderQueue(self.data_dir/'render_queue.json');self._queue_batches={}
        self.planning_pause = threading.Event()
        self.pause = threading.Event()
        self.abort = threading.Event()
        self.events = queue.Queue()
        from background_tasks import Lanes
        self.lanes = Lanes(self.events)
        self.lanes.submit('external-recovery',self.script_library.external.reconcile_preparations,lambda _:self.log('外部配音未完成准备记录已核对。'))
        import runtime_diagnostics as diag
        ds=self.cfg.get("diagnostic_settings", {})
        self.diagnostics=diag.configure(self.data_dir/"runtime_logs", debug=ds.get("debug",False),
            retention_days=ds.get("retention_days",30),file_bytes=int(ds.get("file_mb",8)*1024**2),total_bytes=int(ds.get("total_mb",512)*1024**2))
        threading.Thread(target=self.diagnostics.cleanup,daemon=True).start()
        self.pending_close = False
        self.controls = []
        self.vars = {}
        self.music_choices = {'自动选择': ''}
        self.title('整段组合器 · 剪辑工作台')
        self.geometry('1420x920')
        self.minsize(1180, 760)
        self.configure(bg='#f3f5f8')
        self._style()
        self._build()
        self.build_profile_controls()
        from workbench_ui import attach_empty_states
        attach_empty_states(self)
        self.refresh_render()
        if self.history_folder_report['moved']:
            self.log(f'历史目录已按时间归档：{len(self.history_folder_report["moved"])}个批次；方案及保留期限保持。')
        for issue in self.history_folder_report['errors']:
            self.log('历史时间归档未完成，保留现场并在下次启动重试：'+issue['error'])
        if self.startup_cleanup_report[0]:
            self.log(f'启动清理已完成：{self.startup_cleanup_report[0]}个已完成任务的临时文件，'
                     f'释放{self.startup_cleanup_report[1]/1024**2:.1f} MiB。')
        self._pump_after=self.after(120, self._pump)
        self.history_cleaning=False
        self._retention_after=self.after(1000,self.cleanup_history_timer)
        self.protocol('WM_DELETE_WINDOW', self.on_close)

    def _style(self):
        from workbench_ui import apply_theme
        apply_theme(self)

    def var(self, name, default=None):
        if name not in self.vars:
            val = self.cfg.get(name, default if default is not None else '')
            self.vars[name] = tk.StringVar(value=str(val))
        return self.vars[name]

    def _button(self, frame, text, command, **kwargs):
        btn = ttk.Button(frame, text=text, command=lambda: self.guard(command), **kwargs)
        self.controls.append(btn)
        return btn

    def _entry(self, parent, key, width=10):
        obj = ttk.Entry(parent, textvariable=self.var(key), width=width)
        self.controls.append(obj)
        return obj

    def _tree(self, parent, columns, heights=10):
        from paged_tree import PagedTree
        wrap = ttk.Frame(parent)
        wrap.pack(fill='both', expand=True, padx=8, pady=5)
        tree = PagedTree(wrap, columns=[c[0] for c in columns], show='headings', height=heights)
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, minwidth=55, stretch=key in ('sequence', 'file', 'path'))
        ys = ttk.Scrollbar(wrap, orient='vertical', command=tree.yview)
        xs = ttk.Scrollbar(wrap, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        tree.grid(row=0, column=0, sticky='nsew')
        ys.grid(row=0, column=1, sticky='ns')
        xs.grid(row=1, column=0, sticky='ew')
        tree.controls(wrap).grid(row=2, column=0, columnspan=2, sticky='ew', pady=4)
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        return tree

    def _pathrow(self, frame, row, label, key, file=False):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky='w', padx=6, pady=6)
        self._entry(frame, key, 65).grid(row=row, column=1, sticky='ew', padx=6, pady=6)
        def browse():
            p = filedialog.askopenfilename(parent=self) if file else filedialog.askdirectory(parent=self)
            if p:
                self.var(key).set(p)
        self._button(frame, '选择', browse).grid(row=row, column=2, padx=6, pady=6)
        frame.columnconfigure(1, weight=1)

    def _build(self):
        from workbench_ui import create_shell,Disclosure
        create_shell(self)
        paths = ttk.Frame(self.paths_section.body)
        paths.pack(fill='x', padx=7, pady=(6, 2))
        self._pathrow(paths, 0, '素材根目录', 'material_root')
        self._pathrow(paths, 1, '音乐目录', 'music_root')
        self._pathrow(paths, 2, '输出目录', 'output_root')

        script_box = ttk.LabelFrame(self.voice_content, text='当前文案与声音')
        script_box.pack(fill='x', padx=8, pady=4)
        script_bar = ttk.Frame(script_box);script_bar.pack(fill='x', padx=7, pady=3)
        self.script_source = tk.StringVar(value=self.cfg.get('script_source', '文案库'))
        for label in ('文案库', '临时输入'):
            ttk.Radiobutton(script_bar, text=label, variable=self.script_source, value=label).pack(side='left', padx=3)
        self.script_input_mode = tk.StringVar(value=self.cfg.get('script_input_mode', '单篇'))
        self.temporary_script_modes = ttk.Frame(script_bar)
        self.temporary_script_modes.pack(side='left')
        for label in ('单篇', '批量'):
            ttk.Radiobutton(self.temporary_script_modes, text=label, variable=self.script_input_mode, value=label).pack(side='left', padx=4)
        self.script_source.trace_add('write', lambda *_: self._toggle_temporary_script_modes())
        self._toggle_temporary_script_modes()
        ttk.Label(script_bar, text='模式快捷预设').pack(side='left', padx=(14,4))
        ratios = self.cfg.get('video_mode_percentages', {})
        preset = next((f'{MODE_LABELS[mode]} 100%' for mode in VIDEO_MODES
                       if float(ratios.get(mode, 0)) == 100 and
                       all(float(ratios.get(other, 0)) == 0 for other in VIDEO_MODES if other != mode)),
                      '自定义混合')
        self.mode_preset_var = tk.StringVar(value=preset)
        mode_box = ttk.Combobox(script_bar, textvariable=self.mode_preset_var,
                                values=tuple(f'{MODE_LABELS[mode]} 100%' for mode in VIDEO_MODES) + ('自定义混合',),
                                state='readonly', width=24)
        mode_box.pack(side='left', padx=4);self.controls.append(mode_box)
        mode_box.bind('<<ComboboxSelected>>', self._apply_mode_preset)
        self.allow_script_repeat = tk.BooleanVar(value=bool(self.cfg.get('allow_script_repeat', False)))
        ttk.Checkbutton(script_bar, text='允许重复文案', variable=self.allow_script_repeat).pack(side='left', padx=9)
        script_actions=ttk.Frame(script_box);script_actions.pack(fill='x',padx=7,pady=4)
        self._button(script_actions, '解析文案', self.parse_script_input).pack(side='left', padx=4)
        self._button(script_actions, '编辑朗读文本', self.edit_speech_text).pack(side='left', padx=4)
        self._button(script_actions, '自定义读法', self.edit_custom_reading).pack(side='left', padx=4)
        self.script_count_var = tk.StringVar(value='尚未解析文案')
        ttk.Label(script_actions, textvariable=self.script_count_var).pack(side='left', padx=8)
        self.script_input = tk.Text(script_box, height=4, wrap='word')
        self.script_input.pack(fill='x', padx=9, pady=3)
        library_bar = ttk.Frame(script_box);library_bar.pack(fill='x', padx=9, pady=2)
        ttk.Label(library_bar, text='文案库选择').pack(side='left')
        self.script_library_strategy = tk.StringVar(value=self.cfg.get('script_library_strategy', '低使用次数优先'))
        ttk.Combobox(library_bar, textvariable=self.script_library_strategy,
                     values=('低使用次数优先', '顺序', '随机'), state='readonly', width=18).pack(side='left', padx=5)
        self.script_category_buttons={}
        for profile_id, label in editing_profiles.SCRIPT_NAMES.items():
            button=self._button(library_bar, label, lambda key=profile_id: self.open_script_library(key));button.pack(side='left', padx=4)
            self.script_category_buttons[profile_id]=button
        ttk.Label(library_bar, text='仅取当前品类的启用文案；临时输入可单篇或批量。').pack(side='left', padx=8)
        draft = self.data_dir / 'script_draft.txt'
        if 'script_draft_text' in self.cfg: self.script_input.insert('1.0',self.cfg['script_draft_text'])
        elif draft.is_file() and self.cfg['editing_profile_id']==editing_profiles.FRESH:
            self.script_input.insert('1.0', draft.read_text(encoding='utf-8-sig'))
        self.local_voice_section=Disclosure(self.voice_content,'本地配音音色',expanded=False);self.local_voice_section.pack(fill='x',padx=8,pady=8)
        sfx_box=ttk.LabelFrame(self.voice_content,text='牛肉丸片段音效');sfx_box.pack(fill='x',padx=8,pady=8,before=self.voice_content.winfo_children()[0])
        self.clip_sfx_enabled_var=tk.BooleanVar(value=bool(self.cfg.get('clip_sfx_enabled',False)))
        self.clip_sfx_enable_control=ttk.Checkbutton(sfx_box,text='播放片段音效（仅新方案）',variable=self.clip_sfx_enabled_var)
        self.clip_sfx_enable_control.pack(anchor='w',padx=8,pady=4)
        self.clip_sfx_manage_button=self._button(sfx_box,'管理音效 / 绑定片段',self.open_clip_sfx)
        self.clip_sfx_manage_button.pack(anchor='w',padx=8,pady=4)
        ttk.Label(sfx_box,text='关闭后不播放片段音效，仍保留场景分类及全量镜头轮换，配音和背景音乐保持。开启时按原绑定混入，不按音效时长裁剪视频；工作台也可直接切换。',wraplength=1000).pack(anchor='w',padx=8,pady=4)
        voice_bar = ttk.Frame(self.local_voice_section.body);voice_bar.pack(fill='x', padx=7, pady=3)
        ttk.Label(voice_bar, text='音色策略').pack(side='left', padx=4)
        self.voice_strategy_var = tk.StringVar(value=self.cfg.get('voice_strategy','固定'))
        cmb = ttk.Combobox(voice_bar, textvariable=self.voice_strategy_var, values=('固定','轮换','随机'),state='readonly',width=7)
        cmb.pack(side='left',padx=3);self.controls.append(cmb);self.voice_strategy_control=cmb
        ttk.Label(voice_bar, text='音色').pack(side='left',padx=(12,3))
        self.voice_frame = ttk.Frame(voice_bar);self.voice_frame.pack(side='left')
        self.voice_checks = {}
        self.refresh_voice_profiles()
        self._button(voice_bar,'刷新音色',self.refresh_voice_profiles).pack(side='left',padx=6)
        mix_bar=ttk.Frame(script_box);mix_bar.pack(fill='x',padx=9,pady=4)
        self.subtitle_style_button=self._button(mix_bar,'字幕字体 / 样式',self.open_subtitle_editor)
        self.subtitle_style_button.pack(side='left',padx=(0,12))
        ttk.Label(mix_bar,text='字幕位置').pack(side='left',padx=(12,3))
        self.subtitle_position_var=tk.StringVar(value=self.cfg.get('subtitle_position','下中'))
        cmb=ttk.Combobox(mix_bar,textvariable=self.subtitle_position_var,
                         values=('顶部','上中','居中','下中','底部安全区','自定义'),state='readonly',width=10)
        cmb.pack(side='left',padx=3);self.controls.append(cmb);self.subtitle_position_control=cmb
        cmb.bind('<<ComboboxSelected>>', self.set_subtitle_position)
        ttk.Label(mix_bar,text='混音').pack(side='left',padx=(12,3))
        self.audio_mix_var=tk.StringVar(value=self.cfg.get('audio_mix_profile','配音优先'))
        cmb=ttk.Combobox(mix_bar,textvariable=self.audio_mix_var,
                         values=('配音优先','均衡','音乐突出'),state='readonly',width=9)
        cmb.pack(side='left',padx=3);self.controls.append(cmb);self.audio_mix_control=cmb
        gain_bar=ttk.Frame(script_box);gain_bar.pack(fill='x',padx=9,pady=(0,4))
        ttk.Label(gain_bar,text='配音音量').pack(side='left')
        gain_slider = tk.Scale(gain_bar, from_=-30, to=24, resolution=1, orient='horizontal',
                               length=190, showvalue=False, variable=self.var('voice_gain_db'))
        gain_slider.pack(side='left', padx=4)
        self.voice_gain_slider=gain_slider
        self.voice_gain_control=self._entry(gain_bar,'voice_gain_db',5)
        self.voice_gain_control.pack(side='left',padx=4)
        ttk.Label(gain_bar,text='dB（-30～+24；高增益可能产生压缩感）').pack(side='left')
        ttk.Label(gain_bar,text='BGM（-60～+12 dB）').pack(side='left',padx=(10,0))
        self._entry(gain_bar,'bgm_trim_db',5).pack(side='left',padx=4)
        self._button(gain_bar,'音色管理',self.open_voice_manager).pack(side='left',padx=(14,4))
        self._button(gain_bar,'批量选择音色',self.open_voice_selector).pack(side='left',padx=4)
        ttk.Label(gain_bar,text='临时输入支持 --- 或 1. / 2. 篇首编号；顶文设置见左侧“顶部文案”。').pack(side='left',padx=15)

        self.cache_section=Disclosure(self.voice_content,'配音缓存与本批重新配音',expanded=False);self.cache_section.pack(fill='x',padx=8,pady=8)
        cache_bar=ttk.Frame(self.cache_section.body);cache_bar.pack(fill='x',padx=9,pady=4)
        self.voice_cache_var=tk.BooleanVar(value=self.cfg.get('voice_cache_enabled',True) if self.cfg.get('voice_cache_revision') else True)
        self.voice_force_var=tk.BooleanVar(value=False)
        ttk.Checkbutton(cache_bar,text='启用配音缓存',variable=self.voice_cache_var).pack(side='left')
        ttk.Checkbutton(cache_bar,text='本批强制重新配音',variable=self.voice_force_var).pack(side='left',padx=8)
        for label,command in [('缓存占用',self.voice_cache_info),('打开缓存目录',self.open_voice_cache),('清理未使用缓存',self.clean_voice_cache)]:
            self._button(cache_bar,label,command).pack(side='left',padx=4)

        self._build_video_policy_controls()

        params = ttk.LabelFrame(self.parameter_section.body, text='组合参数')
        params.pack(fill='x', padx=8, pady=6)
        ttk.Label(params, text='生成数量').grid(row=0, column=0, padx=8, pady=6, sticky='w')
        self._entry(params, 'count', 6).grid(row=0, column=1, sticky='w')
        ttk.Label(params, text='时长档位').grid(row=0, column=2, padx=8, sticky='w')
        self.preset = tk.StringVar(value='常规 15～30秒')
        combo = ttk.Combobox(params, textvariable=self.preset, values=('常规 15～30秒', '中长 30～40秒', '长版 40～45秒', '自定义'), width=17, state='readonly')
        combo.grid(row=0, column=3, sticky='w')
        combo.bind('<<ComboboxSelected>>', self.set_preset)
        self.controls.append(combo)
        ttk.Label(params, text='最短 / 最长秒').grid(row=0, column=4, padx=8)
        lengths = ttk.Frame(params)
        lengths.grid(row=0, column=5, sticky='w')
        self._entry(lengths, 'min_seconds', 6).pack(side='left')
        ttk.Label(lengths, text=' ～ ').pack(side='left')
        self._entry(lengths, 'max_seconds', 6).pack(side='left')
        ttk.Label(params, text='优先时长秒').grid(row=1, column=0, padx=8, pady=6, sticky='w')
        pref = ttk.Frame(params)
        pref.grid(row=1, column=1, sticky='w')
        self._entry(pref, 'preferred_min', 6).pack(side='left')
        ttk.Label(pref, text=' ～ ').pack(side='left')
        self._entry(pref, 'preferred_max', 6).pack(side='left')
        ttk.Label(params, text='C展示可跳过；连续C数量由时长和互斥决定').grid(row=1, column=2,columnspan=2,padx=8,sticky='w')
        ttk.Label(params, text='去重').grid(row=1, column=4, padx=8)
        self.dedup_var = tk.StringVar(value='更严格' if self.cfg['dedup_mode'] == 'strict' else '均衡')
        cmb = ttk.Combobox(params, textvariable=self.dedup_var, values=('均衡', '更严格'), width=16, state='readonly')
        cmb.grid(row=1, column=5, sticky='w')
        self.controls.append(cmb)
        ttk.Label(params, text='音乐结束').grid(row=2, column=0, padx=8, pady=6, sticky='w')
        self.music_mode_var = tk.StringVar(value='自然区间（先确认）' if self.cfg['music_mode'] == 'natural' else '允许音乐截尾淡出')
        mode = ttk.Combobox(params, textvariable=self.music_mode_var, values=('自然区间（先确认）', '允许音乐截尾淡出'), width=21, state='readonly')
        mode.grid(row=2, column=1, columnspan=2, sticky='w')
        self.controls.append(mode)
        ttk.Label(params, text='指定音乐').grid(row=2, column=3, sticky='e', padx=6)
        self.music_selected_var = tk.StringVar(value='自动选择')
        self.music_picker = ttk.Combobox(params, textvariable=self.music_selected_var, values=('自动选择',), width=25, state='readonly')
        self.music_picker.grid(row=2, column=4, columnspan=2, sticky='w')
        self.controls.append(self.music_picker)
        ttk.Label(params, text='V2相同原片区间上限').grid(row=3,column=0,padx=8,pady=6,sticky='w')
        self._entry(params,'selection_v2_interval_limit',7).grid(row=3,column=1,sticky='w')
        ttk.Label(params, text='目标账号（可空）').grid(row=3,column=2,padx=8,sticky='w')
        self._entry(params,'selection_v2_target_account',20).grid(row=3,column=3,sticky='w')
        ttk.Label(params, text='填0.7代表70%；按相同原片区间比例计算，不是旧Clip数量比例，也不是平台安全阈值。',wraplength=375).grid(row=3,column=4,columnspan=2,sticky='w')
        ttk.Label(params, text='每批至少 C/开头首镜').grid(row=4,column=0,padx=8,pady=6,sticky='w')
        self._entry(params,'opening_c_min_per_batch',7).grid(row=4,column=1,sticky='w')
        self.opening_count_note=tk.StringVar()
        def opening_note(*_):
            try:
                minimum,count=int(self.var('opening_c_min_per_batch').get()),int(self.var('count').get())
                self.opening_count_note.set(f'首镜最低条数{minimum}超过本批数量{count}，请在左侧调整。' if minimum>count else 'C/开头首镜要求仅用于鲜切牛肉；牛肉丸按G组规则选片。')
            except ValueError:self.opening_count_note.set('首镜最低条数和本批数量须为整数。')
        for key in ('opening_c_min_per_batch','count'):self.var(key).trace_add('write',opening_note)
        opening_note();ttk.Label(params,textvariable=self.opening_count_note,wraplength=700).grid(row=5,column=0,columnspan=6,sticky='w',padx=8,pady=5)
        ttk.Label(params, text='默认1；仅对新方案生效。合格候选受时长、互斥或去重限制时会说明未达标。',wraplength=550).grid(row=4,column=2,columnspan=4,sticky='w')
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)
        params.columnconfigure(5, weight=1)

        confirms = ttk.Frame(self.main_content)
        confirms.pack(fill='x', padx=8, pady=5)
        self.review_var = tk.BooleanVar(value=False)
        self.rights_var = tk.BooleanVar(value=False)
        for var, text, cmd in ((self.review_var, '启用的当前版本片段已复核，可整段使用', self.confirm_reviewed),
                               (self.rights_var, '我确认所选音乐可用于本次用途', None)):
            obj = ttk.Checkbutton(confirms, text=text, variable=var, command=lambda c=cmd: self.guard(c) if c else None)
            obj.pack(side='left', padx=(2, 18))
            self.controls.append(obj)
        ttk.Label(confirms, text='原声：强制去除', foreground='#216e63').pack(side='right')
        planning_area = ttk.LabelFrame(self.main_content, text='组合方案 · 可在视频生成时准备下一批')
        self.planning_area=planning_area
        planning_area.pack(fill='x', padx=8, pady=5)
        bar = ttk.Frame(planning_area);bar.pack(fill='x', pady=3)
        self.scan_btn=self._button(bar, '扫描素材和音乐', self.scan);self.scan_btn.pack(side='left',padx=3)
        self.plan_btn=self._button(bar, '生成组合方案', self.plan,style='Accent.TButton');self.plan_btn.pack(side='left',padx=3)
        self.plan_pause_btn=ttk.Button(bar,text='停止本次组合',command=self.request_plan_pause,state='disabled');self.plan_pause_btn.pack(side='left',padx=3)
        self._button(bar, '打开历史批次', self.load_batch).pack(side='left', padx=3)
        self._button(bar,'追加到视频待剪辑队列',self.send_plan_to_render).pack(side='left',padx=3)
        self._button(bar, '重试失败配音', self.retry_failed_voice).pack(side='left', padx=3)
        self.plan_status_var=tk.StringVar(value='未载入组合方案')
        ttk.Label(planning_area,textvariable=self.plan_status_var).pack(anchor='w',padx=5)
        self.plan_progress=ttk.Progressbar(planning_area,mode='indeterminate');self.plan_progress.pack(fill='x',padx=5)
        self.plan_tree = self._tree(planning_area, [('num', '编号', 55), ('status', '状态', 85), ('duration', '时长(秒)', 95),
                                                       ('count', '片段数', 65), ('bgm', 'BGM', 130), ('toptext', '顶部文案', 170),
                                                       ('sequence', '完整片段顺序', 280)], 5)
        self.plan_tree.bind('<Double-1>', self.open_output)
        self.plan_detail_bar=ttk.Frame(planning_area);self.plan_detail_bar.pack(fill='x',padx=5,pady=8)
        self._button(self.plan_detail_bar, '所选方案：裁切 / 倍速 / 转场', self.show_timing_detail).pack(side='left',padx=(0,8))
        self._button(self.plan_detail_bar, '轮换与去重汇总', self.show_dedup_summary).pack(side='left')
        video_area=ttk.LabelFrame(self.render_content,text='已冻结批次')
        self.video_area=video_area
        video_area.pack(fill='both',expand=True,padx=8,pady=5)
        video_bar=ttk.Frame(video_area);video_bar.pack(fill='x',pady=3)
        self.render_btn=self._button(video_bar,'开始 / 继续生成',self.render,style='Accent.TButton');self.render_btn.pack(side='left',padx=3)
        self.import_render_btn=self._button(video_bar,'导入过往批次',self.import_render_history);self.import_render_btn.pack(side='left',padx=3)
        self._button(video_bar,'移除选中排队批次',self.remove_selected_queued).pack(side='left',padx=3)
        self.pause_btn = ttk.Button(video_bar, text='本条完成后暂停视频', command=self.request_pause, state='disabled');self.pause_btn.pack(side='left', padx=3)
        self.abort_btn = ttk.Button(video_bar, text='取消当前视频生成', command=self.request_abort, state='disabled');self.abort_btn.pack(side='left', padx=3)
        self._button(video_bar,'打开生成目录',self.open_render_directory).pack(side='right',padx=3)
        self.build_review_controls(video_area)
        self.video_rights_control=ttk.Checkbutton(video_area,text='我确认本批次音乐可用于本次用途',variable=self.rights_var)
        self.video_rights_control.pack(anchor='w',padx=5,pady=6)
        self.render_status_var=tk.StringVar(value='尚未开始视频生成')
        ttk.Label(video_area,textvariable=self.render_status_var).pack(anchor='w',padx=5)
        self.render_progress=ttk.Progressbar(video_area,mode='indeterminate');self.render_progress.pack(fill='x',padx=5)
        self.render_tree=self._tree(video_area,[('num','编号',55),('status','状态',85),('duration','时长(秒)',95),('count','片段数',65),('bgm','BGM',150),('toptext','顶部文案',240),('sequence','完整片段顺序',430)],12)
        self.render_tree.bind('<Double-1>',self.open_render_output)
        self.queue_status=tk.StringVar(value='待剪辑队列：0批')
        ttk.Label(video_area,textvariable=self.queue_status).pack(fill='x',padx=5,pady=4)
        ttk.Label(self.render_content,text='历史按各批次自身规则保留。导入只载入方案，点击开始后才生成视频。',style='Subtitle.TLabel').pack(anchor='w',padx=10,pady=8)
        self.progress = ttk.Progressbar(self.main_footer, mode='indeterminate')
        self.progress.pack(fill='x', padx=9, pady=(0, 4))
        self.status_var = tk.StringVar(value='先扫描素材和音乐，再确认复核。自然音乐模式需先确认可用区间。')
        self.status_label=ttk.Label(self.main_footer, textvariable=self.status_var, wraplength=980,style='Subtitle.TLabel');self.status_label.pack(fill='x', padx=10, pady=3)
        log_head=ttk.Frame(self.main_footer);log_head.pack(fill='x',padx=9)
        ttk.Label(log_head,text='最新日志（固定显示）').pack(side='left')
        ttk.Button(log_head,text='查看完整日志',command=lambda:self.tabs.select(self.log_tab)).pack(side='right')
        log_area=ttk.Frame(self.main_footer);log_area.pack(fill='x',padx=9,pady=(3,8))
        self.log_box = tk.Text(log_area, height=2, wrap='word', bg='#fff', relief='flat', borderwidth=0,highlightthickness=1,highlightbackground='#E2E8F0',highlightcolor='#0F766E')
        self.log_box.pack(side='left',fill='x',expand=True)
        log_scroll=ttk.Scrollbar(log_area,command=self.log_box.yview);log_scroll.pack(side='right',fill='y')
        self.log_box.configure(yscrollcommand=log_scroll.set)
        for progress in (self.progress,self.plan_progress,self.render_progress):progress_visible(progress,False)

        media_top = ttk.Frame(self.media_tab)
        media_top.pack(fill='x', padx=9, pady=10)
        ttk.Label(media_top, text='双击播放；勾选复核针对当前文件指纹，修改后的新内容需要重新确认。').pack(anchor='w')
        for label, fn in [('启用所选', lambda: self.toggle_media(True)), ('停用所选', lambda: self.toggle_media(False)),
                          ('确认所选整段可用', self.approve_selected), ('播放所选', self.play_media)]:
            self._button(media_top, label, fn).pack(side='left', padx=3, pady=6)
        self.media_tree = self._tree(self.media_tab, [('group', '分类', 65), ('open', '可作首镜', 80),
                       ('mutex', '互斥组', 160), ('member', '组内成员', 80), ('file', '完整相对路径', 330), ('duration', '秒数', 75),
                       ('size', '画幅', 100), ('audio', '含原声', 75), ('enabled', '启用', 60),
                       ('review', '整段复核', 85), ('link', '索引关联', 120)], 18)
        self.media_tree.bind('<Double-1>', lambda e: self.guard(self.play_media))
        self.media_summary = tk.StringVar(value='未扫描')
        ttk.Label(self.media_tab, textvariable=self.media_summary, wraplength=1150).pack(fill='x', padx=12, pady=10)

        music_top = ttk.Frame(self.music_tab)
        music_top.pack(fill='x', padx=10, pady=10)
        ttk.Label(music_top, text='自然区间由你确认，不使用AI猜高潮。可给同一首歌保存多个起止区间。').pack(anchor='w')
        self.music_tree = self._tree(self.music_tab, [('name', '音乐文件', 440), ('duration', '总时长(秒)', 120), ('ranges', '已确认区间', 160)], 6)
        self.music_tree.bind('<Double-1>', lambda e: self.guard(self.play_music))
        music_edit = ttk.LabelFrame(self.music_tab, text='给选中音乐添加自然区间')
        music_edit.pack(fill='x', padx=10, pady=8)
        self.range_start = tk.StringVar(value='0')
        self.range_end = tk.StringVar(value='')
        self.range_note = tk.StringVar(value='')
        for col, text, var, width in [(0, '开始秒', self.range_start, 9), (2, '结束秒', self.range_end, 9), (4, '备注', self.range_note, 23)]:
            ttk.Label(music_edit, text=text).grid(row=0, column=col, padx=5, pady=8)
            e = ttk.Entry(music_edit, textvariable=var, width=width)
            e.grid(row=0, column=col+1, padx=3, pady=8)
            self.controls.append(e)
        self._button(music_edit, '确认此区间', self.add_range).grid(row=0, column=6, padx=6)
        extra = ttk.Frame(self.music_tab)
        extra.pack(fill='x', padx=10)
        self._button(extra, '播放所选音乐', self.play_music).pack(side='left', padx=3)
        self._button(extra, '预听当前填写区间', self.preview_music_range).pack(side='left', padx=3)
        self._button(extra, '确认所选音乐整首起止自然', self.add_full_ranges).pack(side='left', padx=3)
        self._button(extra, '删除选中区间', self.delete_ranges).pack(side='left', padx=3)
        self.range_tree = self._tree(self.music_tab, [('name', '音乐', 310), ('start', '开始秒', 110), ('end', '结束秒', 110), ('duration', '时长', 110), ('note', '备注', 310)], 7)
        ttk.Label(self.music_tab, text='自然模式精确匹配到输出帧网格；找不到组合就提示。允许截尾淡出模式从音乐0秒开始，但不保证乐句自然结束。', wraplength=1100).pack(fill='x', padx=12, pady=10)

        text_cfg = ttk.LabelFrame(self.text_tab, text='顶部文案设置')
        text_cfg.pack(fill='x', padx=10, pady=10)
        ttk.Label(text_cfg, text='顶部文案使用比例与显示时机在第1页设置；此页保留文案内容和样式。').grid(
            row=0, column=0, columnspan=3, sticky='w', padx=8, pady=8)
        ttk.Label(text_cfg, text='文案库').grid(row=1, column=0, sticky='w', padx=8, pady=6)
        self._entry(text_cfg, 'top_text_library', 65).grid(row=1, column=1, sticky='ew', padx=6, pady=6)
        def choose_text_library():
            pth = filedialog.askopenfilename(title='选择顶部文案库', filetypes=[('文本文件', '*.txt'), ('所有文件', '*.*')], parent=self)
            if pth:
                self.var('top_text_library').set(pth)
                self.refresh_text_preview()
        self._button(text_cfg, '选择', choose_text_library).grid(row=1, column=2, padx=6, pady=6)
        ttk.Label(text_cfg, text='抽取方式').grid(row=2, column=0, sticky='w', padx=8, pady=6)
        self.top_text_mode_var = tk.StringVar(value='顺序' if self.cfg.get('top_text_mode') == 'sequence' else '随机去重')
        modebox = ttk.Combobox(text_cfg, textvariable=self.top_text_mode_var, values=('随机去重', '顺序'), width=16, state='readonly')
        modebox.grid(row=2, column=1, sticky='w', padx=6, pady=6)
        self.controls.append(modebox)
        ttk.Label(text_cfg, text='固定文案编号（0=自动）').grid(row=2, column=1, sticky='e', padx=(0, 190), pady=6)
        self._entry(text_cfg, 'top_text_fixed_index', 8).grid(row=2, column=1, sticky='e', padx=6, pady=6)
        style = ttk.LabelFrame(self.text_tab, text='显示样式')
        style.pack(fill='x', padx=10, pady=8)
        from top_text_editor import open_editor as open_top_text_editor
        self._button(style,'字体样式 / 拖动位置',lambda:open_top_text_editor(self)).pack(side='left',padx=8,pady=8)
        ttk.Label(style,text='清晰白字、暖黄醒目、紧凑黑体、白色宋体；可拖动、居中对齐。\n仅改变顶部文案，新方案生效；首次应用前保留原样式。',wraplength=800).pack(side='left',padx=8,pady=8)
        preview_bar = ttk.Frame(self.text_tab)
        preview_bar.pack(fill='x', padx=10, pady=6)
        self._button(preview_bar, '刷新文案预览', self.refresh_text_preview).pack(side='left', padx=3)
        self._button(preview_bar, '打开文案库', self.open_text_library).pack(side='left', padx=3)
        self.text_preview = tk.Text(self.text_tab, height=16, wrap='word', font=('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC', 10))
        self.text_preview.pack(fill='both', expand=True, padx=10, pady=(2, 10))
        self.text_preview.configure(state='disabled')
        ttk.Label(self.text_tab, text='文案以空行分隔；每条内部换行会原样显示。批量生成时随机模式优先不重复；文案只叠加在最终画布顶部，不影响素材时长和BGM。', wraplength=1120).pack(fill='x', padx=12, pady=(0, 12))
        self.after(250, self.refresh_text_preview)

        self.build_processing_tab()
        self.build_timing_tab()

        env = ttk.Frame(self.settings_content)
        env.pack(fill='x', padx=9, pady=8)
        for row, label, key, is_file in [(0, 'ffmpeg', 'ffmpeg', True), (1, 'ffprobe', 'ffprobe', True), (2, 'Node.js', 'node', True),
                                         (3, '已有Remotion工程', 'remotion_project', False), (4, 'Chrome路径（可空）', 'browser', True),
                                         (5, '原项目素材索引（只读）', 'index_path', True)]:
            self._pathrow(env, row, label, key, is_file)
        ttk.Label(env, text='工具留空时自动查PATH。Remotion优先复用你已有工程依赖；不会修改其代码。索引只作身份关联，不自动审批或更新状态。', wraplength=1150).grid(row=6, column=0, columnspan=3, sticky='w', padx=7, pady=8)
        output_cfg = ttk.LabelFrame(self.settings_content, text='渲染与搜索参数')
        output_cfg.pack(fill='x', padx=10, pady=8)
        items = [('宽度', 'width'), ('高度', 'height'), ('帧率', 'fps'), ('CRF', 'crf'), ('渲染并发', 'concurrency'),
                 ('BGM音量0～1', 'bgm_gain'), ('淡出秒', 'fade_out'), ('淡入秒', 'fade_in'), ('单条片段上限', 'max_clips'),
                 ('单条搜索秒数', 'plan_seconds_per_video'), ('随机种子(可空)', 'seed')]
        for i, (label, key) in enumerate(items):
            r, col = divmod(i, 4)
            ttk.Label(output_cfg, text=label).grid(row=r*2, column=col, padx=9, pady=(8, 0), sticky='w')
            self._entry(output_cfg, key, 17).grid(row=r*2+1, column=col, padx=9, pady=(3, 8), sticky='w')
            output_cfg.columnconfigure(col, weight=1)
        subtitle_cfg = ttk.LabelFrame(self.settings_content, text='字幕高级布局 · XHS_9_16_V1')
        subtitle_cfg.pack(fill='x', padx=10, pady=8)
        for i, (label, key) in enumerate((
            ('字号','subtitle_font_size'),('每行字数','subtitle_max_chars_per_line'),
            ('上边距','subtitle_safe_margin_top'),('下边距','subtitle_safe_margin_bottom'),
            ('左边距','subtitle_safe_margin_left'),('右边距','subtitle_safe_margin_right'))):
            ttk.Label(subtitle_cfg,text=label).grid(row=0,column=i,padx=7,pady=(6,1),sticky='w')
            self._entry(subtitle_cfg,key,10).grid(row=1,column=i,padx=7,pady=(1,7),sticky='w')
        groups = ttk.LabelFrame(self.settings_content, text='素材根目录下的文件夹名称')
        self.group_folder_controls=groups
        groups.pack(fill='x', padx=10, pady=8)
        self.group_vars = {}
        self.sync_group_folder_controls()
        env_buttons = ttk.Frame(self.settings_content)
        env_buttons.pack(fill='x', padx=10, pady=8)
        for label, fn in [('保存设置', self.save_settings), ('环境检查', self.check_env), ('安装独立Remotion依赖（首次可选）', self.install_renderer),
                          ('打开数据目录', lambda: open_local(str(self.data_dir))),
                          ('清理临时文件', self.cleanup_temporary_files)]:
            self._button(env_buttons, label, fn).pack(side='left', padx=3)
        ttk.Label(self.settings_content, text='原声固定去除，不能开启。默认完整原速；导出处理可按明确设置调色/镜像/变速/抽帧。不同画幅保持比例居中。\n暂停在当前成片完成后生效。配音使用本机 IndexTTS-2.5 常驻服务。', wraplength=1150).pack(fill='x', padx=13, pady=10)

    def sync_group_folder_controls(self):
        """Load the selected scheme's folder fields, including changed group keys."""
        groups = groups_for_config(self.cfg)
        if tuple(self.group_vars) != tuple(groups):
            for child in self.group_folder_controls.winfo_children():
                if child in self.controls:
                    self.controls.remove(child)
                child.destroy()
            self.group_vars = {}
            for i, group in enumerate(groups):
                ttk.Label(self.group_folder_controls, text=group).grid(row=0, column=i*2, padx=7, pady=8)
                var = tk.StringVar(value=self.cfg['group_folders'][group])
                self.group_vars[group] = var
                entry = ttk.Entry(self.group_folder_controls, textvariable=var, width=15)
                entry.grid(row=0, column=i*2+1, padx=5)
                self.controls.append(entry)
        else:
            for group, var in self.group_vars.items():
                var.set(self.cfg['group_folders'][group])

    def build_processing_tab(self):
        self.processing_bools = {}
        self.processing_scope_vars = {}
        outer = ttk.Frame(self.processing_tab)
        outer.pack(fill='both', expand=True, padx=12, pady=10)
        ttk.Label(outer, text='导出变体处理', font=('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC', 15, 'bold')).pack(anchor='w')
        ttk.Label(outer, text='所有效果只作用于新成片，不覆盖原素材。镜像/抽帧/调色/动态缩放可以整段、随机几个、随机比例或指定镜头位置；随机结果在“先看组合方案”时冻结。', wraplength=1090).pack(anchor='w', pady=(5, 9))

        def toggle(parent, key, text, row=None):
            var = tk.BooleanVar(value=bool(self.cfg.get(key, PROCESSING_DEFAULTS[key])))
            self.processing_bools[key] = var
            cb = ttk.Checkbutton(parent, text=text, variable=var)
            if row is None:
                cb.pack(anchor='w', pady=5)
            else:
                cb.grid(row=row, column=0, sticky='w', padx=9, pady=7)
            self.controls.append(cb)
            return cb

        scope_labels = {
            'all': '整条成片',
            'random_count': '随机几个片段',
            'random_probability': '随机比例',
            'positions': '指定片段位置',
        }
        scope_reverse = {v:k for k,v in scope_labels.items()}
        self._processing_scope_reverse = scope_reverse

        def scope_controls(parent, row, prefix, extra_builder=None):
            area = ttk.Frame(parent)
            area.grid(row=row, column=1, sticky='w', padx=8, pady=4)
            ttk.Label(area, text='范围').pack(side='left')
            var = tk.StringVar(value=scope_labels.get(self.cfg.get(f'{prefix}_scope', PROCESSING_DEFAULTS[f'{prefix}_scope']), '整条成片'))
            self.processing_scope_vars[prefix] = var
            cb = ttk.Combobox(area, textvariable=var, values=tuple(scope_labels.values()), state='readonly', width=13)
            cb.pack(side='left', padx=(4,10)); self.controls.append(cb)
            ttk.Label(area, text='随机数量').pack(side='left')
            self._entry(area, f'{prefix}_random_min', 4).pack(side='left', padx=(4,1))
            ttk.Label(area, text='～').pack(side='left')
            self._entry(area, f'{prefix}_random_max', 4).pack(side='left', padx=(1,8))
            ttk.Label(area, text='比例').pack(side='left')
            self._entry(area, f'{prefix}_probability', 5).pack(side='left', padx=(4,8))
            ttk.Label(area, text='位置').pack(side='left')
            self._entry(area, f'{prefix}_positions', 12).pack(side='left', padx=(4,8))
            if extra_builder:
                extra_builder(area)
            return area

        toggle(outer, 'processing_enabled', '启用镜像 / 抽帧 / 输出处理（转场与画面特效在独立入口设置）')
        box = ttk.LabelFrame(outer, text='Clip级变体（片段编号按最终时间线从1开始）')
        box.pack(fill='x', pady=6)

        toggle(box, 'processing_mirror', '水平镜像', 0)
        scope_controls(box, 0, 'processing_mirror')
        ttk.Label(box, text='指定位置示例：2,4,6-8。顶部文案不会反向；源画面已有文字/招牌会反向。', wraplength=1030).grid(row=1, column=1, sticky='w', padx=8, pady=(0,4))

        toggle(box, 'processing_drop', '抽帧 / 画面降采样', 2)
        def drop_extra(area):
            ttk.Label(area, text='每').pack(side='left')
            self._entry(area, 'processing_drop_every', 4).pack(side='left', padx=3)
            ttk.Label(area, text='帧丢1个采样').pack(side='left')
        scope_controls(box, 2, 'processing_drop', drop_extra)
        ttk.Label(box, text='只对被选中的Clip生成静音派生缓存；保持片段时间长度，运动采样可能变稀，先少量测试。', wraplength=1030).grid(row=3, column=1, sticky='w', padx=8, pady=(0,4))

        self._button(outer, '转场 / 画面特效设置（调色、缩放、暗角、柔光）', self.open_visual_settings).pack(anchor='w',pady=6)
        box.columnconfigure(1, weight=1)

        whole = ttk.LabelFrame(outer, text='整条成片级处理')
        whole.pack(fill='x', pady=6)
        ttk.Label(whole, text='片段级变速、头尾修剪和转场在第6页设置。').grid(row=0, column=0, columnspan=2, sticky='w', padx=9, pady=8)
        toggle(whole, 'processing_fps_enabled', '指定输出帧率', 1)
        fr = ttk.Frame(whole); fr.grid(row=1, column=1, sticky='w', padx=8)
        cmb = ttk.Combobox(fr, textvariable=self.var('processing_fps'), values=('24', '25', '30', '50', '60'), state='readonly', width=6)
        cmb.pack(side='left'); self.controls.append(cmb)
        ttk.Label(fr, text=' fps；整条成片统一输出，不等于倍速。').pack(side='left', padx=7)
        toggle(whole, 'processing_hash_refresh', '文件指纹变更（MD5）', 2)
        ttk.Label(whole, text='每条新成片写入独立导出标识并记录真实MD5；不修改原素材身份。', wraplength=850).grid(row=2, column=1, sticky='w', padx=8)
        whole.columnconfigure(1, weight=1)

        buttons = ttk.Frame(outer); buttons.pack(fill='x', pady=8)
        self._button(buttons, '保存设置', self.save_settings).pack(side='left', padx=3)
        self._button(buttons, '载入随机变体推荐', self.processing_random_preset).pack(side='left', padx=3)
        self._button(buttons, '关闭全部处理', self.disable_processing).pack(side='left', padx=3)
        self._button(buttons, '查看当前处理摘要', self.show_processing_summary).pack(side='left', padx=3)
        self._button(buttons, 'uniqvid 官网（外部工具）', self.open_uniqvid).pack(side='left', padx=3)
        ttk.Label(outer, text='随机只决定“哪些Clip应用哪些效果”，并在方案阶段保存；渲染失败重试不会再次随机。uniqvid仍仅为官网入口，未接入其私有引擎。', wraplength=1110).pack(anchor='w', pady=6)

    def build_timing_tab(self):
        self.timing_enum_vars = {}
        page = self.timing_tab
        canvas = tk.Canvas(page, highlightthickness=0, bg='#f3f5f8')
        bar = ttk.Scrollbar(page, orient='vertical', command=canvas.yview)
        bar.pack(side='right', fill='y'); canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=bar.set)
        body = ttk.Frame(canvas)
        window = canvas.create_window(0, 0, window=body, anchor='nw')
        body.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>', lambda e: canvas.itemconfigure(window, width=e.width))
        # Local wheel bindings only; do not override the other pages' scrollbars.
        def wheel(e):
            canvas.yview_scroll(int(-e.delta / 120) if e.delta else (1 if e.num == 5 else -1), 'units')
        def bind_wheel(widget):
            widget.bind('<MouseWheel>', wheel, add='+')
            widget.bind('<Button-4>', wheel, add='+'); widget.bind('<Button-5>', wheel, add='+')
            for child in widget.winfo_children(): bind_wheel(child)
        ttk.Label(body, text='时长裁切 · 节奏 · 画面效果', font=('Microsoft YaHei UI' if os.name == 'nt' else 'Noto Sans CJK SC', 15, 'bold')).pack(anchor='w', padx=12, pady=(10,4))
        cb = ttk.Checkbutton(body, text='启用裁片 / 画面变速处理（转场和画面特效由下方独立选择）', variable=self.processing_bools['processing_enabled'])
        cb.pack(anchor='w', padx=12, pady=4); self.controls.append(cb)
        ttk.Label(body, text='配音时长目标在第1页控制。这里是额外裁片和效果设置；裁片、变速、转场统一参与规划，不修改原视频。', wraplength=1080).pack(anchor='w', padx=12, pady=4)
        quick=ttk.LabelFrame(body,text='常用设置');quick.pack(fill='x',padx=12,pady=6)
        duration_details=ttk.Frame(body)
        visual_details=ttk.Frame(body)
        duration_boxes={}

        def toggle(parent,key,label):
            var=tk.BooleanVar(value=bool(self.cfg.get(key,PROCESSING_DEFAULTS[key])))
            self.processing_bools[key]=var
            c=ttk.Checkbutton(parent,text=label,variable=var);c.pack(anchor='w',padx=8,pady=4);self.controls.append(c)
        def row(parent):
            f=ttk.Frame(parent);f.pack(fill='x',padx=8,pady=4);return f
        def number(parent,label,key,width=6):
            ttk.Label(parent,text=label).pack(side='left',padx=(2,4))
            entry=self._entry(parent,key,width);entry.pack(side='left',padx=(0,10));return entry
        def enum(parent,key,mapping,width=14):
            reverse={v:k for k,v in mapping.items()}
            var=tk.StringVar(value=reverse.get(self.cfg.get(key,PROCESSING_DEFAULTS.get(key,TIMELINE_DEFAULTS.get(key))),next(iter(mapping))))
            cb=ttk.Combobox(parent,textvariable=var,values=tuple(mapping),state='readonly',width=width)
            cb.pack(side='left',padx=(0,10));self.controls.append(cb)
            self.timing_enum_vars[key]=(var,mapping)
            return var
        def scope(parent,name,boundary=False):
            area=row(parent);p='processing_'+name
            all_label='整条 / 所有切点' if boundary else ('所有A/B片段' if name=='ab_duration' else '所有片段')
            mapping={all_label:'all','随机几个':'random_count','随机比例':'random_probability','指定位置':'positions'}
            choice=enum(area,p+'_scope',mapping,15)
            entries=[(number(area,'随机数量',p+'_random_min',3),'random_count'),
                     (number(area,'～',p+'_random_max',3),'random_count'),
                     (number(area,'比例0～1',p+'_probability',5),'random_probability'),
                     (number(area,'位置',p+'_positions',14),'positions')]
            def sync_scope(*_):
                for entry,expected in entries:
                    entry.configure(state='normal' if mapping[choice.get()]==expected else 'disabled')
            choice.trace_add('write',sync_scope);sync_scope()
        def box(title):
            number=int(title[0])
            parent=duration_details if number<=3 else visual_details
            b=ttk.LabelFrame(parent,text=title);b.pack(fill='x',pady=6)
            if number<=3:duration_boxes[('processing_trim','processing_ab_duration_enabled','processing_micro_trim_enabled')[number-1]]=b
            return b
        values={'固定数值':'fixed','随机范围':'random'}

        b=box('1 头尾修剪（秒数按原片时间计，不是变速后的秒数）')
        toggle(b,'processing_trim','启用头尾修剪')
        scope(b,'trim')
        r=row(b);enum(r,'processing_trim_mode',{'仅去片头':'head','仅去片尾':'tail','片头片尾都去':'both','随机选片头或片尾':'random_side'},18)
        enum(r,'processing_trim_values',values)
        number(r,'固定去头秒','processing_trim_head');number(r,'固定去尾秒','processing_trim_tail')
        r=row(b);number(r,'随机去头','processing_trim_head_min');number(r,'～','processing_trim_head_max')
        number(r,'随机去尾','processing_trim_tail_min');number(r,'～','processing_trim_tail_max')
        toggle(b,'processing_trim_protect_first','保护整条视频首镜片头（开启时，第1段不去头）')
        toggle(b,'processing_trim_protect_last','保护整条视频最后片尾（开启时，最后1段不去尾）')
        r=row(b);number(r,'处理后至少保留秒','processing_min_kept_seconds')
        ttk.Label(r,text='太短则跳过该段的修剪/变速，并在方案明细中注明。').pack(side='left')

        b=box('2 A/B随机片段时长（A=开头，B=中间；C/D不受影响）')
        toggle(b,'processing_ab_duration_enabled','启用开头/中间随机时长（默认关闭）')
        r=row(b);number(r,'可选播放秒数','processing_ab_duration_values',18)
        ttk.Label(r,text='例如3,4,5；只填4表示固定4秒。按变速后、转场前的独立播放时长。').pack(side='left')
        scope(b,'ab_duration')
        r=row(b);enum(r,'processing_ab_duration_mode',{'保留前段':'front','保留后段':'back','居中取段':'center','随机起点':'random'},12)
        toggle(b,'processing_ab_duration_protect_first','首镜保留原开场：从原片0秒开始（优先于去片头）')
        ttk.Label(b,text='随机数量只从A/B片段计算；指定位置落在C/D会跳过并说明。按长度取段，不是AI动作完整性判断；随机起点可能截断动作，原文件不修改。',wraplength=1080).pack(anchor='w',padx=8,pady=4)

        b=box('3 无配音匹配时：裁剪镜头的作用范围（高级）')
        toggle(b,'processing_micro_trim_enabled','启用裁剪镜头')
        scope(b,'micro_trim')
        r=row(b)
        enum(r,'processing_micro_trim_mode',{'随机选片头或片尾':'random_side','仅去片头':'head','仅去片尾':'tail'},18)
        toggle(b,'processing_micro_trim_protect_first','保护首镜片头')
        toggle(b,'processing_micro_trim_protect_last','保护最后片尾')
        ttk.Label(b,text='裁剪秒数、区间和最短镜头在上方常用设置；这里仅设置要裁哪些镜头。配音匹配任务由实际差额选择镜头，不受此随机数量限制。',wraplength=1080).pack(anchor='w',padx=8,pady=4)

        b=box('4 片段级变速（未选中的片段保持1倍；BGM始终自身速度）')
        toggle(b,'processing_speed_enabled','启用片段变速')
        scope(b,'speed')
        r=row(b);enum(r,'processing_speed_values',values)
        number(r,'固定倍速','processing_speed');number(r,'随机倍速','processing_speed_min');number(r,'～','processing_speed_max')
        ttk.Label(b,text='例如：范围“指定位置”，填写2,4；固定1.10倍。只变第2和第4段，不是整条一起变。',wraplength=1080).pack(anchor='w',padx=8,pady=4)

        b=box('5 自定义转场（仅选择“自定义转场”时生效）')
        self.custom_transition_box=b
        toggle(b,'processing_transition','启用转场')
        scope(b,'transition',True)
        r=row(b);enum(r,'processing_transition_type',{'叠化':'fade','柔和淡暗':'fade_black','向左推移':'slide_left','向右推移':'slide_right','从左擦入':'wipe_left','从右擦入':'wipe_right','模糊叠化':'blur_fade','随机类型':'random'},14)
        enum(r,'processing_transition_values',values)
        number(r,'固定秒','processing_transition_seconds');number(r,'随机秒','processing_transition_min');number(r,'～','processing_transition_max')
        ttk.Label(b,text='重叠转场会缩短总时长；每个转场最多占相邻片段各1/3，防止连续转场把短片段全部盖住。首帧/末帧不额外闪白。',wraplength=1080).pack(anchor='w',padx=8,pady=4)

        b=box('6 自定义画面特效（仅选择“自定义画面特效”时生效）')
        self.custom_effect_box=b
        toggle(b,'processing_fx','启用画面特效')
        scope(b,'fx')
        r=row(b);enum(r,'processing_fx_type',{'轻微暗角':'vignette','柔光叠层':'soft_light','随机两者':'random'})
        number(r,'强度0～0.6','processing_fx_strength')
        toggle(b,'processing_color','启用调色')
        scope(b,'color')
        r=row(b)
        for label,key in [('亮度','processing_brightness'),('对比','processing_contrast'),('饱和','processing_saturation')]:number(r,label,key)
        toggle(b,'processing_zoom','启用动态缩放')
        scope(b,'zoom')
        r=row(b);number(r,'缩放最小','processing_zoom_min');number(r,'～','processing_zoom_max')
        ttk.Label(b,text='自定义内勾选的效果可叠加；镜像、抽帧、输出帧率在“画面处理”单独控制。字幕及顶部文案不跟随这些效果。',wraplength=1080).pack(anchor='w',padx=8,pady=4)

        buttons=row(body)
        self.timing_footer=buttons
        from timing_controls_ui import TimingChooser
        self.timing_chooser=TimingChooser(self,quick,duration_details,visual_details,duration_boxes)
        self._button(buttons,'保存设置',self.save_settings).pack(side='left',padx=3)
        self._button(buttons,'关闭全部处理',self.disable_processing).pack(side='left',padx=3)
        self._button(buttons,'查看当前处理摘要',self.show_processing_summary).pack(side='left',padx=3)
        ttk.Label(body,text='设置改变后请回第1页重新“先看组合方案”。处理结果会保存到方案；暂停、重试或继续同一批不重新随机。',wraplength=1080).pack(anchor='w',padx=12,pady=(4,14))
        self.after_idle(lambda: bind_wheel(body))

    def show_timing_detail(self):
        if not self.batch: raise AssemblyError('请先生成或打开一个组合方案。')
        selected=self.plan_tree.selection()
        if not selected:raise AssemblyError('先在方案列表选择一条视频。')
        item=next((x for x in self.batch['items'] if x['id']==selected[0]),None)
        if not item:
            failed=next((f for f in self.batch.get('planning_failed_tasks',[]) if selected[0]=='planning_failure_'+str(f['number'])),None)
            if failed:messagebox.showerror('选片失败',failed['error'],parent=self)
            return
        if item.get('sk_snapshot'):
            from sk_ui import show_report
            return show_report(self,item)
        if getattr(self,'_qt_parent',None):
            from auxiliary_dialogs_qt import timing_detail
            self._timing_detail=timing_detail(self,item);return
        win=tk.Toplevel(self);win.title(f"方案{item['number']}：处理明细（只读）");win.geometry('1140x620')
        ttk.Label(win,text=f"最终时长 {item['duration']:.3f}秒；转场总重叠 {item.get('transition_overlap_seconds',0):.3f}秒；BGM自身不变速；原素材不修改。").pack(anchor='w',padx=10,pady=10)
        if item.get('voice_fit_snapshot'):
            fit=item['voice_fit_snapshot'];fps=self.batch['config']['fps']
            words=(f"配音差额裁切：候选 {fit['candidate_frames']/fps:.3f}秒 → 目标 {fit['target_frames']/fps:.3f}秒；"
                   f"实际减时 {fit['removed_timeline_frames']/fps:.3f}秒。随机位置已冻结；未叠加额外随机裁片。")
            ttk.Label(win,text=words,wraplength=1080).pack(anchor='w',padx=10,pady=3)
        if item.get('grouped_revision'):
            anchor=item.get('package_anchor')
            text='覆盖去重：冻结关系验证通过。配音 '+str(item.get('effective_voice_duration','不适用'))+'秒。'
            if anchor:text+=f" 包装口播「{anchor['phrase']}」{anchor['start_seconds']:.3f}～{anchor['end_seconds']:.3f}秒；包装槽："+'；'.join(f"{c['relative_path']} {c['timeline_start_frame']/self.batch['config']['fps']:.3f}～{(c['timeline_start_frame']+c['duration_frames'])/self.batch['config']['fps']:.3f}" for c in item['clips'] if c.get('planning_block')=='package')
            ttk.Label(win,text=text,wraplength=1080).pack(anchor='w',padx=10,pady=3)
        detail=item.get('dedup_detail')
        if detail:
            nearest=detail.get('nearest') or {}
            words=(f"去重：{detail['reason']}；最近历史：{nearest.get('other_id') or '无'}；"
                   f"账号：{nearest.get('other_account') or '未记录'}；同素材{nearest.get('same_material_count',0)}条；"
                   f"同路径不同版本线索{nearest.get('version_relation_count',0)}条；"
                   f"共享原片{nearest.get('shared_source_seconds',0):.3f}秒；本条重复展示{nearest.get('candidate_display_seconds',0):.3f}秒"
                   f"（{nearest.get('candidate_ratio',0):.1%}），对方{nearest.get('other_ratio',0):.1%}；"
                   f"连续两段/三段/首尾搭配：{nearest.get('repeated_pairs',0)}/{nearest.get('repeated_triples',0)}/{nearest.get('repeated_ends',0)}；"
                   f"区间{'完整' if nearest.get('interval_complete',True) else '未知或部分缺失'}。"
                   f"{detail['account_history_status']}。比例分母为各自片段播放时长之和，未扣转场。")
            ttk.Label(win,text=words,wraplength=1080).pack(anchor='w',padx=10,pady=4)
        tree=self._tree(win,[('pos','位置',50),('file','完整相对路径',210),('role','角色/阶段',115),('mutex','互斥组',145),('member','成员',55),
                             ('source','原长秒',65),('window','原片采用区间',120),('rate','倍速',55),
                             ('duration','输出段长秒',80),('transition','本段之后转场',135),('fx','效果 / 说明',240)],12)
        types={'none':'无','fade':'叠化','fade_black':'柔和淡暗','slide_left':'左推','slide_right':'右推','wipe_left':'左擦','wipe_right':'右擦','blur_fade':'模糊叠化'}
        for i,c in enumerate(item['clips']):
            t=c.get('outgoing_transition',{'type':'none','frames':0});eff=c.get('effects',{})
            desc=[]
            if c.get('source_group'):desc.append(c['source_group']+' / '+c['combo_id']+' / '+('组合' if c['asset_kind']=='combo' else '独立'))
            if eff.get('mirror'):desc.append('镜像')
            if eff.get('drop'):desc.append('抽帧')
            if eff.get('color'):desc.append('调色')
            if eff.get('zoom'):desc.append('缩放')
            if eff.get('fx_type') not in (None,'none'):desc.append({'vignette':'暗角','soft_light':'柔光'}.get(eff['fx_type'],eff['fx_type']))
            if c.get('random_duration_applied'):desc.append(f"A/B时长目标{c.get('target_playback_seconds') or '可用全段'}秒；{c.get('window_mode')}")
            if c.get('micro_trim_applied'):desc.append(f"随机微裁：去{'片头' if c['micro_trim_side']=='head' else '片尾'}{c['micro_trim_removed_seconds']:g}秒")
            if c.get('protection_reason'):desc.append(c['protection_reason'])
            desc.extend(c.get('edit_notes',[]))
            tree.insert('','end',values=(i+1,c.get('relative_path',c['display']),c.get('timeline_role','旧方案'),c.get('mutex_group','旧方案'),c.get('member_number') if c.get('member_number') is not None else '—',f"{c['duration']:.3f}",f"{c.get('source_in_seconds',0):.3f}～{c.get('source_out_seconds',c['duration']):.3f}",f"{c.get('playback_rate',1):.3f}",
                f"{c['duration_frames']/self.batch['config']['fps']:.3f}",f"{types.get(t['type'],t['type'])} {t['frames']/self.batch['config']['fps']:.3f}秒",'；'.join(desc) or '原样'))
        ttk.Label(win,text='片段编号按本条最终顺序从1开始；修剪值是原片秒数。只读明细不改变方案，调整设置后需另建新方案。',wraplength=1080).pack(anchor='w',padx=10,pady=8)
        ttk.Button(win,text='关闭',command=win.destroy).pack(pady=8)

    def show_dedup_summary(self):
        if self.batch and self.batch.get('sk_summary'):
            from sk_ui import show_report
            return show_report(self)
        if not self.batch: raise AssemblyError('请先生成或打开一个组合方案。')
        summary=self.batch.get('selection_v2_summary')
        if not summary:
            messagebox.showinfo('批次去重汇总','此旧批次没有V2轮换汇总；继续执行仍按旧Manifest。',parent=self)
            return
        labels=summary.get('material_labels',{})
        lines=[f"已确认候选{summary['eligible_materials']}种；本批未使用{summary['unused_eligible_materials']}种。",
               f"各组使用次数：{summary['group_uses']}",f"各组覆盖：{summary.get('group_coverage',{})}",
               f"主要瓶颈：{summary['bottlenecks']}"]
        stage=self.batch.get('stage_summary')
        if stage:
            lines.insert(0,f"分类数量：{stage['category_counts']}；首镜候选{stage['open_candidates']}条（A {stage['open_from_a']}，C/开头 {stage['open_from_c']}）。")
            if 'c_open_min_requested' in stage:
                lines.insert(1,f"C/开头首镜：最低目标{stage['c_open_min_requested']}条，实际{stage['c_open_actual']}条。")
        for title,field in (('首镜分配','first_distribution'),('结尾分配','last_distribution')):
            lines.extend(['',title])
            for key,count in sorted(summary[field].items(),key=lambda x:(-x[1],labels.get(x[0],x[0]))):
                lines.append(f"  {labels.get(key,key)}  {count}次  [{key[-12:]}]")
        if getattr(self,'_qt_parent',None):
            from auxiliary_dialogs_qt import text_window
            self._dedup_summary=text_window(self,'本批轮换与去重汇总','\n'.join(lines));return
        win=tk.Toplevel(self);win.title('本批轮换与去重汇总');win.geometry('860x620')
        area=tk.Text(win,wrap='word');area.pack(side='left',fill='both',expand=True,padx=(10,0),pady=10)
        scroll=ttk.Scrollbar(win,orient='vertical',command=area.yview)
        scroll.pack(side='right',fill='y',pady=10,padx=(0,10));area.configure(yscrollcommand=scroll.set)
        area.insert('1.0','\n'.join(lines));area.configure(state='disabled')


    def disable_processing(self):
        self.timing_enum_vars['transition_mode'][0].set('直接切换')
        self.timing_enum_vars['picture_effect_mode'][0].set('原画（关闭画面特效）')
        for key, var in self.processing_bools.items():
            if key not in ('processing_trim_protect_first','processing_trim_protect_last','processing_ab_duration_protect_first','processing_micro_trim_protect_first','processing_micro_trim_protect_last','processing_min_kept_enabled'):
                var.set(False)
        for key in ('processing_brightness', 'processing_contrast', 'processing_saturation', 'processing_speed'):
            self.var(key).set('1.0')
        self.var('processing_zoom_min').set('1.015')
        self.var('processing_zoom_max').set('1.04')
        self.log('全部导出效果已关闭；请保存并重新规划，旧批次保持原参数。')

    def processing_random_preset(self):
        self.processing_bools['processing_enabled'].set(True)
        for key in ('processing_mirror','processing_drop','processing_zoom'):
            self.processing_bools[key].set(True)
        self.processing_bools['processing_color'].set(False)
        for k in ('processing_trim','processing_speed_enabled','processing_transition','processing_fx'):
            if k in self.processing_bools: self.processing_bools[k].set(False)
        self.processing_scope_vars['processing_mirror'].set('随机几个片段')
        self.processing_scope_vars['processing_drop'].set('随机几个片段')
        self.timing_enum_vars['processing_zoom_scope'][0].set('随机几个')
        self.timing_enum_vars['picture_effect_mode'][0].set('自定义画面特效')
        self.timing_enum_vars['transition_mode'][0].set('直接切换')
        for prefix, mn, mx in (('processing_mirror',1,3),('processing_drop',1,2),('processing_zoom',1,3)):
            self.var(prefix+'_random_min').set(str(mn)); self.var(prefix+'_random_max').set(str(mx))
        self.var('processing_drop_every').set('30')
        self.var('processing_zoom_min').set('1.015'); self.var('processing_zoom_max').set('1.04')
        self.log('已载入轻量随机变体推荐：镜像1～3段、抽帧1～2段、动态缩放1～3段。请先预览1条。')

    def show_processing_summary(self):
        cfg = self.collect_config()
        notes = processing_warnings(cfg)
        s = processing_settings(cfg)
        content = '\n\n'.join(notes) if notes else '未启用导出处理。保持完整原速，仅使用BGM和当前顶部文案。'
        content += f"\n\n实际输出帧率：{s['fps']}；画面倍速：{s['speed']:g}"
        messagebox.showinfo('当前导出处理', content, parent=self)

    def open_uniqvid(self):
        if messagebox.askyesno('打开外部网站', '这只是打开uniqvid官网，不是接入或调用其引擎。\n本工具不会传送文件或自动购买服务。是否打开浏览器？', parent=self):
            webbrowser.open(UNIQVID_URL)

    def _build_video_policy_controls(self):
        ratios = self.cfg.get('video_mode_percentages') or {mode:(100 if mode ==
            self.cfg.get('script_mode', VIDEO_MODES[-1]) else 0) for mode in VIDEO_MODES}
        box = ttk.LabelFrame(self.mode_section.body, text='视频模式分配（总数按最大余数法计算）')
        box.pack(fill='x', padx=8, pady=3)
        self.mode_percent_vars = {}
        self.mode_estimates = {}
        for index, mode in enumerate(VIDEO_MODES):
            row, column = divmod(index, 2)
            cell = ttk.Frame(box); cell.grid(row=row, column=column, sticky='w', padx=8, pady=2)
            ttk.Label(cell, text=MODE_LABELS[mode], width=19).pack(side='left')
            var = tk.StringVar(value=str(ratios.get(mode, 0)))
            self.mode_percent_vars[mode] = var
            field = ttk.Entry(cell, textvariable=var, width=6)
            field.pack(side='left'); self.controls.append(field)
            ttk.Label(cell, text='%').pack(side='left', padx=3)
            estimate = tk.StringVar()
            self.mode_estimates[mode] = estimate
            ttk.Label(cell, textvariable=estimate, width=11).pack(side='left', padx=4)
        self.mode_total_var = tk.StringVar()
        ttk.Label(box, textvariable=self.mode_total_var).grid(row=0, column=2, rowspan=2, sticky='w', padx=8)

        top = ttk.LabelFrame(self.mode_section.body, text='顶部文案（独立于视频模式；内容与样式仍在第4页）')
        top.pack(fill='x', padx=8, pady=3)
        self.top_use_var = tk.StringVar(value=self.cfg.get('top_text_use_policy', 'ratio'))
        for label, value in (('不使用','none'),('全部使用','all'),('按比例','ratio')):
            ttk.Radiobutton(top, text=label, variable=self.top_use_var, value=value).pack(side='left', padx=4)
        self.top_percent_var = tk.StringVar(value=str(self.cfg.get('top_text_use_percent', 40)))
        ttk.Entry(top, textvariable=self.top_percent_var, width=5).pack(side='left', padx=2)
        ttk.Label(top, text='%').pack(side='left')
        self.top_estimate_var = tk.StringVar()
        ttk.Label(top, textvariable=self.top_estimate_var, width=11).pack(side='left', padx=7)
        ttk.Label(top, text='出现时机').pack(side='left', padx=(10,3))
        labels = {'auto':'自动','start':'开头显示','after_voice':'配音结束后显示','full':'全程显示'}
        self.top_timing_var = tk.StringVar(value=labels.get(self.cfg.get('top_text_timing'), '自动'))
        timing = ttk.Combobox(top, textvariable=self.top_timing_var, values=tuple(labels.values()),
                              state='readonly', width=15)
        timing.pack(side='left', padx=2); self.controls.append(timing)
        timing.bind('<<ComboboxSelected>>', lambda *_: self._update_video_policy_preview())
        ttk.Label(top, text='开头时长').pack(side='left', padx=(8,2))
        self.intro_seconds_var = tk.StringVar(value=str(self.cfg.get('intro_top_text_duration', 2.5)))
        self.intro_seconds_control = ttk.Entry(top, textvariable=self.intro_seconds_var, width=6)
        self.intro_seconds_control.pack(side='left')
        ttk.Label(top, text='秒').pack(side='left', padx=2)

        ttk.Label(self.mode_section.body, text='通用时长：有顶文则配音字幕结束后继续“顶文＋画面＋BGM”至计划片尾；无顶文随配音结束。均须满足最短/最长时长。',
                  foreground='#216e63').pack(anchor='w', padx=12, pady=2)
        for var in self.mode_percent_vars.values():
            var.trace_add('write', lambda *_: self._on_mode_ratios_changed())
        for var in (self.top_percent_var, self.top_use_var, self.var('count')):
            var.trace_add('write', lambda *_: self._update_video_policy_preview())
        self._update_video_policy_preview()

    def _toggle_temporary_script_modes(self):
        if self.script_source.get() == '临时输入':
            self.temporary_script_modes.pack(side='left')
        else:
            self.temporary_script_modes.pack_forget()

    def _apply_mode_preset(self, event=None):
        selected = self.mode_preset_var.get()
        if selected == '自定义混合':
            return
        label = selected.removesuffix(' 100%')
        mode = next((key for key, value in MODE_LABELS.items() if value == label), '')
        if mode not in VIDEO_MODES:
            return
        self._applying_mode_preset = True
        try:
            for name, var in self.mode_percent_vars.items():
                var.set('100' if name == mode else '0')
        finally:
            self._applying_mode_preset = False
        self._update_video_policy_preview()

    def _on_mode_ratios_changed(self):
        if not getattr(self, '_applying_mode_preset', False):
            self.mode_preset_var.set('自定义混合')
        self._update_video_policy_preview()

    def _set_production_controls(self, counts, top_count):
        voiced = counts['配音+BGM'] + counts['配音+字幕+BGM'] > 0
        subtitled = counts['字幕+BGM'] + counts['配音+字幕+BGM'] > 0
        local=voiced and (not hasattr(self,'voice_source_var') or self.voice_source_var.get()!='外部配音文件')
        self.voice_strategy_control.state(['!disabled' if local else 'disabled'])
        for control in (self.audio_mix_control,self.voice_gain_control):
            control.state(['!disabled' if voiced else 'disabled'])
        for control, profile in zip(self.voice_frame.winfo_children(), self.voice_profiles):
            control.state(['!disabled' if local and profile.get('available') else 'disabled'])
        self.sync_voice_source_controls(voiced)
        self.voice_gain_slider.configure(state='normal' if voiced else 'disabled')
        self.subtitle_position_control.state(['!disabled' if subtitled else 'disabled'])
        timing = self.top_timing_var.get()
        intro_used = (top_count > 0 and (timing == '开头显示' or
                      (timing == '自动' and (counts['字幕+BGM'] + counts['配音+BGM'] > 0)) or
                      (timing == '配音结束后显示' and counts['字幕+BGM'] > 0)))
        self.intro_seconds_control.state(['!disabled' if intro_used else 'disabled'])

    def _update_video_policy_preview(self):
        try:
            count = int(self.var('count').get())
            ratios = {mode:var.get() for mode,var in self.mode_percent_vars.items()}
            counts = mode_counts({'count':count, 'video_mode_percentages':ratios})
            for mode, label in self.mode_estimates.items():
                label.set(f'预计{counts[mode]}条')
            self.mode_total_var.set(f'合计100% / {sum(counts.values())}条')
            top_policy = self.top_use_var.get()
            top_count = (0 if top_policy == 'none' else count if top_policy == 'all' else
                         apportion(count, [self.top_percent_var.get(),
                                           100-float(self.top_percent_var.get())], ['有','无'])[0])
            self.top_estimate_var.set(f'预计{top_count}条')
            self._set_production_controls(counts, top_count)
        except (ValueError, TypeError, KeyError) as exc:
            self.mode_total_var.set(str(exc) if str(exc) else '比例待填')
            for label in self.mode_estimates.values(): label.set('预计—')
            self.top_estimate_var.set('预计—')

    def collect_config(self):
        cfg = copy.deepcopy(self.cfg)
        # Only newly planned GUI jobs opt into the new system. Loaded manifests
        # carry their own version and remain byte-for-byte frozen.
        cfg['subtitle_system_version']='smart_animation_v1'
        cfg.pop('video_mode_schedule', None)
        ints = ('count', 'opening_c_min_per_batch', 'c_min', 'c_max', 'max_clips', 'fps', 'width', 'height', 'crf', 'concurrency', 'top_text_fixed_index', 'top_text_font_size', 'top_text_top', 'top_text_stroke', 'processing_drop_every', 'processing_fps', 'processing_mirror_random_min', 'processing_mirror_random_max', 'processing_drop_random_min', 'processing_drop_random_max', 'processing_color_random_min', 'processing_color_random_max', 'processing_zoom_random_min', 'processing_zoom_random_max', 'subtitle_font_size', 'subtitle_max_chars_per_line')
        floats = ('min_seconds', 'max_seconds', 'preferred_min', 'preferred_max', 'bgm_gain', 'fade_out', 'fade_in', 'plan_seconds_per_video', 'top_text_max_width', 'processing_brightness', 'processing_contrast', 'processing_saturation', 'processing_speed', 'processing_mirror_probability', 'processing_drop_probability', 'processing_color_probability', 'processing_zoom_probability', 'processing_zoom_min', 'processing_zoom_max', 'selection_v2_interval_limit', 'voice_trim_db', 'voice_gain_db', 'bgm_trim_db', 'subtitle_custom_y', 'subtitle_safe_margin_top', 'subtitle_safe_margin_bottom', 'subtitle_safe_margin_left', 'subtitle_safe_margin_right')
        try:
            for key, var in self.vars.items():
                value = var.get().strip().strip('"')
                cfg[key] = (int(value) if key in ints or type(TIMELINE_DEFAULTS.get(key)) is int
                            else float(value) if key in floats or type(TIMELINE_DEFAULTS.get(key)) is float else value)
        except ValueError as exc:
            raise AssemblyError('数字参数格式不正确。请使用普通数字和英文小数点。') from exc
        cfg['dedup_mode'] = 'strict' if self.dedup_var.get() == '更严格' else 'balanced'
        cfg['music_mode'] = 'natural' if self.music_mode_var.get().startswith('自然') else 'fade'
        cfg['selected_music'] = (self.music_choices.get(self.music_selected_var.get(), '') if self.music
                                 else self.cfg.get('selected_music',''))
        cfg['group_folders'] = {g: v.get().strip() for g, v in self.group_vars.items()}
        cfg['source_audio'] = 'discard'
        # The preset only fills ratios; it is never a planning input.
        cfg.pop('script_mode', None)
        for key in ('post_voice_policy', 'post_voice_plain_percent',
                    'post_voice_top_percent', 'post_voice_top_text_ratio',
                    'post_voice_clean_ratio', 'post_voice_random_mode'):
            cfg.pop(key, None)
        try:
            cfg['video_mode_percentages'] = {mode:float(var.get())
                                             for mode,var in self.mode_percent_vars.items()}
            cfg['top_text_use_percent'] = float(self.top_percent_var.get())
            cfg['intro_top_text_duration'] = float(self.intro_seconds_var.get())
        except ValueError as exc:
            raise AssemblyError('视频模式或顶部文案比例需填写普通数字。') from exc
        cfg['video_policy_version'] = VIDEO_POLICY_REVISION
        cfg['bgm_rule_version'] = BGM_DIRECTORY_RULE
        cfg['voice_cache_enabled'] = bool(self.voice_cache_var.get())
        cfg['voice_cache_revision'] = 2
        cfg.pop('voice_force_regenerate',None)
        cfg['top_text_use_policy'] = self.top_use_var.get()
        cfg['top_text_timing'] = {'自动':'auto','开头显示':'start',
                                  '配音结束后显示':'after_voice','全程显示':'full'}[self.top_timing_var.get()]
        cfg['script_input_mode'] = self.script_input_mode.get()
        cfg['script_source'] = self.script_source.get()
        cfg['script_library_strategy'] = self.script_library_strategy.get()
        cfg['allow_script_repeat'] = bool(self.allow_script_repeat.get())
        cfg['voice_strategy'] = self.voice_strategy_var.get()
        cfg['voice_selected_ids'] = [voice_id for voice_id, var in self.voice_checks.items() if var.get()]
        cfg['subtitle_position'] = self.subtitle_position_var.get()
        if cfg.get('subtitle_style'):
            style=copy.deepcopy(cfg['subtitle_style'])
            for setting,field in [('subtitle_font_size','font_size'),('subtitle_safe_margin_bottom','safe_bottom')]:
                if cfg.get(setting)!=self.cfg.get(setting):style[field]=cfg[setting]
            if cfg['subtitle_position']!=self.cfg.get('subtitle_position') or cfg.get('subtitle_custom_y')!=self.cfg.get('subtitle_custom_y'):
                from subtitle_styles import from_config
                plain=dict(cfg);plain.pop('subtitle_style',None);style['y']=from_config(plain)['y']
            cfg['subtitle_style']=style
        cfg['audio_mix_profile'] = self.audio_mix_var.get()
        cfg['top_text_enabled'] = (cfg['top_text_use_policy'] != 'none' and
                                   cfg['top_text_use_percent'] > 0 if cfg['top_text_use_policy'] == 'ratio'
                                   else cfg['top_text_use_policy'] == 'all')
        cfg['top_text_mode'] = 'sequence' if self.top_text_mode_var.get() == '顺序' else 'random'
        for key, var in self.processing_bools.items():
            cfg[key] = bool(var.get())
        for prefix, var in self.processing_scope_vars.items():
            cfg[prefix + '_scope'] = self._processing_scope_reverse.get(var.get(), 'all')
        for key, (var, mapping) in self.timing_enum_vars.items():
            cfg[key] = mapping[var.get()]
        cfg=self.profile_config(cfg)
        cfg=self.collect_output_style(cfg)
        validate_config(cfg)
        if not 0 <= cfg['crf'] <= 40 or not 1 <= cfg['concurrency'] <= 16:
            raise AssemblyError('CRF请使用0～40；渲染并发1～16。')
        return cfg

    def set_preset(self, event=None):
        lookup = {'常规 15～30秒': (15, 30, 20, 28), '中长 30～40秒': (30, 40, 32, 38), '长版 40～45秒': (40, 45, 41, 44)}
        values = lookup.get(self.preset.get())
        if values:
            for key, val in zip(('min_seconds', 'max_seconds', 'preferred_min', 'preferred_max'), values):
                self.var(key).set(str(val))

    def set_subtitle_position(self, event=None):
        if self.subtitle_position_var.get() != '自定义':
            return
        y = simpledialog.askfloat('自定义字幕位置', '填写画面高度比例（0.1～0.9）：',
                                  initialvalue=float(self.cfg.get('subtitle_custom_y', 0.72)),
                                  minvalue=0.1, maxvalue=0.9, parent=self)
        if y is None:
            self.subtitle_position_var.set('下中')
        else:
            self.var('subtitle_custom_y').set(str(y))

    def guard(self, fn):
        if fn is None:
            return
        try:
            fn()
        except Exception as exc:
            import runtime_diagnostics as diag
            diag.error(exc,component="ui")
            self.log(str(exc))
            messagebox.showerror('请检查', str(exc), parent=self)

    def log(self, text):
        self.log_box.insert('end', f'{now()[11:19]}  {text}\n')
        if self.log_box.yview()[1]>.98:self.log_box.see('end')
        if int(self.log_box.index('end-1c').split('.')[0])>1600:self.log_box.delete('1.0','201.0')
        self.status_var.set(str(text).splitlines()[0][:180])

    def require_idle(self):
        if getattr(self,'history_cleaning',False):
            raise AssemblyError('正在清理历史组合，请稍后再开始或继续生成。')
        if self.busy or self.lanes.running('planning'):
            raise AssemblyError('当前任务正在运行；此操作请等待任务结束。组合方案和视频生成可在各自区域独立操作。')

    def require_planning_idle(self):
        if getattr(self,'history_cleaning',False):raise AssemblyError('正在清理历史组合，请稍后再准备方案。')
        if self.lanes.running('planning'):raise AssemblyError('组合方案正在准备，请等待或停止本次组合。')
        if self.busy and not getattr(self,'render_running',False):raise AssemblyError('其他维护操作正在执行，请稍后再准备方案。')

    def require_render_idle(self):
        if getattr(self,'history_cleaning',False):raise AssemblyError('正在清理历史组合，请稍后再生成视频。')
        if self.busy:raise AssemblyError('视频生成正在进行；可以在组合方案区准备下一批。')

    def launch_planning(self,worker,done):
        self.require_planning_idle()
        self.planning_pause.clear()
        self.plan_progress.start(12);self.progress.start(12)
        progress_visible(self.plan_progress,True)
        progress_visible(self.progress,True,getattr(self,'status_label',None))
        self.plan_pause_btn.configure(state='normal')
        self.plan_btn.configure(state='disabled');self.scan_btn.configure(state='disabled')
        self.plan_status_var.set('正在准备组合方案；视频生成独立继续')
        self.lanes.submit('planning',worker,done,context={'batch_id':(self.batch or {}).get('id','')})

    def launch(self, worker, done, *, allow_planning=False):
        if allow_planning:self.require_render_idle()
        else:self.require_idle()
        self.busy = True
        self.pause.clear(); self.abort.clear()
        self.progress.start(12)
        progress_visible(self.progress,True,getattr(self,'status_label',None))
        self.pause_btn.configure(state='normal')
        self.lanes.submit('production',worker,done,context={'batch_id':(self.batch or {}).get('id','')})

    def launch_voice(self,worker,done,on_error=None):
        return self.lanes.submit('voice',worker,done,on_error=on_error)

    def _pump(self):
        import time
        deadline=time.monotonic()+(.004 if getattr(self,'presentation_only',False) else .018)
        try:
            for _ in range(80):
                if time.monotonic()>deadline:break
                ev=self.events.get_nowait()
                if ev[0]=='log':self.log(ev[1])
                elif ev[0]=='item':self.update_item(ev[1])
                elif ev[0]=='script_tools_progress':
                    if ev[1].winfo_exists():ev[1].status.set(ev[2])
                elif ev[0] in ('lane_done','lane_error'):
                    _,lane,token,callback,value,on_error=ev
                    self.lanes.complete(lane,token)
                    if lane=='production':
                        self.busy=False
                        self.render_running=False
                        if not self.lanes.running('planning'):self.progress.stop()
                        self.pause_btn.configure(state='disabled');self.abort_btn.configure(state='disabled')
                        self.render_progress.stop();self.render_btn.configure(state='normal')
                        progress_visible(self.render_progress,False)
                        if not self.lanes.running('planning'):progress_visible(self.progress,False)
                        self._update_video_policy_preview()
                    elif lane=='planning':
                        self.plan_progress.stop()
                        progress_visible(self.plan_progress,False)
                        if not self.busy:self.progress.stop()
                        if not self.busy:progress_visible(self.progress,False)
                        self.plan_pause_btn.configure(state='disabled')
                        self.plan_btn.configure(state='normal');self.scan_btn.configure(state='normal')
                        self.plan_status_var.set('组合准备已停止' if self.planning_pause.is_set() else '组合准备结束')
                    if ev[0]=='lane_error':
                        self.log(str(value))
                        if lane=='production':self.render_status_var.set('视频任务异常结束：'+str(value))
                        if lane=='production':self.video_queue.halt()
                        if callback:self.guard(lambda:callback(value))
                        elif lane in ('production','planning'):messagebox.showerror('任务未完成',str(value),parent=self)
                    elif callback:self.guard(lambda:callback(value))
                    if self.pending_close and not self.lanes.active:
                        self._finish_close();return
        except queue.Empty:pass
        self._pump_after=self.after(80,self._pump)

    def worker_log(self,msg):
        import runtime_diagnostics as diag
        diag.emit('message',msg,component='pipeline')
        if self.events.qsize()<3000:self.events.put(('log',msg))

    def open_subtitle_editor(self):
        if getattr(self,'_qt_parent',None):
            self.subtitle_editor=self._qt_parent.editors.open('subtitles');return
        from subtitle_editor_ui import SubtitleEditor
        if getattr(self,'subtitle_editor',None) and self.subtitle_editor.winfo_exists():
            self.subtitle_editor.lift();return
        self.subtitle_editor=SubtitleEditor(self)

    def voice_cache_info(self):
        from voice_cache import stats
        def done(info):messagebox.showinfo('配音缓存',f"已提交条目：{info['entries']}\n占用：{info['bytes']/1024**2:.1f} MiB\n{info['root']}",parent=self)
        self.lanes.submit('cache-info',stats,done)

    def open_voice_cache(self):
        from voice_cache import ROOT
        ROOT.mkdir(parents=True,exist_ok=True);open_local(ROOT)

    def clean_voice_cache(self):
        from voice_cache import unused_candidates,remove_candidates
        def confirmed(candidates):
            if not candidates:
                self.log('没有超过7天且未被任务引用的缓存。');return
            if messagebox.askyesno('确认清理未使用缓存',f'将清理 {len(candidates)} 个超过7天未使用且没有任务引用的条目。正在使用的条目会跳过。继续？',parent=self):
                self.lanes.submit('cache-clean',lambda:remove_candidates(candidates,self.data_dir),lambda count:self.log(f'用户确认清理正式配音缓存：{count} 个条目。'))
        self.lanes.submit('cache-scan',lambda:unused_candidates(self.data_dir),confirmed)

    def save_settings(self):
        self.cfg = self.collect_config()
        self.persist_config(self.cfg)
        (self.data_dir / 'script_draft.txt').write_text(self.script_input.get('1.0','end-1c'),encoding='utf-8')
        self.log('设置已保存。原项目规则和索引未修改。')

    def refresh_voice_profiles(self):
        old = {key: var.get() for key, var in getattr(self, 'voice_checks', {}).items()}
        for child in self.voice_frame.winfo_children(): child.destroy()
        self.voice_profiles = discover_profiles()
        self.voice_checks = {}
        if not self.voice_profiles:
            ttk.Label(self.voice_frame,text='未找到音色Profile').pack(side='left')
        for profile in self.voice_profiles:
            voice_id = profile['voice_id']
            selected = old.get(voice_id, voice_id in self.cfg.get('voice_selected_ids', []))
            var = tk.BooleanVar(value=selected and profile.get('available', False))
            self.voice_checks[voice_id] = var
            label = profile.get('display_name', voice_id)
            if not profile.get('available'):
                label += '（不可用）'
            ttk.Checkbutton(self.voice_frame,text=label,variable=var,
                            state='normal' if profile.get('available') else 'disabled').pack(side='left',padx=3)
        if hasattr(self, 'mode_percent_vars'):
            self._update_video_policy_preview()

    def open_voice_selector(self):
        if getattr(self,'_qt_parent',None):
            from auxiliary_dialogs_qt import voice_selector
            self.voice_selection_window=voice_selector(self);return
        win=tk.Toplevel(self);win.title('批量选择音色');win.geometry('1100x560')
        profile_id=self.cfg['editing_profile_id']
        ttk.Label(win,text='只显示既有可用音色；全选支持当前筛选结果。此处只选用音色，不改变审核状态。').pack(fill='x',padx=10,pady=8)
        tree=self._tree(win,[('id','音色ID',190),('name','音色名称',420),('status','状态',120)],10)
        for profile in self.voice_profiles:
            if profile.get('available'):
                tree.insert('','end',iid=profile['voice_id'],values=(profile['voice_id'],profile.get('display_name',profile['voice_id']),'可用'))
        tree.selection_set([key for key,var in self.voice_checks.items() if var.get()])
        def apply():
            if self.cfg['editing_profile_id']!=profile_id:raise AssemblyError('剪辑方案已切换，请重新打开音色选择窗口。')
            selected=set(tree.selection())
            available={p['voice_id'] for p in self.voice_profiles if p.get('available')}
            if not selected.issubset(available):raise AssemblyError('音色可用状态已变化，请刷新后重选。')
            for key,var in self.voice_checks.items():var.set(key in selected)
            self._update_video_policy_preview()
            win.destroy()
        self._button(win,'应用所选音色',apply).pack(pady=8)
        self.voice_selection_window=win

    def open_voice_manager(self):
        if getattr(self,'_qt_parent',None):
            self.voice_manager=self._qt_parent.editors.open('voices');return
        if getattr(self, 'voice_manager', None) and self.voice_manager.winfo_exists():
            self.voice_manager.lift()
            self.voice_manager.focus_set()
            return
        from voice_manager_ui import VoiceManagerWindow
        self.voice_manager = VoiceManagerWindow(self, self.refresh_voice_profiles)

    def open_script_library(self, profile_id=None):
        profile_id = profile_id or self.cfg['editing_profile_id']
        if getattr(self,'_qt_parent',None):
            self.script_library_window=self._qt_parent.editors.open('scripts',profile_id=profile_id);return
        if getattr(self, 'script_library_window', None) and self.script_library_window.winfo_exists():
            self.script_library_window.show_profile(profile_id,self.cfg.get('product_spec_id','default') if profile_id==self.cfg['editing_profile_id'] else 'default')
            self.script_library_window.lift()
            self.script_library_window.focus_set()
            return
        from script_library_ui import ScriptLibraryWindow
        self.script_library_window = ScriptLibraryWindow(self, self.script_library, profile_id=profile_id)

    def parse_script_input(self, auto_count=True):
        scripts = parse_scripts(self.script_input.get('1.0','end-1c'),
                                self.script_input_mode.get() == '批量', '配音+字幕+BGM',
                                custom=self.custom_readings)
        self.script_count_var.set(f'已识别{len(scripts)}篇')
        if scripts and auto_count:
            self.var('count').set(str(len(scripts)))
        return scripts

    def edit_speech_text(self):
        scripts = self.parse_script_input(False)
        if not scripts:
            raise AssemblyError('请先输入文案。')
        script_id = (scripts[0]['script_id'] if len(scripts) == 1 else
                     simpledialog.askstring('选择文案',f'输入要编辑的编号（S001～S{len(scripts):03d}）：',parent=self))
        script = next((s for s in scripts if s['script_id'] == script_id),None)
        if script is None:
            raise AssemblyError('文案编号不存在。')
        key = hashlib.sha256(script['raw_text'].encode('utf-8')).hexdigest()
        current = self.script_overrides.get(key,script['speech_text'])
        edited = simpledialog.askstring(f'{script_id}朗读文本',
                    '只修改朗读版本；原文和显示字幕保持不变。',initialvalue=current,parent=self)
        if edited is not None:
            self.script_overrides[key] = edited.strip()
            atomic_json(self.data_dir / 'script_overrides.json', self.script_overrides)
            self.log(f'{script_id}已设置人工朗读文本覆盖，原文未修改。')

    def edit_custom_reading(self):
        source = simpledialog.askstring('自定义读法', '输入原文中的词或缩写：', parent=self)
        if source is None: return
        source = source.strip()
        if not source: raise AssemblyError('原词不能为空。')
        spoken = simpledialog.askstring('自定义读法', f'{source} 应读成：',
                                         initialvalue=self.custom_readings.get(source, ''), parent=self)
        if spoken is None: return
        spoken = spoken.strip()
        if not spoken: raise AssemblyError('读法不能为空。')
        self.custom_readings[source] = spoken
        atomic_json(self.data_dir / 'normalizer_custom.json', self.custom_readings)
        self.log(f'已保存「{source}」的自定义读法；新方案会使用。')

    def scan(self):
        self.require_planning_idle()
        cfg = self.collect_config()
        self.cfg = cfg
        self.persist_config(cfg)
        key=cfg['editing_profile_id']
        self.launch_planning(lambda: scan_library(cfg, self.store, self.worker_log, stop=self.planning_pause), lambda result:self.scanned_for_profile(result,key))

    def scanned(self, result):
        self.clips, self.music, warnings = result
        self.scan_warnings=list(warnings)
        self.refresh_media()
        self.refresh_music()
        for warning in warnings:
            self.log(warning)
        self.log(f'已扫描 {len(self.clips)} 条视频 / {len(self.music)} 首音乐。原音轨无论是否存在，输出均去除。')

    def refresh_media(self):
        self.media_tree.delete(*self.media_tree.get_children())
        summaries = []
        for g in ([] if editing_profiles.grouped(self.cfg) else groups_for_config(self.cfg)):
            entries = [c for c in self.clips if c['group'] == g]
            durations = [c['duration'] for c in entries if c['enabled']]
            if durations:
                summaries.append(f"{self.cfg['group_folders'][g]}：{len(durations)}条，均长{sum(durations)/len(durations):.2f}秒，范围{min(durations):.2f}～{max(durations):.2f}秒")
        for i, c in enumerate(self.clips):
            mutex_display=c.get('mutex_group','旧目录')
            if self.cfg.get('material_format')=='sk_diverse':
                mutex_display=c.get('sk_placement_path') or ((c['sk_scene_path']+'（相似组，软限制）') if c.get('sk_scene_path') else '独立素材')
            self.media_tree.insert('', 'end', iid=str(i), values=(c.get('category',c['group']), '是' if c.get('can_open') else '否',
                                  mutex_display,c.get('member_number') if c.get('member_number') is not None else '—',c.get('relative_path',c['display']), f"{c['duration']:.3f}", f"{c.get('display_width',c['width'])}×{c.get('display_height',c['height'])}",
                                  '有（将去除）' if c['has_audio'] else '无', '是' if c['enabled'] else '否',
                                  '已确认' if c['reviewed'] else '待确认', c.get('project_link', {}).get('clip_id') or '未关联'))
        opens=[c for c in self.clips if c.get('enabled') and c.get('reviewed') and c.get('can_open')]
        sources=f"已启用且复核的首镜候选{len(opens)}条（A {sum(c['category']=='A' for c in opens)}，C/开头 {sum(c['category']=='C' for c in opens)}）。" if not editing_profiles.grouped(self.cfg) and groups_for_config(self.cfg)==STAGE_GROUPS else ''
        if self.cfg.get('material_format')=='sk_diverse':
            from sk_materials import summary as sk_summary
            stats=sk_summary(self.clips)
            sources='SK本库：'+ '；'.join(f'{r["type"]} {r["files"]}文件/{r["similar_groups"]}相似组/{r["placement_groups"]}数字组/首镜{r["opening_candidates"]}' for r in stats['types'])
        elif editing_profiles.grouped(self.cfg):
            sources=f"G组镜头{len(self.clips)}条；组合{sum(c['asset_kind']=='combo' for c in self.clips)}，独立{sum(c['asset_kind']=='independent' for c in self.clips)}。A/B/C/D仅为组合编号；请在第1页确认覆盖关系。"
        self.media_summary.set('；  '.join(summaries) + '\n' + sources + '\n未关联仅表示无法凭当前文件内容/路径对应原索引；程序不会用文件名猜商品身份。')
        enabled = [c for c in self.clips if c['enabled']]
        self.review_var.set(bool(enabled) and all(c['reviewed'] for c in enabled))

    def confirm_reviewed(self):
        if self.lanes.running('planning'):
            return
        if not self.clips:
            self.review_var.set(False)
            raise AssemblyError('请先扫描文件，再确认当前文件版本。')
        reviewed = self.review_var.get()
        for c in self.clips:
            if c['enabled']:
                self.store.set_flag(c['hash'], reviewed=reviewed)
                c['reviewed'] = reviewed
        self.refresh_media()

    def approve_selected(self):
        hashes = {self.clips[int(iid)]['hash'] for iid in self.media_tree.selection()}
        for digest in hashes:
            self.store.set_flag(digest, reviewed=True)
        for c in self.clips:
            if c['hash'] in hashes:
                c['reviewed'] = True
        self.refresh_media()

    def toggle_media(self, enabled):
        hashes = {self.clips[int(i)]['hash'] for i in self.media_tree.selection()}
        for digest in hashes:
            self.store.set_flag(digest, enabled=enabled)
        for c in self.clips:
            if c['hash'] in hashes:
                c['enabled'] = enabled
        self.refresh_media()

    def play_media(self):
        selected = self.media_tree.selection()
        if selected:
            open_local(self.clips[int(selected[0])]['path'])

    def refresh_music(self):
        previous = self.music_selected_var.get()
        self.music_choices = {'自动选择': ''}
        self.music_tree.delete(*self.music_tree.get_children())
        seen = set()
        for i, m in enumerate(self.music):
            identity = (m['hash'], m.get('bgm_type'))
            if identity in seen:
                continue
            seen.add(identity)
            try:
                display = Path(m['path']).relative_to(Path(self.cfg['music_root'])).as_posix()
            except ValueError:
                display = m['name']
            name = display + '  [' + m['hash'][:6] + ']'
            self.music_choices[name] = m['hash']
            count = sum(r['music_hash'] == m['hash'] and r.get('confirmed') for r in self.ranges)
            self.music_tree.insert('', 'end', iid=str(i), values=(display, f"{m['duration']:.3f}", count))
        self.music_picker['values'] = tuple(self.music_choices)
        if previous not in self.music_choices:
            selected = next((k for k, v in self.music_choices.items() if v == self.cfg.get('selected_music')), '自动选择')
            self.music_selected_var.set(selected)
        self.range_tree.delete(*self.range_tree.get_children())
        mapping = {m['hash']: m for m in self.music}
        for r in self.ranges:
            m = mapping.get(r['music_hash'])
            name = m['name'] if m else '[音乐不存在或内容已改变]'
            self.range_tree.insert('', 'end', iid=r['id'], values=(name, f"{r['start']:.3f}", f"{r['end']:.3f}", f"{r['end']-r['start']:.3f}", r.get('note', '')))

    def selected_track(self):
        selected = self.music_tree.selection()
        if not selected:
            raise AssemblyError('请先在音乐列表选择一首音乐。')
        return self.music[int(selected[0])]

    def play_music(self):
        open_local(self.selected_track()['path'])

    def add_range(self):
        import math
        import uuid
        m = self.selected_track()
        try:
            start, end = float(self.range_start.get()), float(self.range_end.get())
        except ValueError as exc:
            raise AssemblyError('开始、结束请填写秒数，例如0和23.5。') from exc
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= m['duration'] + 1e-6:
            raise AssemblyError('区间须在音乐文件时长之内，且开始早于结束。')
        if any(r['music_hash'] == m['hash'] and abs(r['start']-start) < 1e-6 and abs(r['end']-end) < 1e-6 for r in self.ranges):
            raise AssemblyError('此区间已经存在。')
        self.ranges.append({'id': uuid.uuid4().hex, 'music_hash': m['hash'], 'start': start, 'end': end,
                            'confirmed': True, 'note': self.range_note.get().strip(), 'confirmed_at': now()})
        atomic_json(self.music_path, self.ranges)
        self.refresh_music()
        self.log('已保存自然区间确认，不会修改音乐原文件。')

    def add_full_ranges(self):
        import uuid
        selected = self.music_tree.selection()
        if not selected:
            raise AssemblyError('请先选中音乐，可按Ctrl多选。')
        if not messagebox.askyesno('确认整首音乐', '确认所选音乐从0秒开始、原文件结束处都自然？\n这不是程序自动判断。', parent=self):
            return
        for iid in selected:
            m = self.music[int(iid)]
            if not any(r['music_hash'] == m['hash'] and r['start'] == 0 and abs(r['end']-m['duration']) < 1e-6 for r in self.ranges):
                self.ranges.append({'id': uuid.uuid4().hex, 'music_hash': m['hash'], 'start': 0.0, 'end': m['duration'],
                                    'confirmed': True, 'note': '用户确认整首起止', 'confirmed_at': now()})
        atomic_json(self.music_path, self.ranges)
        self.refresh_music()

    def delete_ranges(self):
        selected = set(self.range_tree.selection())
        self.ranges = [r for r in self.ranges if r['id'] not in selected]
        atomic_json(self.music_path, self.ranges)
        self.refresh_music()

    def preview_music_range(self):
        m = self.selected_track()
        try:
            start, end = float(self.range_start.get()), float(self.range_end.get())
        except ValueError as exc:
            raise AssemblyError('请填写区间起止秒数。') from exc
        if not 0 <= start < end <= m['duration']:
            raise AssemblyError('音乐区间超界。')
        cfg = self.collect_config()
        ffmpeg = resolve_tool('ffmpeg', cfg['ffmpeg'])
        target = self.data_dir / 'audio_preview.wav'
        def worker():
            run_capture([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-ss', str(start), '-i', m['path'],
                         '-t', str(end-start), '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le', str(target)], 90)
            return str(target)
        self.launch(worker, lambda p: open_local(p))

    def plan(self):
        self.require_planning_idle()
        cfg = self.collect_config()
        if cfg.get('material_format')=='sk_diverse':
            from sk_materials import preflight
            preflight(self.clips,cfg)
        from ball_scene_rotation import capture_config as capture_scene_config
        cfg=capture_scene_config(cfg,self.data_dir)
        if cfg.get('clip_sfx_enabled'):
            from clip_sfx import capture_config
            cfg=capture_config(cfg,self.data_dir)
        if cfg.get('voice_source','indextts25')!='external_files' and self.lanes.running('voice'):
            raise AssemblyError('请等待本次音色后台操作结束后建立新批次，以便冻结一致的音色快照。')
        cfg['script_library_path']=str(self.script_library.path)
        output_root = Path(cfg['output_root'])
        output_root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(output_root).free < 2 * 1024**3:
            raise AssemblyError('磁盘剩余空间不足2 GiB，请先清理程序临时文件。第7页有“清理临时文件”入口。')
        if not cfg.get('seed'):
            cfg['seed'] = uuid.uuid4().hex
        natural_frames = None
        if cfg['music_mode'] == 'natural' and self.music and all('hash' in track for track in self.music):
            natural_frames = {
                'all':[track['target_frames'] for track in music_candidates(self.music, self.ranges, cfg)],
                'instrumental':[track['target_frames'] for track in music_candidates(
                    self.music, self.ranges, cfg, narration_enabled=True)]}
        schedule = build_schedule(cfg, natural_frames)
        cfg['video_mode_schedule'] = schedule
        manual_text = self.script_input.get('1.0','end-1c')
        text_count = sum(entry['video_mode'] != '纯画面+BGM' for entry in schedule)
        if (any(entry['video_mode'].startswith('配音') for entry in schedule) and self.music and
                all('hash' in track for track in self.music) and not music_candidates(
                    self.music, self.ranges, cfg, narration_enabled=True)):
            if cfg.get('batch_failure_policy')=='skip_recompose_v1':self.log('纯音乐预检未通过，逐条组合结束后汇总。')
            else:raise AssemblyError('NO_SUITABLE_INSTRUMENTAL_BGM：当前视频包含配音，但纯音乐库中没有符合条件的BGM。')
        if text_count and cfg.get('script_source') == '文案库':
            source_rows = self.script_library.select_for_profile(text_count, cfg)
        elif text_count:
            source_rows = bind_scripts(parse_scripts(manual_text,
                self.script_input_mode.get() == '批量', '配音+字幕+BGM', custom=self.custom_readings),
                text_count, cfg['allow_script_repeat'])
        else:
            source_rows = []
        if cfg.get('voice_source')=='external_files' and any(e['video_mode'] in ('配音+BGM','配音+字幕+BGM') for e in schedule):
            if cfg.get('script_source')!='文案库':raise AssemblyError('外部配音请先将文案存入文案库并绑定音频。')
        scripts = [None] * cfg['count']
        row_iter = iter(source_rows)
        for index, entry in enumerate(schedule):
            if entry['video_mode'] == '纯画面+BGM':
                continue
            row = next(row_iter)
            script = parse_scripts(row['raw_text'], False, entry['video_mode'],
                                   custom=self.custom_readings)[0]
            script['script_id'] = (row['script_id'] if cfg.get('script_source') == '文案库'
                                   else f'S{index+1:03d}')
            if cfg.get('script_source') == '文案库':
                script['script_library_id'] = row['script_id']
                script['script_profile_id'] = row['profile_id']
                script['script_spec_id']=row.get('spec_id','default')
            else:
                script['script_profile_id']=cfg['editing_profile_id']
                script['script_spec_id']=cfg.get('product_spec_id','default')
            key = hashlib.sha256(script['raw_text'].encode('utf-8')).hexdigest()
            script['speech_text_override'] = self.script_overrides.get(key, '')
            script['diagnostic_number']=index+1
            scripts[index] = script
        if cfg.get('voice_source')=='external_files':
            issues=self.script_library.external.preflight([s['script_library_id'] for s in scripts if s and s['narration_enabled']],cfg.get('external_voice_strategy','均衡轮换'),cfg)
            if issues:
                if cfg.get('batch_failure_policy')=='skip_recompose_v1':
                    for issue in issues:self.log('外部配音预检提示，逐条处理后汇总：'+issue)
                else:raise AssemblyError('外部配音预检未通过：\n'+'\n'.join(issues))
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / 'script_draft.txt').write_text(manual_text, encoding='utf-8')
        if not self.clips:
            raise AssemblyError('先扫描素材和音乐。')
        if not self.rights_var.get():
            raise AssemblyError('请确认所选音乐可用于本次用途。程序不会替你批准音乐权利。')
        if cfg.get('processing_enabled'):
            notes = processing_warnings(cfg)
            if not messagebox.askyesno('本批启用导出处理', '\n\n'.join(notes) + '\n\n继续生成组合方案？', parent=self):
                return
        # Do not plan against paths/config different from the last scan.
        if any(cfg[k] != self.cfg[k] for k in ('material_root', 'music_root', 'group_folders')):
            raise AssemblyError('素材/音乐路径或分组已改变，请重新扫描。')
        # New plans retain every previous frozen batch and its reservations.
        cfg['voice_force_regenerate'] = bool(self.voice_force_var.get())
        self.voice_force_var.set(False)
        cfg['voice_job_temp_dir'] = str((self.data_dir/'job_temp'/uuid.uuid4().hex).resolve())
        Path(cfg['voice_job_temp_dir']).mkdir(parents=True, exist_ok=False)
        (Path(cfg['voice_job_temp_dir'])/'.assembler_job_temp').write_text('', encoding='utf-8')
        self.cfg = {key:value for key,value in cfg.items()
                    if key not in ('video_mode_schedule', 'voice_job_temp_dir', 'voice_force_regenerate')}
        self.persist_config(self.cfg)
        from background_tasks import snapshot_profiles,write_snapshot
        profiles=(snapshot_profiles(self.voice_profiles,cfg['voice_job_temp_dir']) if cfg.get('voice_source','indextts25')!='external_files' and any(s and s['narration_enabled'] for s in scripts) else [])
        clips, music, ranges = copy.deepcopy(self.clips), copy.deepcopy(self.music), copy.deepcopy(self.ranges)
        if cfg.get('editorial_enabled'):
            from editorial import capture as capture_editorial
            clips,cfg=capture_editorial(clips,cfg,self.store.root)
        if cfg.get('clip_sfx_enabled'):
            from clip_sfx import capture_config as capture_sfx
            cfg=capture_sfx(cfg,self.store.root)
        if editing_profiles.ball_profile(cfg) and editing_profiles.grouped(cfg):
            from ball_scene_rotation import capture_config as capture_scenes
            cfg=capture_scenes(cfg,self.store.root)
        if editing_profiles.grouped(cfg):
            from grouped_materials import Coverage
            if cfg.get('material_format')=='sk_diverse':
                from sk_materials import Review
                coverage=Review(self.store,cfg['material_root'])
            else:coverage=Coverage(self.store,cfg['material_root'])
            coverage.annotate(clips)
            cfg['grouped_coverage_snapshot']=coverage.snapshot(clips)
        cfg['task_snapshot']=write_snapshot(cfg['voice_job_temp_dir'],cfg,scripts,profiles)
        def worker():
            try:
                packages = None;provider=None
                text_slots = [(index, script) for index, script in enumerate(scripts) if script]
                if text_slots:
                    ffmpeg = resolve_tool('ffmpeg', cfg['ffmpeg'])
                    if cfg.get('voice_source','indextts25')!='external_files' and any(script['narration_enabled'] for _, script in text_slots):
                        provider = self.planning_provider or IndexTTS25Provider(log=self.worker_log)
                        self.planning_provider=provider
                        provider.cancel_event=self.planning_pause
                        provider.resource_timeout=cfg.get("resource_wait_timeout_seconds",300)
                        provider.request_timeout=cfg.get("tts_timeout_seconds",900)
                    prepared = prepare_script_packages([script for _, script in text_slots], cfg, ffmpeg,
                                                       profiles, provider, self.worker_log)
                    packages = [None] * cfg['count']
                    for (index, _), package in zip(text_slots, prepared):
                        packages[index] = package
                batch = plan_batch(clips, music, ranges, cfg, self.store, self.worker_log, self.planning_pause, packages)
                batch['preparation_attempt_errors']=[dict(number=p.get('diagnostic_number'),script_id=p.get('script_id'),attempts=p['preparation_attempt_errors']) for p in packages or [] if p and p.get('preparation_attempt_errors')]
                import runtime_diagnostics as diag
                diag.emit("batch_link","规划配音记录关联正式批次",batch_id=batch["id"],source_batch_id=Path(cfg["voice_job_temp_dir"]).name,items=[{"job_id":i["id"],"number":i["number"],"script_id":i.get("script_id")} for i in batch["items"]])
                path = save_batch(batch, self.store)
                self.script_library.external.finalize(batch,cfg['voice_job_temp_dir'])
                self.script_library.reserve_batch(batch)
                return batch, path
            except Exception:
                # A saved Manifest owns its media until render/retry. No Manifest
                # means this newly created job is an orphan and may be removed.
                owned = False
                for manifest in (self.data_dir/'batches').glob('*/batch_manifest.json'):
                    try:
                        existing = read_json(manifest, {})
                        if existing.get('config', {}).get('voice_job_temp_dir') == cfg['voice_job_temp_dir']:
                            owned = True; break
                    except (OSError, ValueError):
                        continue
                if not owned:
                    self.script_library.external.abandon_preparation(cfg['voice_job_temp_dir'])
                    cleanup_orphan_job(Path(cfg['voice_job_temp_dir']), self.data_dir)
                raise
        self.launch_planning(worker, self.planned)

    def planned(self, result):
        self.batch, self.batch_path = result
        self.refresh_plan()
        self.tabs.select(self.main_tab)
        for warning in self.batch['warnings']:
            self.log(warning)
        for failed in self.batch.get('voice_failed_tasks', []):
            self.log(f'{failed["script_id"]} VOICE_FAILED：{failed["error"]}')
        for item in self.batch['items']:
            if item.get('status') == 'VOICE_FAILED':
                self.log(f"{item['script_id']} VOICE_FAILED / {item.get('voice_failure_stage','配音执行')}：{item.get('error','')}")
        self.log(f"已保存{len(self.batch['items'])}条组合方案，尚未渲染。双击已完成行可播放。")
        self.show_batch_errors(self.batch,planning=True)

    def show_batch_errors(self,batch,planning=False):
        from batch_error_summary import POLICY,summary,signature
        if batch.get('config',{}).get('batch_failure_policy')!=POLICY:return
        paused=self.planning_pause.is_set() if planning else self.pause.is_set() or self.abort.is_set()
        if paused:return
        message=summary(batch)
        reported=getattr(self,'_reported_batch_errors',{})
        key=batch.get('id','');token=signature(batch)
        if message and reported.get(key)!=token:
            messagebox.showerror('批次错误汇总',message,parent=self)
            reported[key]=token;self._reported_batch_errors=reported

    def retry_failed_voice(self):
        self.require_idle()
        if not self.batch_path:
            raise AssemblyError('请先打开含配音失败项的批次。')
        from batch_retention import ensure_resumable
        batch = ensure_resumable(self.batch_path,self.store)
        pending = list(batch.get('voice_failed_tasks', []))
        frozen = [item for item in batch['items'] if item.get('status') in ('VOICE_FAILED', 'VOICE_RETRYING')]
        if not pending and not frozen:
            self.log('当前批次没有需要重试的配音。')
            return
        cfg = batch['config']
        ffmpeg = resolve_tool('ffmpeg', cfg['ffmpeg'])
        path = self.batch_path
        for item in frozen:
            item['status'] = 'VOICE_RETRYING'
            item.pop('error', None)
        atomic_json(path, batch)
        self.batch = batch
        self.refresh_plan()
        def worker():
            from voice_retry import retry_batch_voice
            from core import now
            if cfg.get('voice_source')=='external_files':
                raise AssemblyError('外部配音不能调用本地 TTS 重配；请检查被冻结的文件，使用原任务继续生成。')
            provider = self.voice_provider or IndexTTS25Provider(log=self.worker_log)
            self.voice_provider = provider
            retry_batch_voice(batch, provider, ffmpeg, log=self.worker_log)
            batch['items'].sort(key=lambda i:i['number'])
            batch['voice_retry_at'] = now()
            atomic_json(path, batch)
            self.store.reserve(batch)
            self.script_library.reserve_batch(batch)
            return batch
        self.launch(worker, lambda result: self._retry_finished(result))

    def _retry_finished(self, batch):
        self.batch = batch
        self.refresh_plan()
        self.log(f"配音重试结束：待生成{sum(i['status']=='planned' for i in batch['items'])}条，仍失败{len(batch.get('voice_failed_tasks', []))+sum(i['status']=='VOICE_FAILED' for i in batch['items'])}条。")

    def refresh_plan(self):
        from workbench_ui import sync_empty_states
        sync_empty_states(self)
        self.plan_tree.delete(*self.plan_tree.get_children())
        if hasattr(self,'plan_status_var') and not self.lanes.running('planning'):
            self.plan_status_var.set(f"当前组合：{self.batch['id']} · {len(self.batch['items'])}条" if self.batch else '未载入组合方案')
        if not self.batch:return
        for item in self.batch['items']:
            self.plan_tree.insert('', 'end', iid=item['id'])
            self.update_item(item)
        for failed in self.batch.get('voice_failed_tasks', []):
            self.plan_tree.insert('', 'end', iid='voice_failure_'+failed['script_id'],
                                  values=(f"{failed['number']:02d}", '配音失败', '', '', '',
                                          failed['script_id'], (failed.get('voice_failure_stage','配音执行')+'：'+failed['error'])[:250]))
        for failed in self.batch.get('planning_failed_tasks',[]):
            self.plan_tree.insert('','end',iid='planning_failure_'+str(failed['number']),
                values=(f"{failed['number']:02d}",'配音失败（未规划）' if failed.get('failure_code')=='VOICE_FAILED' else '选片失败',
                        '','','',failed.get('failure_code',''),failed['error']))

    def update_item(self, item):
        # UI views own copies; never mutate the producer's frozen working batch.
        for batch in (self.batch,getattr(self,'render_batch_state',None)):
            for index,current in enumerate((batch or {}).get('items',[])):
                if current['id']==item['id']:
                    batch['items'][index]=copy.deepcopy(item);break
        self.update_tree_item(self.plan_tree,item,self.batch)
        if getattr(self,'render_tree',None):self.update_tree_item(self.render_tree,item,self.render_batch_state)

    def update_tree_item(self,tree,item,batch):
        names = {'planned':'待生成','rendering':'生成中','completed':'已完成','failed':'失败','RENDER_BLOCKED':'时序门禁待处理'}
        if tree.exists(item['id']):
            detail = (' → '.join((f"{c.get('timeline_role','')}:{c.get('relative_path',c['display'])}"
                                  if item.get('selection_rule_version') in STAGE_RULE_VERSIONS else c['display'])
                                 for c in item['clips']))
            if item.get('resolved_top_text_timing') is not None:
                fps = (batch or {}).get('config', {}).get('fps', item.get('output_fps', 30))
                top_span = (f"{item['top_text_start']:.1f}–{item['top_text_end']:.1f}秒"
                            if item.get('top_text_start') is not None else '无')
                bottom_span = (f"{item['bottom_subtitle_start']/fps:.1f}–"
                               f"{item['bottom_subtitle_end']/fps:.1f}秒"
                               if item.get('bottom_subtitle_start') is not None else '无')
                detail = (f"{MODE_LABELS.get(item['video_mode'], item['video_mode'])}；"
                          f"顶部{'有' if item['top_text_enabled'] else '无'}({top_span})；"
                          f"底部字幕({bottom_span}) | " + detail)
            if item.get('status') in ('VOICE_FAILED', 'VOICE_RETRYING','RENDER_BLOCKED'):
                detail = f"{item.get('voice_failure_stage','配音执行')}：{item.get('error','正在重新检查API及缓存')} | " + detail
            if item.get('clip_sfx_snapshot',{}).get('events'):
                effects=item['clip_sfx_snapshot']['events'];fps=item['clip_sfx_snapshot']['fps']
                detail+=' | 音效：'+'；'.join(f"{e['name']}@{e['start_frame']/fps:.2f}秒" for e in effects)
            if item.get('visual_layout_snapshot'):
                from visual_layout import label
                detail=label(item)+' | '+detail
            if item.get('sk_snapshot'):
                sk=item['sk_snapshot']
                detail=f'SK｜首镜{sk["opening_type"]}｜相似场景软调整{sk["soft_scene_adjustments"]}次｜明细见“所选方案”'
            if item.get('text_layer_timing_revision'):
                detail+=f' | 配音字幕结束后顶文({top_span})'
            if item.get('voice_speed_snapshot',{}).get('factor',1)!=1:
                detail+=f" | 配音{item['voice_speed_snapshot']['factor']:g}倍"
            tree.item(item['id'], values=(f"{item['number']:02d}", names.get(item['status'], item['status']),
                                 f"{item['duration']:.3f}", len(item['clips']), item['bgm']['name'],
                                 item.get('top_text', {}).get('text', '').replace('\n', ' / ')[:42],
                                 detail))

    def refresh_render(self):
        from workbench_ui import sync_empty_states
        sync_empty_states(self)
        self.render_tree.delete(*self.render_tree.get_children())
        batch=self.render_batch_state
        if batch:
            for item in batch['items']:
                self.render_tree.insert('','end',iid=item['id']);self.update_tree_item(self.render_tree,item,batch)
            self.render_status_var.set(f"视频批次：{batch['id']} · {len(batch['items'])}条")
        self.refresh_queued_rows()

    def open_render_directory(self):
        batch=self.render_batch_state
        if not batch:raise AssemblyError('尚未开始视频生成。')
        open_local(batch.get('output_date_folder') or batch['config']['output_root'])

    def open_render_output(self,event=None):
        if not self.render_batch_state:return
        selected=self.render_tree.selection()
        if not selected:return
        item=next((i for i in self.render_batch_state['items'] if i['id']==selected[0]),None)
        if item and item.get('status')=='completed':open_local(item['output_path'])

    def cleanup_history_timer(self):
        from batch_retention import sweep,INTERVAL_MS
        self._retention_after=self.after(INTERVAL_MS,self.cleanup_history_timer)
        if self.busy or self.lanes.running('planning') or self.history_cleaning:return
        self.history_cleaning=True
        def finish(report):
            self.history_cleaning=False
            removed=sum(r['removed_completed'] for r in report['batches'])
            expired=sum(r['expired_unfinished'] for r in report['batches'])
            if removed or expired:self.log(f'历史自动清理：移除已完成组合{removed}条，到期未完成组合{expired}条；成片保留。')
            if report['errors']:self.log('历史清理部分失败，保留现场，下次重试：'+str(report['errors']))
            if self.batch_path and not self.busy:
                self.batch=read_json(self.batch_path,None)
                if self.batch is None:self.batch_path=None
                self.refresh_plan()
        def failed(exc):
            self.history_cleaning=False
            self.log('历史自动清理失败，将在下次检查时重试：'+str(exc))
        self.lanes.submit('batch-history',lambda:sweep(self.store),finish,on_error=failed)

    def load_batch(self):
        self.require_planning_idle()
        from batch_history_ui import HistoryWindow
        HistoryWindow(self)

    def open_batch_path(self,p):
        self.require_planning_idle()
        if self.busy and getattr(self,'render_batch_path',None) and Path(p).resolve()==Path(self.render_batch_path).resolve():
            raise AssemblyError('该批次正在视频区生成；请在视频区查看进度。')
        from batch_retention import ensure_resumable
        batch = ensure_resumable(Path(p),self.store)
        if batch.get('schema_version') != 1 or not isinstance(batch.get('items'), list):
            raise AssemblyError('不是本工具支持的批次清单。')
        self.batch, self.batch_path = batch, Path(p)
        self.script_library.reconcile_batch(batch)
        self.refresh_plan()
        self.log('已载入原批次。继续生成使用批次中保存的参数，不重新随机，不改变时长。')
        if batch.get('history_retention'):
            self.log('未完成组合保留到 '+batch['history_retention']['expires_at']+'；仅查看不会延长期限。')

    def import_render_history(self):
        from batch_history_ui import HistoryWindow
        self.render_history_window=HistoryWindow(self,on_load=self.queue_plan)

    def open_render_batch_path(self,path):
        self.require_render_idle()
        from batch_retention import ensure_resumable
        batch=ensure_resumable(Path(path),self.store)
        self.review_first_var.set(bool(batch['config'].get('review_first_enabled',False)))
        if batch.get('schema_version')!=1 or not isinstance(batch.get('items'),list):raise AssemblyError('不是本工具支持的批次清单。')
        self.script_library.reconcile_batch(batch)
        self.render_batch_state=copy.deepcopy(batch);self.render_batch_path=Path(path)
        self.refresh_render();self.tabs.select(self.render_tab)
        self.log('已导入过往批次到视频页：'+batch['id']+'；尚未开始生成，工作台方案保持。')

    def send_plan_to_render(self):
        if not self.batch_path:raise AssemblyError('请先生成组合方案，或打开工作台历史批次。')
        self.queue_plan(self.batch_path)

    def render(self):
        self.require_render_idle()
        queued=self.video_queue.next()
        path=getattr(self,'render_batch_path',None) or (Path(queued['path']) if queued else None) or self.batch_path
        if path is None:
            queued=self.video_queue.next()
            if queued:path=Path(queued['path'])
            else:raise AssemblyError('请导入过往批次，或在工作台将组合方案送到视频生成。')
        queued_consent=any(e['path']==str(Path(path).resolve()) and e.get('rights_confirmed') for e in self.video_queue.entries)
        if not self.rights_var.get() and not queued_consent:
            raise AssemblyError('继续本批次前，请确认音乐可用于本次用途。')
        # Tool paths may change without re-planning. Artistic and timing settings remain locked.
        current = self.collect_config()
        from batch_retention import ensure_resumable
        batch = ensure_resumable(path,self.store)
        from batch_revision import activate
        batch=activate(path,self.store,self.script_library)
        if not batch.get('review_session') and hasattr(self,'review_first_var'):
            batch['config']['review_first_enabled']=bool(self.review_first_var.get())
        from background_tasks import snapshot_batch_references
        snapshot_batch_references(batch,Path(path).parent/'input_snapshot')
        output_root = Path(batch['config']['output_root'])
        output_root.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(output_root).free < 2 * 1024**3:
            raise AssemblyError('磁盘剩余空间不足2 GiB，请先清理程序临时文件。第7页有“清理临时文件”入口。')
        for k in ('ffmpeg', 'ffprobe', 'node', 'remotion_project', 'browser', 'concurrency'):
            batch['config'][k] = current[k]
        atomic_json(path, batch)
        if (self.batch or {}).get('id')==batch['id']:self.batch = copy.deepcopy(batch)
        self.render_batch_state=copy.deepcopy(batch);self.render_batch_path=path
        self.refresh_render()
        def on_item(item):
            if item.get('status') in ('completed', 'failed', 'VOICE_FAILED'):
                self.script_library.finish_item(item, item['status'] == 'completed')
            self.events.put(('item', copy.deepcopy(item)))
        def worker():
            if batch['config'].get('voice_job_temp_dir'):
                from voice_production_pipeline import run_pipeline
                provider=None
                if batch['config'].get('voice_source','indextts25')!='external_files':
                    provider=self.voice_provider or IndexTTS25Provider(log=self.worker_log)
                    self.voice_provider=provider
                    provider.cancel_event=self.abort
                    provider.resource_timeout=batch["config"].get("resource_wait_timeout_seconds",300)
                    provider.request_timeout=batch["config"].get("tts_timeout_seconds",900)
                # The pipeline owns the batch lease. Wrapping it again here
                # would make the same thread wait for its own non-reentrant lock.
                return run_pipeline(
                    path,batch,self.store,self.script_library,provider,
                    resolve_tool('ffmpeg',batch['config']['ffmpeg']),log=self.worker_log,
                    pause=self.pause,cancel=self.abort,on_item=on_item)
            return render_batch(path,self.store,self.worker_log,on_item,self.pause,self.abort)
        self.video_queue.start(path,batch)
        self.launch(worker,lambda result:self.rendered(result,path),allow_planning=True)
        self.render_running=True
        self.render_progress.start(12);self.render_btn.configure(state='disabled')
        progress_visible(self.render_progress,True)
        self.tabs.select(self.render_tab)
        self.abort_btn.configure(state='normal')

    def rendered(self, batch, path=None):
        self.render_batch_state=copy.deepcopy(batch)
        path=path or getattr(self,'render_batch_path',None) or self.batch_path
        if self.pause.is_set() or self.abort.is_set():self.video_queue.halt()
        elif path:self.video_queue.complete(path)
        selected=(self.batch or {}).get('id')==batch['id']
        if selected:self.batch=copy.deepcopy(batch)
        completed = sum(i['status'] == 'completed' for i in batch['items'])
        failed = sum(i['status'] == 'failed' for i in batch['items'])
        voice_failed = len(batch.get('voice_failed_tasks', [])) + sum(i['status']=='VOICE_FAILED' for i in batch['items'])
        self.log(f'本轮结束：完成{completed}/{len(batch["items"])}条，失败{failed}条，配音失败{voice_failed}条。原素材声音均已排除。')
        has_queue_next=self.video_queue.auto and self.video_queue.next() is not None
        earlier=getattr(self,'_queue_errors',[])
        if has_queue_next or earlier:
            if failed or voice_failed:
                from batch_error_summary import summary
                earlier.append(batch['id']+'：'+summary(batch));self._queue_errors=earlier
            if not has_queue_next and earlier:
                self._queue_errors=[];messagebox.showerror('待剪辑队列错误汇总','\n\n'.join(earlier),parent=self)
        else:self.show_batch_errors(batch)
        if batch.get('history_retention'):
            self.log(f'累计已生成{completed+batch["history_retention"].get("completed_count",0)}条；历史仅保留未完成组合。')
            if selected:
                self.batch=read_json(path,None) if path else None
                if self.batch is None:self.batch_path=None
        if selected:self.refresh_plan()
        if path and not Path(path).exists():self.render_batch_path=None
        self.refresh_render()
        self.render_status_var.set(f"视频处理结束：{batch['id']} · 完成{completed}条，失败{failed+voice_failed}条")
        if batch.get('review_session',{}).get('state')=='waiting':
            self.render_status_var.set('首条已完成，等待审片；后续视频尚未开始。')
            if hasattr(self,'review_hint'):
                self.review_hint.set('先查看首条成片；需要调整时点“编辑本批参数”，修改后点“按新参数继续”。')
        if self.video_queue.auto and self.video_queue.next():
            self.after_idle(self.continue_render_queue)

    def open_output(self, event=None):
        if not self.batch:
            return
        selected = self.plan_tree.selection()
        if selected:
            item = next((i for i in self.batch['items'] if i['id'] == selected[0]), None)
            if item is None:
                return
            if Path(item['output_path']).exists():
                self.guard(lambda: open_local(item['output_path']))
            elif item.get('error'):
                messagebox.showerror('该条尚未完成', item['error'], parent=self)
            else:
                messagebox.showinfo('尚未生成', '\n'.join(c['display'] for c in item['clips']), parent=self)

    def refresh_text_preview(self):
        if not hasattr(self, 'text_preview'):
            return
        path = self.var('top_text_library').get().strip().strip('"')
        lines = []
        try:
            items = load_top_texts(path) if path else []
            lines.append(f'共 {len(items)} 条顶部文案\n')
            for i, value in enumerate(items, 1):
                lines.append(f'【{i}】\n{value}\n')
        except Exception as exc:
            lines.append(f'文案库读取失败：{exc}')
        self.text_preview.configure(state='normal')
        self.text_preview.delete('1.0', 'end')
        self.text_preview.insert('1.0', '\n'.join(lines))
        self.text_preview.configure(state='disabled')

    def open_text_library(self):
        path = self.var('top_text_library').get().strip().strip('"')
        if not path:
            raise AssemblyError('尚未设置文案库路径。')
        open_local(path)

    def open_clip_sfx(self):
        from clip_sfx_ui import open_manager
        open_manager(self)

    def request_plan_pause(self):
        self.planning_pause.set()
        self.log('已请求停止本次组合：当前准备步骤安全结束后保存已完成方案，视频生成继续。')

    def request_pause(self):
        self.pause.set()
        self.log('已请求暂停视频：当前成片完成后暂停，组合方案准备继续。')

    def request_abort(self):
        self.abort.set()
        self.pause.set()
        self.log('已请求取消当前生成：仅终止组合器本次启动的子进程；方案可继续。')

    def check_env(self):
        cfg = self.collect_config()
        def worker():
            messages = []
            for name in ('ffmpeg', 'ffprobe', 'node'):
                try:
                    p = resolve_tool(name, cfg[name])
                    text = run_capture([p, '--version' if name == 'node' else '-version'], 15).splitlines()[0]
                    messages.append(f'{name}: {p}\n{text}')
                except Exception as exc:
                    messages.append(str(exc))
            try:
                node, root = remotion_environment(cfg)
                messages.append(f'Remotion可用：{root}')
            except Exception as exc:
                messages.append(str(exc))
            return '\n\n'.join(messages)
        self.launch(worker, lambda text: messagebox.showinfo('环境检查', text, parent=self))

    def install_renderer(self):
        self.require_idle()
        if not messagebox.askyesno('首次安装', '将调用npm联网安装独立渲染依赖，不修改已有项目。\n已有Remotion工程时优先在上面选择它。继续安装？', parent=self):
            return
        from core import stream_process
        self.launch(lambda: stream_process([sys.executable, '-u', str(APP_DIR/'setup_renderer.py')], self.data_dir/'install.log', self.worker_log, 1800),
                    lambda _: self.log('依赖安装结束。请点击环境检查；详细输出在数据目录install.log。'))

    def cleanup_temporary_files(self):
        if self.busy or self.lanes.running("planning") or self.lanes.running("voice"):
            raise AssemblyError('请等待当前任务结束后再清理临时文件。')
        count, size = manual_cleanup(self.data_dir, dry_run=True)
        if not count:
            self.log('没有可安全清理的程序临时文件；未触碰正式素材和输出。')
            return
        if not messagebox.askyesno('清理临时文件',
                f'预计清理{count}个程序临时文件，释放{size/1024**3:.3f} GiB。\n'
                '范围：已完成任务临时媒体、音色试听、未保护诊断副本及未被待生成方案引用的旧配音缓存。\n'
                '正式视频、素材、音乐、音色参考和文案库不在清理范围。继续？', parent=self):
            return
        removed, freed = manual_cleanup(self.data_dir)
        self.log(f'临时文件清理完成：{removed}个文件，释放{freed/1024**3:.3f} GiB。')

    def on_close(self):
        if self.busy or self.lanes.active:
            if messagebox.askyesno('任务仍在运行', '是否在当前成片/步骤完成后关闭？\n已有方案会保留，可下次继续。', parent=self):
                self.pending_close = True
                self.pause.set()
                self.planning_pause.set()
                self.lanes.cancel("voice")
            return
        self._finish_close()

    def _finish_close(self):
        try:
            self.cfg = self.collect_config()
            self.persist_config(self.cfg)
        except Exception:
            pass
        if self.voice_provider:
            self.voice_provider.close_owned()
        if self.planning_provider and self.planning_provider is not self.voice_provider:
            self.planning_provider.close_owned()
        if getattr(self,'subtitle_editor',None) and self.subtitle_editor.winfo_exists():
            self.subtitle_editor.close()
        self.log_tab.close()
        self.diagnostics.close()
        self.destroy()

    def destroy(self):
        if getattr(self,'_destroyed',False):return
        self._destroyed=True
        if getattr(self,'_qt_timer_bridge',None):self._qt_timer_bridge.close()
        # The App owns this Tcl interpreter, including callbacks from child pages.
        for pending in self.tk.splitlist(self.tk.call('after','info')):
            try:self.tk.call('after','cancel',pending)
            except tk.TclError:pass
        if getattr(self,'_pump_after',None):
            try:self.after_cancel(self._pump_after)
            except tk.TclError:pass
        if getattr(self,'_retention_after',None):
            try:self.after_cancel(self._retention_after)
            except tk.TclError:pass
        if hasattr(self,'log_tab'):self.log_tab.close()
        if hasattr(self,'diagnostics'):self.diagnostics.close()
        if hasattr(self,'ui_style'):
            from workbench_ui import release_theme
            release_theme(self)
        super().destroy()


def main():
    parser = argparse.ArgumentParser(description='本地整段组合器：手动文案、可选本地配音与字幕、BGM')
    parser.add_argument('--data-dir', default=str(APP_DIR / 'data'))
    parser.add_argument('--tk',action='store_true',help='使用兼容Tk主窗口')
    args = parser.parse_args()
    if os.name == 'nt' and args.tk:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        with instance_lock(Path(args.data_dir)):
            if args.tk:
                app = App(Path(args.data_dir));app.mainloop()
            else:
                from qt_workbench import run
                run(Path(args.data_dir))
    except Exception as exc:
        print(exc, file=sys.stderr)
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('无法启动', str(exc))
            root.destroy()
        except Exception:
            pass
        raise SystemExit(1)

if __name__ == '__main__':
    main()
