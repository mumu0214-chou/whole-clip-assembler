"""Profile controls using the existing App, Store and task engine."""
import copy
import tkinter as tk
from tkinter import ttk, messagebox
import editing_profiles as profiles
from product_catalog_ui import CatalogControls
from output_style_ui import OutputStyleControls
from visual_controls_ui import VisualControls

BALL_TRANSITIONS={'自然淡化':'natural','柔和淡暗':'gentle','直接切换':'none'}
BALL_EFFECTS={'原画':'original','轻推近':'gentle_push','清亮':'fresh','质感':'texture'}


def ball_label(mapping,code):
    return next((label for label,value in mapping.items() if value==code),next(iter(mapping)))


class EditingProfileMixin(CatalogControls, OutputStyleControls, VisualControls):
    def build_profile_controls(self):
        bar=ttk.LabelFrame(self.main_content,text='商品与共用剪辑规则')
        bar.pack(fill='x',padx=8,pady=4,before=self.main_content.winfo_children()[0])
        first=ttk.Frame(bar);first.pack(fill='x')
        from workbench_ui import Disclosure
        self.profile_details=Disclosure(bar,'当前规则与时长摘要',expanded=False);self.profile_details.pack(fill='x',pady=3)
        second=ttk.Frame(self.profile_details.body);second.pack(fill='x',pady=3)
        self.profile_name=tk.StringVar(value=profiles.NAMES[self.cfg['editing_profile_id']])
        box=ttk.Combobox(first,textvariable=self.profile_name,values=list(profiles.NAMES.values()),state='readonly',width=18)
        box.pack(side='left',padx=5);box.bind('<<ComboboxSelected>>',lambda _:self.guard(self.switch_profile))
        self.build_spec_controls(bar,box)
        self.build_output_style_controls(bar)
        from sk_ui import build as build_sk_controls
        build_sk_controls(self,bar)
        self.match_voice_var=tk.BooleanVar(value=self.cfg.get('match_video_to_voice',False))
        ttk.Checkbutton(first,text='按配音时长选片并裁差额',variable=self.match_voice_var).pack(side='left',padx=8)
        self.match_voice_var.trace_add('write',self.timing_chooser.sync)
        ttk.Button(first,text='时长裁切设置',command=lambda:self.tabs.select(self.timing_tab)).pack(side='left')
        ttk.Button(first,text='转场 / 画面特效',command=self.open_visual_settings).pack(side='left',padx=6)
        self.visual_summary_var=tk.StringVar()
        ttk.Label(self.profile_details.body,textvariable=self.visual_summary_var).pack(anchor='w',padx=5)
        ttk.Label(first,text='仅影响新方案；已冻结/运行批次保留原配置。').pack(side='left',padx=12)
        ttk.Label(second,text='有顶文：配音结束后接画面＋顶文＋BGM至计划片尾；无顶文：随配音结束。均受最短/最长时长限制。').pack(side='left',padx=5)
        ttk.Label(self.profile_details.body,textvariable=self.timing_summary,wraplength=840).pack(fill='x',padx=5,pady=3)
        self.grouped_panel=ttk.LabelFrame(self.main_content,text='牛肉丸 · G组素材确认')
        grouped_rules=ttk.Frame(self.grouped_panel);grouped_rules.pack(fill='x',pady=3)
        ttk.Button(grouped_rules,text='按组复核 / 安全裁切',command=lambda:self.guard(self.review_grouped)).pack(side='left',padx=6)
        self.clip_sfx_quick_control=ttk.Checkbutton(grouped_rules,text='播放片段音效',variable=self.clip_sfx_enabled_var)
        self.clip_sfx_quick_control.pack(side='left',padx=(16,6))
        ttk.Button(grouped_rules,text='音效设置 / 绑定',command=lambda:self.guard(self.open_clip_sfx)).pack(side='left',padx=6)
        self.clip_sfx_switch_hint=tk.StringVar()
        ttk.Label(grouped_rules,textvariable=self.clip_sfx_switch_hint).pack(side='left',padx=8)
        self.clip_sfx_enabled_var.trace_add('write',lambda *_:self.update_clip_sfx_switch_hint())
        self.script_scope_var=tk.StringVar(value=profiles.NAMES[self.cfg['editing_profile_id']])
        ttk.Label(second,text='文案来源').pack(side='left',padx=(15,2))
        ttk.Label(second,textvariable=self.script_scope_var).pack(side='left')
        ttk.Label(second,text='（随剪辑方案切换）').pack(side='left',padx=3)
        self.voice_source_var=tk.StringVar(value='外部配音文件' if self.cfg.get('voice_source')=='external_files' else '本地 IndexTTS-2.5')
        self.external_strategy_var=tk.StringVar(value=self.cfg.get('external_voice_strategy','均衡轮换'))
        self.external_only_var=tk.BooleanVar(value=self.cfg.get('external_voice_only_scripts',False))
        voice_box=ttk.LabelFrame(self.voice_content,text='配音来源与外部声音选择');voice_box.pack(fill='x',padx=8,pady=8)
        voice=ttk.Frame(voice_box);voice.pack(fill='x',pady=4)
        ttk.Label(voice,text='配音来源').pack(side='left',padx=5)
        self.voice_source_display_var=tk.StringVar(value=self.voice_source_var.get())
        self.voice_source_control=ttk.Combobox(voice,textvariable=self.voice_source_display_var,values=('本地 IndexTTS-2.5','外部配音文件'),state='readonly',width=21)
        self.voice_source_control.pack(side='left')
        self.voice_source_control.bind('<<ComboboxSelected>>',lambda _:self.voice_source_var.set(self.voice_source_display_var.get()))
        ttk.Label(voice,text='外部声音选择').pack(side='left',padx=(12,3))
        self.external_strategy_control=ttk.Combobox(voice,textvariable=self.external_strategy_var,values=('均衡轮换','随机','指定'),state='readonly',width=12)
        self.external_strategy_control.pack(side='left')
        self.external_only_control=ttk.Checkbutton(voice,text='仅选已有有效外部配音的文案',variable=self.external_only_var)
        self.external_only_control.pack(side='left',padx=8)
        ttk.Button(voice,text='导出/绑定/管理配音',command=lambda:self.open_script_library()).pack(side='left',padx=5)
        voice_filter=ttk.Frame(voice_box);voice_filter.pack(fill='x',pady=3)
        self.external_voice_selected_names=copy.deepcopy(self.cfg.get('external_voice_selected_names'))
        self.external_voice_summary=tk.StringVar(value='')
        self.external_voice_selector_control=ttk.Button(voice_filter,text='外部音色多选',command=lambda:self.guard(self.open_external_voice_selector))
        self.external_voice_selector_control.pack(side='left',padx=5)
        ttk.Button(voice_filter,text='文案ID一键配对配音库',command=lambda:self.guard(self.open_external_pairing)).pack(side='left',padx=5)
        ttk.Label(voice_filter,textvariable=self.external_voice_summary,wraplength=950).pack(side='left',padx=8)
        ttk.Label(voice_box,text='无配音视频显示“不适用”；指定声音在各篇文案内设置。').pack(anchor='w',padx=5)
        self.build_voice_speed_controls(self.voice_content)
        self.voice_source_var.trace_add('write',lambda *_:self._update_video_policy_preview())
        self.load_profile_controls()
        self._update_video_policy_preview()

    def sync_voice_source_controls(self, voiced):
        if not hasattr(self,'voice_source_control'):return
        self.voice_source_display_var.set(self.voice_source_var.get() if voiced else '不适用')
        self.voice_source_control.configure(state='readonly' if voiced else 'disabled')
        external=voiced and self.voice_source_var.get()=='外部配音文件'
        self.external_strategy_control.configure(state='readonly' if external else 'disabled')
        self.external_only_control.state(['!disabled' if external else 'disabled'])
        self.external_voice_selector_control.state(['!disabled' if external else 'disabled'])

    def update_clip_sfx_switch_hint(self):
        self.clip_sfx_switch_hint.set('音效开：仅用于新方案' if self.clip_sfx_enabled_var.get()
                                      else '音效关：仍按场景分类选片，配音和BGM保持')

    def update_external_voice_summary(self):
        names=self.external_voice_selected_names
        self.external_voice_summary.set('全部有效绑定' if names is None else '仅选 '+str(len(names))+' 个音色：'+'、'.join(names))

    def open_external_voice_selector(self):
        catalog=self.script_library.external.voice_catalog(self.cfg['editing_profile_id'],self.cfg.get('product_spec_id','default'))
        profile=self.cfg['editing_profile_id'];spec=self.cfg.get('product_spec_id','default')
        def apply(names):
            if profile!=self.cfg['editing_profile_id'] or spec!=self.cfg.get('product_spec_id','default'):raise ValueError('品类或规格已切换，请关闭并重新选择当前规格音色。')
            self.external_voice_selected_names=names
            self.update_external_voice_summary()
        if getattr(self,'_qt_parent',None) is not None:
            from external_voice_qt import ExternalVoiceSelectorDialog
            existing=getattr(self,'external_voice_selector_window',None)
            if isinstance(existing,ExternalVoiceSelectorDialog) and not existing.closed:
                existing.show();existing.raise_();existing.activateWindow();return
            self.external_voice_selector_window=ExternalVoiceSelectorDialog(self,catalog,self.external_voice_selected_names,apply)
            self.external_voice_selector_window.show();return
        from external_voice_ui import ExternalVoiceSelector
        self.external_voice_selector_window=ExternalVoiceSelector(self,catalog,self.external_voice_selected_names,apply)

    def open_external_pairing(self):
        if getattr(self,'_qt_parent',None) is not None:
            from external_voice_qt import ExternalPairingDialog
            existing=getattr(self,'external_pairing_window',None)
            if isinstance(existing,ExternalPairingDialog) and not existing.closed:
                existing.show();existing.raise_();existing.activateWindow();return
            from qt_workbench import C
            self.external_pairing_window=ExternalPairingDialog(self);self.external_pairing_window.show()
            C.QTimer.singleShot(0,self.external_pairing_window.start);return
        from script_library_ui import ScriptLibraryWindow
        from script_tools_ui import ExternalWindow
        owner=ScriptLibraryWindow(self,self.script_library,profile_id=self.cfg['editing_profile_id'])
        window=ExternalWindow(owner)
        window.pair_library()

    def profile_config(self,cfg):
        cfg=profiles.clean(cfg)
        if hasattr(self,'product_spec_var'):
            cfg=self.script_library.catalog.freeze(cfg,self.selected_spec())
        from shot_trim_controls import REVISION,DEFAULTS
        cfg['shot_trim_revision']=REVISION
        for key in ('shot_trim_range_min','shot_trim_range_max'):cfg[key]=float(cfg.get(key,DEFAULTS[key]))
        cfg['match_video_to_voice']=self.match_voice_var.get()
        cfg['voice_fit_mode']='random_edges_v1'
        # New plans opt in explicitly; frozen manifests keep their old allocator.
        cfg['voice_fit_capacity_revision']='voice_fit_capacity_v2'
        cfg['batch_failure_policy']='skip_recompose_v1'
        cfg['history_retention_revision']='unfinished_history_72h_v1'
        cfg['grouped_search_revision']='grouped_frozen_slot_search_v1'
        cfg['external_voice_retry_limit']=3
        for key,default in [('voice_fit_max_trim_seconds',1.),('voice_fit_max_excess_seconds',3.)]:
            cfg[key]=float(self.vars[key].get() or default)
        cfg['script_library_profile']=cfg['editing_profile_id']
        cfg['voice_source']='external_files' if self.voice_source_var.get()=='外部配音文件' else 'indextts25'
        cfg['external_voice_strategy']=self.external_strategy_var.get()
        cfg['external_voice_only_scripts']=self.external_only_var.get()
        cfg['external_voice_selected_names']=copy.deepcopy(self.external_voice_selected_names)
        cfg['external_voice_selection_revision']='filename_voice_filter_v1'
        cfg['clip_sfx_enabled']=bool(self.clip_sfx_enabled_var.get()) if profiles.ball_profile(cfg) and hasattr(self,'clip_sfx_enabled_var') else False
        cfg=profiles.new_plan_config(cfg)
        if cfg.get('material_format') == 'sk_diverse':
            from sk_materials import TYPES
            cfg['group_folders']={k:k for k in TYPES}
        if profiles.ball_profile(cfg):
            cfg['ball_pacing_revision']='ball_pacing_v1'
        from visual_controls import REVISION
        cfg['visual_controls_revision']=REVISION
        cfg['script_draft_text']=self.script_input.get('1.0','end-1c')
        return cfg

    def persist_config(self,cfg):
        from core import atomic_json
        cfg=profiles.clean(cfg)
        self.profile_state=profiles.save(self.profile_state,cfg)
        atomic_json(self.config_path,profiles.document(self.profile_state,cfg))
        self.cfg=cfg
        if hasattr(self,'sk_scheme_row'):
            from sk_ui import sync as sync_sk
            sync_sk(self)

    def load_profile_controls(self):
        cfg=self.cfg
        self.sync_group_folder_controls()
        self.sync_output_style()
        self.sync_visual_controls()
        if hasattr(self,'opening_c_shortcut'):
            self.opening_c_shortcut.state(['disabled' if profiles.grouped(cfg) else '!disabled'])
        if hasattr(self,'clip_sfx_enabled_var'):
            self.clip_sfx_enabled_var.set(bool(cfg.get('clip_sfx_enabled',False)))
            self.clip_sfx_enable_control.state(['!disabled' if profiles.ball_profile(cfg) else 'disabled'])
            self.clip_sfx_quick_control.state(['!disabled' if profiles.ball_profile(cfg) else 'disabled'])
            self.update_clip_sfx_switch_hint()
        self.voice_source_var.set('外部配音文件' if cfg.get('voice_source')=='external_files' else '本地 IndexTTS-2.5')
        self.external_strategy_var.set(cfg.get('external_voice_strategy','均衡轮换'))
        self.external_only_var.set(cfg.get('external_voice_only_scripts',False))
        self.external_voice_selected_names=copy.deepcopy(cfg.get('external_voice_selected_names'))
        self.update_external_voice_summary()
        self.structure_hint_var.set('SK：仅使用SK库；自由交叉、同类可连续；首镜A/E＋人工指定，类别按整批时长软偏好，检查近72小时及有效预留。' if cfg.get('material_format')=='sk_diverse' else '牛肉丸：全量镜头优先轮换；掰开/煮汤/展示/吃按整批时长4:3:2:1软平衡，单条可缺类，其他画面参与。覆盖去重及安全裁切保持。'
            if profiles.grouped(cfg) else '鲜切牛肉：首镜 A 或 C/开头 → A/B 前段 → 可选连续 C → 可选 D → E 结尾；原素材不修改。')
        self.profile_name.set(profiles.NAMES[cfg['editing_profile_id']])
        self.refresh_catalog_controls()
        self.match_voice_var.set(cfg.get('match_video_to_voice',False))
        self.script_scope_var.set(profiles.NAMES[cfg['editing_profile_id']])
        self.refresh_catalog_controls()
        self.grouped_panel.pack_forget()
        if profiles.ball_profile(cfg) and profiles.grouped(cfg):self.grouped_panel.pack(fill='x',padx=8,pady=3,after=self.main_content.winfo_children()[0])
        from sk_ui import sync as sync_sk
        sync_sk(self)

    def switch_profile(self):
        if getattr(self,'subtitle_editor',None) and self.subtitle_editor.winfo_exists():
            self.profile_name.set(profiles.NAMES[self.cfg['editing_profile_id']])
            raise ValueError('请先保存或关闭当前方案的字幕编辑窗口，再切换方案。')
        key=next((k for k,v in self.catalog_names().items() if v==self.profile_name.get()),None)
        if key is None:key=next(k for k,v in profiles.NAMES.items() if v==self.profile_name.get())
        if key==self.cfg['editing_profile_id']:return
        try:
            self.persist_config(self.collect_config())
        except Exception:
            self.profile_name.set(profiles.NAMES[self.cfg['editing_profile_id']]);raise
        cfg=profiles.load(self.profile_state,key,self.cfg)
        from visual_controls import migrate_for_editor
        cfg=migrate_for_editor(cfg)
        self.cfg=cfg
        for name,var in self.vars.items():
            from shot_trim_controls import DEFAULTS as SHOT_DEFAULTS
            default={**SHOT_DEFAULTS,'voice_fit_max_trim_seconds':1.,'voice_fit_max_excess_seconds':3.}.get(name,'')
            var.set(str(cfg.get(name,default)))
        for name,var in self.processing_bools.items():var.set(bool(cfg.get(name,SHOT_DEFAULTS.get(name,False))))
        for prefix,var in self.processing_scope_vars.items():
            var.set(next((v for v,k in self._processing_scope_reverse.items() if k==cfg.get(prefix+'_scope')),'整条成片'))
        for name,(var,mapping) in self.timing_enum_vars.items():
            var.set(next((label for label,value in mapping.items() if value==cfg.get(name)),next(iter(mapping))))
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
        self.script_input.delete('1.0','end');self.script_input.insert('1.0',cfg.get('script_draft_text',''))
        self.clips=[];self.music=[];self.review_var.set(False);self.rights_var.set(False)
        self.refresh_media();self.refresh_music();self.load_profile_controls()
        self.persist_config(cfg)
        self.log('已切换到'+self.catalog_names()[key]+'；请扫描该方案素材。当前批次继续使用冻结配置。')

    def scanned_for_profile(self,result,key):
        if key!=self.cfg['editing_profile_id']:
            self.log('旧方案素材扫描完成；当前已切换方案，未替换当前素材列表。');return
        self.scanned(result)

    def review_grouped(self):
        if not profiles.ball_profile(self.cfg) or not profiles.grouped(self.cfg) or not self.clips:raise ValueError('请先切换牛肉丸方案并扫描素材')
        parent=getattr(self,'_qt_parent',None)
        if parent is not None:
            from grouped_review_qt import GroupedReviewDialog
            existing=getattr(self,'grouped_review_window',None)
            if isinstance(existing,GroupedReviewDialog) and not existing.closed:
                existing.show();existing.raise_();existing.activateWindow();return
            self.grouped_review_window=GroupedReviewDialog(self,self.clips,self.cfg['material_root'],parent)
            self.grouped_review_window.show();return
        from grouped_review_ui import GroupedReviewWindow
        self.grouped_review_window=GroupedReviewWindow(self,self.clips,self.cfg['material_root'])
