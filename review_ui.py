from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import ttk


class ReviewControls:
    def build_review_controls(self,parent):
        row=ttk.Frame(parent);row.pack(fill='x',padx=5,pady=5)
        self.review_first_var=tk.BooleanVar(value=self.cfg.get('review_first_enabled',False))
        ttk.Checkbutton(row,text='首条完成后暂停审片',variable=self.review_first_var).pack(side='left')
        for label,fn in [('查看首条成片',self.open_review_first),('编辑本批参数',self.edit_review_batch),
                         ('按新参数继续',self.apply_review_batch),('保留原参数继续',self.accept_review_batch)]:
            ttk.Button(row,text=label,command=lambda f=fn:self.guard(f)).pack(side='left',padx=5)
        self.review_hint=tk.StringVar(value='暂停后可更新本批未开始的视频；已完成及其他排队批次保持各自设置。')
        ttk.Label(parent,textvariable=self.review_hint).pack(anchor='w',padx=6)

    def review_target(self):
        self.require_idle()
        path=getattr(self,'render_batch_path',None)
        if not path:raise ValueError('请先在视频生成页载入本批次')
        from batch_retention import ensure_resumable
        ensure_resumable(path,self.store)
        from batch_revision import activate
        return Path(path),activate(path,self.store,self.script_library)

    def open_review_first(self):
        from core import open_local,read_json
        path=getattr(self,'render_batch_path',None)
        batch=read_json(path,{}) if path else getattr(self,'render_batch_state',{}) or {}
        output=batch.get('review_session',{}).get('first_output')
        if not output or not Path(output).is_file():raise ValueError('本批尚无首条审片成片')
        open_local(output)

    def edit_review_batch(self):
        path,batch=self.review_target()
        self.video_queue.halt()
        self.review_workbench_backup=deepcopy(self.collect_config())
        self.review_edit_path=path
        self.sync_review_controls(deepcopy(batch['config']))
        self.review_hint.set('正在编辑本批参数；完成后点“按新参数继续”。只更新本批未完成条目。')
        self.tabs.select(self.main_tab)
        self.log('已载入本批参数。可修改配音、音调、音量、字幕、顶文、画面、转场及选片参数。')

    def sync_review_controls(self,cfg):
        from visual_layout import LABELS
        self.cfg=cfg
        for name,var in self.vars.items():
            if name in cfg:var.set(str(cfg[name]))
        for name,var in self.processing_bools.items():
            if name in cfg:var.set(bool(cfg[name]))
        for prefix,var in self.processing_scope_vars.items():
            var.set(next((v for v,k in self._processing_scope_reverse.items() if k==cfg.get(prefix+'_scope')),'整条成片'))
        for name,(var,mapping) in self.timing_enum_vars.items():
            var.set(next((label for label,value in mapping.items() if value==cfg.get(name)),next(iter(mapping))))
        for name,var in self.group_vars.items():var.set(cfg.get('group_folders',{}).get(name,name))
        for attr,name in [('script_source','script_source'),('script_input_mode','script_input_mode'),
                          ('script_library_strategy','script_library_strategy'),('allow_script_repeat','allow_script_repeat'),
                          ('voice_strategy_var','voice_strategy'),('subtitle_position_var','subtitle_position'),
                          ('audio_mix_var','audio_mix_profile'),('top_use_var','top_text_use_policy'),
                          ('top_percent_var','top_text_use_percent'),('intro_seconds_var','intro_top_text_duration'),
                          ('voice_cache_var','voice_cache_enabled')]:
            if name in cfg:getattr(self,attr).set(cfg[name])
        self.top_timing_var.set({'auto':'自动','start':'开头显示','after_voice':'配音结束后显示','full':'全程显示'}[cfg.get('top_text_timing','auto')])
        self.top_text_mode_var.set('顺序' if cfg.get('top_text_mode')=='sequence' else '随机去重')
        self.dedup_var.set('更严格' if cfg.get('dedup_mode')=='strict' else '均衡')
        self.music_mode_var.set('自然区间（先确认）' if cfg.get('music_mode')=='natural' else '允许音乐截尾淡出')
        for mode,var in self.mode_percent_vars.items():var.set(cfg.get('video_mode_percentages',{}).get(mode,0))
        for voice,var in self.voice_checks.items():var.set(voice in cfg.get('voice_selected_ids',[]))
        self.visual_layout_var.set(next(k for k,v in LABELS.items() if v==cfg.get('visual_layout','normal')))
        self.voice_speed_enabled_var.set(cfg.get('voice_speed_enabled',False));self.voice_speed_factor_var.set(str(cfg.get('voice_speed_factor',1.15)))
        self.voice_speed_selected=list(cfg.get('voice_speed_selected',[]));self.update_speed_summary()
        self.voice_pitch_var.set(str(cfg.get('voice_pitch_semitones',0)))
        self.editorial_enabled_var.set(cfg.get('editorial_enabled',False))
        self.review_first_var.set(cfg.get('review_first_enabled',False))
        from edit_style import LABELS as STYLES
        self.edit_style_var.set(next(k for k,v in STYLES.items() if v==cfg.get('edit_style','plain')))
        self.edit_style_audio_var.set(cfg.get('edit_style_audio',True))
        self.edit_style_push_var.set(cfg.get('edit_style_push',True))
        self.load_profile_controls()

    def accept_review_batch(self):
        from core import atomic_json
        from review_session import resume
        path,batch=self.review_target();atomic_json(path,resume(batch))
        self.review_edit_path=None
        if getattr(self,'review_workbench_backup',None):
            self.sync_review_controls(self.review_workbench_backup);self.review_workbench_backup=None
        self.render()

    def apply_review_batch(self):
        from batch_revision import prepare,activate
        from background_tasks import snapshot_profiles
        path,batch=self.review_target()
        if path!=getattr(self,'review_edit_path',None):raise ValueError('请先点“编辑本批参数”，再调整参数并继续')
        cfg=self.collect_config();profiles=deepcopy(self.voice_profiles)
        self.video_queue.halt()
        def worker():
            provider=getattr(self,'voice_provider',None) if cfg.get('voice_source')!='external_files' else None
            from batch_retention import run_lease
            with run_lease(path,self.store,batch):
                prepare(path,cfg,self.store,self.script_library,profiles,provider,self.worker_log,self.pause)
                return activate(path,self.store,self.script_library,leased=True)
        def done(updated):
            self.render_batch_state=updated;self.review_edit_path=None
            previous=getattr(self,'review_workbench_backup',None)
            if previous and (previous.get('editing_profile_id'),previous.get('product_spec_id'))!=(updated['config'].get('editing_profile_id'),updated['config'].get('product_spec_id')):
                self.sync_review_controls(previous)
            self.review_workbench_backup=None
            self.review_hint.set('本批待生成条目已采用新参数；首条成片保留。')
            self.render()
        self.launch(worker,done)
