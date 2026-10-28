"""Single visible visual-effect chooser, shared by Tk and the Qt presenter."""
import tkinter as tk
from tkinter import ttk
from visual_controls import TRANSITIONS, EFFECTS, label, summary


class VisualControls:
    def build_unified_visual_controls(self, parent):
        panel=ttk.LabelFrame(parent,text='转场 / 画面特效 · 唯一设置入口')
        panel.pack(fill='x',padx=10,pady=6,before=parent.winfo_children()[0])
        self.visual_mode_controls={}
        for title,key,mapping in [('转场','transition_mode',TRANSITIONS),('画面特效','picture_effect_mode',EFFECTS)]:
            line=ttk.Frame(panel);line.pack(fill='x',padx=8,pady=4)
            ttk.Label(line,text=title,width=12).pack(side='left')
            var=tk.StringVar(value=label(mapping,self.cfg[key]))
            self.timing_enum_vars[key]=(var,mapping)
            control=ttk.Combobox(line,textvariable=var,values=tuple(mapping),state='readonly',width=26)
            control.pack(side='left');self.controls.append(control);self.visual_mode_controls[key]=control
            var.trace_add('write',lambda *_:self.sync_visual_controls())
        ttk.Label(panel,text='所有品类在此设置，各品类分别保存。直接切换=无转场；原画=关闭调色、动态缩放、暗角和柔光。\n转场/特效独立于裁片及输出处理开关；选择自定义后展开下方详细参数。牛肉丸仍保护开头与短镜头。',wraplength=1050).pack(anchor='w',padx=8,pady=4)
        self.sync_visual_controls()

    def sync_visual_controls(self):
        if 'transition_mode' not in getattr(self,'timing_enum_vars',{}):return
        cfg={key:mapping[var.get()] for key,(var,mapping) in self.timing_enum_vars.items()
             if key in ('transition_mode','picture_effect_mode')}
        for key,name in [('transition_mode','custom_transition_box'),('picture_effect_mode','custom_effect_box')]:
            box=getattr(self,name,None)
            if box is not None:
                box.pack_forget()
                if cfg[key]=='custom':box.pack(fill='x',pady=6)
        if hasattr(self,'visual_summary_var'):self.visual_summary_var.set(summary(cfg))

    def open_visual_settings(self):
        self.tabs.select(self.timing_tab)
        if not self.timing_chooser.visual_expanded:self.timing_chooser.toggle_visual()
        self.sync_visual_controls()
