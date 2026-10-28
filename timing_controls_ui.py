"""One editing choice over compatible legacy timing parameters."""
import tkinter as tk
from tkinter import ttk


CUT_KEYS = ('processing_trim','processing_ab_duration_enabled','processing_micro_trim_enabled')
CUT_CHOICES = {
    '不额外随机裁短': (),
    '裁剪镜头（减掉所选秒数）': ('processing_micro_trim_enabled',),
    '头尾修剪（指定减掉多少）': ('processing_trim',),
    '取固定长度（保留3/4/5秒）': ('processing_ab_duration_enabled',),
    '自定义叠加（保留原设置）': None,
}


def selected_cut_mode(booleans):
    active = tuple(k for k in CUT_KEYS if booleans[k].get())
    return next(label for label, keys in CUT_CHOICES.items() if keys == active) if len(active)<=1 else list(CUT_CHOICES)[-1]


class TimingChooser:
    def __init__(self, app, parent, duration_details, visual_details, duration_boxes):
        self.app = app
        self.duration_details, self.visual_details = duration_details, visual_details
        self.boxes = duration_boxes
        self.busy = False
        self.expanded = False
        self.visual_expanded = False
        self.mode = tk.StringVar(app, selected_cut_mode(app.processing_bools))
        self.summary = tk.StringVar(app, '')
        app.timing_summary = self.summary
        ttk.Label(parent, text='1. 裁剪镜头：从片头或片尾减掉时间。').pack(anchor='w', padx=10, pady=(8,3))
        line = ttk.Frame(parent); line.pack(fill='x', padx=10, pady=4)
        choice = ttk.Combobox(line, textvariable=self.mode, values=tuple(CUT_CHOICES), width=37, state='readonly')
        choice.pack(side='left', padx=(0,8)); app.controls.append(choice)
        self.choice=choice
        choice.bind('<<ComboboxSelected>>', lambda _: self.choose(self.mode.get()))
        self.detail_button = ttk.Button(line, text='展开裁片详细参数', command=self.toggle_details)
        self.detail_button.pack(side='left')
        self.build_shot_controls(parent)
        ttk.Label(parent, text='2. 其他效果：独立选择；变速、转场也会改变总时长。').pack(anchor='w', padx=10, pady=(8,3))
        line = ttk.Frame(parent); line.pack(fill='x', padx=10, pady=4)
        for label, key in [('片段变速','processing_speed_enabled')]:
            check = ttk.Checkbutton(line, text=label, variable=app.processing_bools[key])
            check.pack(side='left', padx=(0,12)); app.controls.append(check)
        app.build_unified_visual_controls(parent)
        self.visual_button = ttk.Button(line, text='展开速度 / 转场 / 效果参数', command=self.toggle_visual)
        self.visual_button.pack(side='left')
        ttk.Label(parent, textvariable=self.summary, wraplength=1080).pack(fill='x', padx=10, pady=6)
        self.voice_params=ttk.LabelFrame(duration_details,text='配音选片搜索余量（高级）')
        line=ttk.Frame(self.voice_params);line.pack(fill='x',padx=8,pady=6)
        # Keep the old field for saved profile compatibility; new plans use the
        # visible shot choices/range as their actual per-shot limit.
        app.cfg.setdefault('voice_fit_max_trim_seconds',1.);app.var('voice_fit_max_trim_seconds')
        for label,key,default in [('整条候选最多多出秒数','voice_fit_max_excess_seconds',3.)]:
            ttk.Label(line,text=label).pack(side='left',padx=5)
            app.cfg.setdefault(key,default)
            app._entry(line,key,6).pack(side='left',padx=5)
        ttk.Label(self.voice_params,text='限制选片候选可比配音长多少；实际裁量仍受上面的镜头秒数、最短镜头和安全裁切许可限制。',wraplength=1000).pack(anchor='w',padx=8,pady=6)
        for key in CUT_KEYS+('processing_enabled','processing_speed_enabled','processing_transition','processing_fx'):
            app.processing_bools[key].trace_add('write', self.sync)
        self.sync()

    def build_shot_controls(self,parent):
        from shot_trim_controls import DEFAULTS
        app=self.app
        for key,value in DEFAULTS.items():app.cfg.setdefault(key,value)
        for key in ('shot_trim_voice_enabled','processing_min_kept_enabled'):
            app.processing_bools[key]=tk.BooleanVar(app,bool(app.cfg[key]))
        panel=ttk.Frame(parent);panel.pack(fill='x',padx=10,pady=4)
        self.shot_enable=ttk.Checkbutton(panel,text='启用裁剪镜头',variable=app.processing_bools['processing_micro_trim_enabled'])
        self.shot_enable.pack(anchor='w')
        line=ttk.Frame(panel);line.pack(fill='x',pady=4)
        mode=tk.StringVar(app,'候选秒数' if app.cfg['shot_trim_amount_mode']=='choices' else '随机区间')
        app.timing_enum_vars['shot_trim_amount_mode']=(mode,{'候选秒数':'choices','随机区间':'range'})
        ttk.Combobox(line,textvariable=mode,values=('候选秒数','随机区间'),width=12,state='readonly').pack(side='left',padx=(0,8))
        self.amount_checks={};self.check_busy=False
        csv=app.var('processing_micro_trim_values')
        for seconds in (.25,.5,1.,1.5,2.):
            var=tk.BooleanVar(app,False);self.amount_checks[seconds]=var
            ttk.Checkbutton(line,text=f'{seconds:g}秒',variable=var).pack(side='left',padx=5)
            var.trace_add('write',self.write_choices)
        csv.trace_add('write',self.read_choices);self.read_choices()
        self.choice_line=line
        self.range_line=ttk.Frame(panel);self.range_line.pack(fill='x',pady=4)
        ttk.Label(self.range_line,text='随机减掉').pack(side='left')
        for label,key in [('', 'shot_trim_range_min'),('～','shot_trim_range_max')]:
            ttk.Label(self.range_line,text=label).pack(side='left',padx=3)
            app._entry(self.range_line,key,6).pack(side='left')
        ttk.Label(self.range_line,text='秒（按原片时间计）').pack(side='left',padx=5)
        def amount_state(*_):
            for c in self.choice_line.winfo_children()[1:]:c.configure(state='normal' if mode.get()=='候选秒数' else 'disabled')
            for c in self.range_line.winfo_children():
                if isinstance(c,ttk.Entry):c.configure(state='normal' if mode.get()=='随机区间' else 'disabled')
        mode.trace_add('write',amount_state);amount_state()
        line=ttk.Frame(panel);line.pack(fill='x',pady=4)
        side,mapping=app.timing_enum_vars['processing_micro_trim_mode']
        ttk.Label(line,text='裁剪位置').pack(side='left',padx=(0,8))
        ttk.Combobox(line,textvariable=side,values=tuple(mapping),state='readonly',width=20).pack(side='left')
        for label,key in [('保护首镜片头','processing_micro_trim_protect_first'),('保护最后片尾','processing_micro_trim_protect_last')]:
            ttk.Checkbutton(line,text=label,variable=app.processing_bools[key]).pack(side='left',padx=8)
        line=ttk.Frame(panel);line.pack(fill='x',pady=4)
        ttk.Checkbutton(line,text='限制镜头最短不少于',variable=app.processing_bools['processing_min_kept_enabled']).pack(side='left')
        self.minimum_entry=app._entry(line,'processing_min_kept_seconds',6);self.minimum_entry.pack(side='left',padx=5)
        ttk.Label(line,text='秒；取消勾选则关闭这个限制').pack(side='left')
        def min_state(*_):self.minimum_entry.configure(state='normal' if app.processing_bools['processing_min_kept_enabled'].get() else 'disabled')
        app.processing_bools['processing_min_kept_enabled'].trace_add('write',min_state);min_state()
        ttk.Label(panel,text='有配音匹配：先检查组合能否裁到配音时长，再按实际差额裁短。勾选秒数的最大值（或区间上限）是每个镜头最多可裁多少；实际裁量可更小，精确到帧。随机决定镜头和片头/片尾。\n无配音匹配：直接随机减掉所选秒数/区间。牛肉丸只有已允许安全裁切的镜头可裁；关闭最短限制也保留此规则。',wraplength=1000).pack(anchor='w',pady=5)

    def read_choices(self,*_):
        if self.check_busy:return
        try:values={float(x) for x in self.app.var('processing_micro_trim_values').get().replace('，',',').split(',') if x.strip()}
        except ValueError:values=set()
        self.check_busy=True
        try:
            for seconds,var in self.amount_checks.items():var.set(seconds in values)
        finally:self.check_busy=False

    def write_choices(self,*_):
        if self.check_busy:return
        self.check_busy=True
        try:self.app.var('processing_micro_trim_values').set(','.join(f'{seconds:g}' for seconds,var in self.amount_checks.items() if var.get()))
        finally:self.check_busy=False

    def choose(self, label):
        keys = CUT_CHOICES[label]
        if keys is not None:
            self.busy = True
            try:
                for key in CUT_KEYS:
                    self.app.processing_bools[key].set(key in keys)
            finally:
                self.busy = False
        self.sync()

    def sync(self, *_):
        if self.busy:
            return
        self.mode.set(selected_cut_mode(self.app.processing_bools))
        voice_var=getattr(self.app,'match_voice_var',None)
        voice_active=bool(voice_var and voice_var.get())
        if voice_active:self.mode.set('按配音实际差额裁短')
        self.shot_enable.configure(variable=self.app.processing_bools['shot_trim_voice_enabled' if voice_active else 'processing_micro_trim_enabled'])
        self.choice.configure(state='disabled' if voice_active else 'readonly')
        enabled = self.app.processing_bools['processing_enabled'].get()
        extras = [label for label,key in [('变速','processing_speed_enabled')]
                  if self.app.processing_bools[key].get()]
        text = ('当前：'+self.mode.get()+'；'+('、'.join(extras) if extras else '无额外画面变速'))
        if voice_active:
            text='配音匹配：按可裁容量选片，再按实际差额裁短；牛肉与牛肉丸各用各自选片规则。'
            text+=(' SK未允许安全裁切的镜头保持完整。' if self.app.cfg.get('material_format')=='sk_diverse'
                   else ' 牛肉丸未允许安全裁切的镜头保持完整。')
            if not enabled:text+=' 裁片/变速开关关闭；配音差额匹配及独立转场/特效选择仍生效。'
        elif not enabled:
            text = '裁片/变速开关关闭：对应单项暂不执行。'+text
        elif self.mode.get()==list(CUT_CHOICES)[-1]:
            text += '。原有多项裁片会叠加；选择上面的一种方式可取消重复裁片。'
        if not voice_active:text += ' 开启第1页的“按配音时长选片并裁差额”，可按实际差额自动裁短。'
        if not voice_active and self.app.cfg.get('material_format')=='sk_diverse':
            text+=' SK头尾修剪、固定时长和微裁均需先在素材清单允许安全裁切。'
        self.summary.set(text)
        for key, box in self.boxes.items():
            box.pack_forget()
            if not voice_active and (self.mode.get()==list(CUT_CHOICES)[-1] or self.app.processing_bools[key].get()):
                box.pack(fill='x', pady=5)
        self.voice_params.pack_forget()
        if voice_active:self.voice_params.pack(fill='x',pady=5)

    def toggle_details(self):
        self.expanded = not self.expanded
        if self.expanded:
            self.duration_details.pack(fill='x', padx=12, pady=5, before=self.app.timing_footer)
        else:
            self.duration_details.pack_forget()
        self.detail_button.configure(text='收起裁片详细参数' if self.expanded else '展开裁片详细参数')

    def toggle_visual(self):
        self.visual_expanded = not self.visual_expanded
        if self.visual_expanded:
            self.visual_details.pack(fill='x', padx=12, pady=5, before=self.app.timing_footer)
        else:
            self.visual_details.pack_forget()
        self.visual_button.configure(text='收起速度 / 转场 / 效果参数' if self.visual_expanded else '展开速度 / 转场 / 效果参数')
