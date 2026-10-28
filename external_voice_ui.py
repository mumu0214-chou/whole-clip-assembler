"""External voice multi-selection; uses the existing library and profile settings."""
import tkinter as tk
from tkinter import ttk, messagebox


class ExternalVoiceSelector(tk.Toplevel):
    def __init__(self, owner, catalog, selected, apply):
        super().__init__(owner)
        self.title('外部配音 · 选择音色')
        self.geometry('540x620')
        self.transient(owner)
        self.apply_callback = apply
        self.all_voices = tk.BooleanVar(self, value=selected is None)
        self.checks = {}
        ttk.Label(self, text='从“文案ID+音色”文件名识别音色；每条组合只用本篇绑定的配音。', wraplength=500).pack(fill='x', padx=15, pady=10)
        ttk.Checkbutton(self, text='使用全部有效绑定（原有方式）', variable=self.all_voices, command=self.sync).pack(anchor='w', padx=15)
        panel = ttk.Frame(self)
        panel.pack(fill='both', expand=True, padx=15, pady=10)
        canvas = tk.Canvas(panel, highlightthickness=0)
        scrollbar = ttk.Scrollbar(panel, orient='vertical', command=canvas.yview)
        scrollbar.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=frame, anchor='nw')
        frame.bind('<Configure>', lambda _: canvas.configure(scrollregion=canvas.bbox('all')))
        self.checkbuttons = []
        for name in sorted(set(catalog) | set(selected or [])):
            counts = catalog.get(name, dict(valid=0, bound=0))
            var = tk.BooleanVar(self, value=selected is None or name in selected)
            self.checks[name] = var
            widget = ttk.Checkbutton(frame, text=f'{name}  ·  有效 {counts["valid"]} / 绑定 {counts["bound"]}', variable=var)
            widget.pack(anchor='w', pady=4)
            self.checkbuttons.append(widget)
        ttk.Label(self, text='取消“全部”后可多选。所选音色缺失时按现有失败策略处理，不借用其他音色。设置随当前剪辑方案保存，已冻结批次保留原声音。', wraplength=500).pack(fill='x', padx=15, pady=5)
        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=15, pady=12)
        ttk.Button(bar, text='全选', command=lambda: self.choose_all(True)).pack(side='left')
        ttk.Button(bar, text='清空', command=lambda: self.choose_all(False)).pack(side='left', padx=6)
        ttk.Button(bar, text='应用', command=self.apply_selection).pack(side='right')
        ttk.Button(bar, text='取消', command=self.destroy).pack(side='right', padx=6)
        self.sync()

    def sync(self):
        for widget in self.checkbuttons:
            widget.state(['disabled' if self.all_voices.get() else '!disabled'])

    def choose_all(self, value):
        self.all_voices.set(False)
        for var in self.checks.values():
            var.set(value)
        self.sync()

    def apply_selection(self):
        selected = None if self.all_voices.get() else [name for name, var in self.checks.items() if var.get()]
        if selected == []:
            messagebox.showerror('请选择音色', '至少勾选一个音色，或使用全部有效绑定。', parent=self)
            return
        self.apply_callback(selected)
        self.destroy()
