"""Shared desktop design tokens, navigation and disclosure controls.

Visual direction: a Chinese batch-editing workbench. Quiet neutral surfaces,
teal actions and a visible material -> voice -> plan -> video workflow.
"""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk

REVISION='workbench_ui_v1'
COLORS=dict(canvas='#F4F6F8',surface='#FFFFFF',ink='#1E293B',secondary='#64748B',
            divider='#E2E8F0',accent='#0F766E',accent_hover='#115E59',selected='#DDF1ED',
            field='#F8FAFC',warning='#A16207',error='#B91C1C',success='#15803D')
FONT='Microsoft YaHei UI'

def apply_theme(root):
    vendor=Path(__file__).parent/'vendor_ui'
    if str(vendor) not in sys.path:sys.path.insert(0,str(vendor))
    from ttkbootstrap import Style
    # Production owns one root; isolated UI tests may use multiple Tcl interpreters.
    previous=tk._default_root
    tk._default_root=root
    try:
        if Style.instance is not None and Style.instance.tk is not root.tk:Style.instance=None
        style=Style(theme='flatly')
        root.ui_style=style
        _configure_theme(root,style)
        return style
    finally:tk._default_root=previous

def _configure_theme(root,style):
    for widget in ('Text','Listbox','Scale'):root.option_add('*'+widget+'.Font',(FONT,10))
    root.option_add('*Text.background',COLORS['surface'])
    root.option_add('*Text.foreground',COLORS['ink'])
    root.option_add('*Text.insertBackground',COLORS['accent'])
    style.configure('.',font=(FONT,10),foreground=COLORS['ink'])
    for name in ('TFrame','TLabel','TCheckbutton','TRadiobutton','TLabelframe'):
        style.configure(name,background=COLORS['canvas'])
    style.configure('TLabelframe',borderwidth=1,relief='flat',padding=12)
    style.configure('TLabelframe.Label',background=COLORS['canvas'],font=(FONT,11,'bold'),foreground=COLORS['ink'])
    style.configure('TButton',padding=(12,8),font=(FONT,10),background=COLORS['surface'],foreground=COLORS['ink'],bordercolor=COLORS['divider'])
    style.configure('Accent.TButton',padding=(14,9),font=(FONT,10,'bold'),background=COLORS['accent'],foreground='white',bordercolor=COLORS['accent'])
    style.map('Accent.TButton',background=[('disabled','#B8C8C5'),('pressed',COLORS['accent_hover']),('active',COLORS['accent_hover'])],foreground=[('disabled','#FFFFFF')])
    style.configure('Nav.TRadiobutton',padding=(12,11),font=(FONT,10),background=COLORS['canvas'],foreground=COLORS['secondary'],indicatorcolor=COLORS['canvas'])
    style.layout('Nav.TRadiobutton',[('Radiobutton.padding',dict(sticky='nswe',children=[('Radiobutton.label',dict(sticky='nswe'))]))])
    style.map('Nav.TRadiobutton',background=[('selected',COLORS['selected']),('active','#EAF0F3')],foreground=[('selected',COLORS['accent']),('active',COLORS['ink'])])
    style.configure('PageTitle.TLabel',font=(FONT,22,'bold'),foreground=COLORS['ink'])
    style.configure('Subtitle.TLabel',font=(FONT,9),foreground=COLORS['secondary'])
    style.configure('Brand.TLabel',font=(FONT,15,'bold'),foreground=COLORS['accent'])
    style.configure('Section.TLabel',font=(FONT,12,'bold'),foreground=COLORS['ink'])
    style.configure('Disclosure.TButton',anchor='w',font=(FONT,10,'bold'),padding=(12,10),background=COLORS['surface'])
    style.configure('Treeview',rowheight=34,font=(FONT,10),background=COLORS['surface'],fieldbackground=COLORS['surface'],borderwidth=0)
    style.configure('Treeview.Heading',font=(FONT,10,'bold'),padding=(8,10),background='#EAF0F3',foreground=COLORS['secondary'])
    style.map('Treeview',background=[('selected',COLORS['selected'])],foreground=[('selected',COLORS['ink'])])
    style.configure('TEntry',padding=(8,7),fieldbackground=COLORS['field'])
    style.configure('TCombobox',padding=(8,6),fieldbackground=COLORS['field'])
    style.configure('Horizontal.TProgressbar',thickness=4,borderwidth=0,troughcolor=COLORS['divider'],background=COLORS['accent'])
    style.configure('Workbench.TNotebook',background=COLORS['canvas'],borderwidth=0)
    style.layout('Workbench.TNotebook.Tab',[])
    root.configure(background=COLORS['canvas'])
    return style

def release_theme(root):
    from ttkbootstrap import Style
    if Style.instance is getattr(root,'ui_style',None):
        Style.instance=None
        from ttkbootstrap.utils.fonts import Fonts
        Fonts.reset()

def progress_visible(widget,visible,before=None):
    if not hasattr(widget,'pack'):return
    if visible:
        options=dict(fill='x',padx=5,pady=4)
        if before is not None:options['before']=before
        widget.pack(**options)
    else:widget.pack_forget()

class Disclosure(ttk.Frame):
    """Native button disclosure; hiding never destroys variables or controls."""
    def __init__(self,parent,title,expanded=False):
        super().__init__(parent)
        self.title=title;self.expanded=False
        self.toggle_button=ttk.Button(self,style='Disclosure.TButton',command=self.toggle)
        self.toggle_button.pack(fill='x')
        self.body=ttk.Frame(self,padding=(12,8,12,12))
        self.set_expanded(expanded)
    def set_expanded(self,expanded):
        self.expanded=bool(expanded)
        self.toggle_button.configure(text=('▾  ' if self.expanded else '▸  ')+self.title)
        if self.expanded:self.body.pack(fill='x')
        else:self.body.pack_forget()
    def toggle(self):self.set_expanded(not self.expanded)

def create_shell(app):
    stage=ttk.Frame(app);stage.pack(fill='both',expand=True)
    sidebar=ttk.Frame(stage,width=184,padding=(16,24,12,16));sidebar.pack(side='left',fill='y');sidebar.pack_propagate(False)
    ttk.Label(sidebar,text='整段组合器',style='Brand.TLabel').pack(anchor='w')
    ttk.Label(sidebar,text='批量剪辑工作台',style='Subtitle.TLabel').pack(anchor='w',pady=(4,24))
    app.nav_var=tk.StringVar(value='main')
    nav=ttk.Frame(sidebar);nav.pack(fill='x')
    ttk.Label(sidebar,text='原素材保留\n方案与生成独立运行',style='Subtitle.TLabel',justify='left').pack(side='bottom',anchor='w',pady=(16,0))
    right=ttk.Frame(stage,padding=(16,16,20,12));right.pack(side='left',fill='both',expand=True)
    app.page_title_var=tk.StringVar(value='组合工作台')
    app.page_note_var=tk.StringVar(value='准备下一批组合，查看每一条镜头方案。')
    header=ttk.Frame(right);header.pack(fill='x',pady=(4,16))
    ttk.Label(header,textvariable=app.page_title_var,style='PageTitle.TLabel').pack(anchor='w')
    ttk.Label(header,textvariable=app.page_note_var,style='Subtitle.TLabel').pack(anchor='w',pady=(5,0))
    app.structure_hint_var=tk.StringVar(value='')
    app.main_footer=ttk.Frame(right);app.main_footer.pack(side='bottom',fill='x',pady=(12,0))
    app.tabs=ttk.Notebook(right,style='Workbench.TNotebook');app.tabs.pack(fill='both',expand=True)
    app.main_tab,app.media_tab,app.music_tab,app.text_tab,app.processing_tab,app.timing_tab,app.env_tab=[ttk.Frame(app.tabs) for _ in range(7)]
    app.render_tab=ttk.Frame(app.tabs);app.voice_tab=ttk.Frame(app.tabs)
    from runtime_log_ui import RuntimeLogPanel
    app.log_tab=RuntimeLogPanel(app.tabs,app)
    rows=[('main','组合工作台',app.main_tab,'选定商品和配置，准备下一批完整组合。'),
          ('render','视频生成',app.render_tab,'导入已冻结的批次，查看生成进度和输出。'),
          ('voice','文案与配音',app.voice_tab,'管理当前品类文案、外部声音和字幕配置。'),
          ('media','素材清单',app.media_tab,'查看、复核和启停当前素材。'),
          ('music','音乐区间',app.music_tab,'确认可用音乐和使用区间。'),
          ('text','顶部文案',app.text_tab,'设置顶部文字及其出现时机。'),
          ('processing','画面处理',app.processing_tab,'按需设置镜像、调色和画面效果。'),
          ('timing','时长与裁切',app.timing_tab,'设置镜头裁量、变速和转场。'),
          ('env','环境与参数',app.env_tab,'管理目录、组合参数和运行环境。'),
          ('logs','运行日志',app.log_tab,'查看逐条阶段、错误和诊断记录。')]
    app.navigation={key:dict(title=title,frame=frame,note=note) for key,title,frame,note in rows}
    for key,title,frame,note in rows:
        app.tabs.add(frame,text=title)
        ttk.Radiobutton(nav,text=title,variable=app.nav_var,value=key,style='Nav.TRadiobutton',command=lambda page=frame:app.tabs.select(page)).pack(fill='x',pady=2)
    def selected(_=None):
        current=app.tabs.select()
        for key,row in app.navigation.items():
            if str(row['frame'])==current:
                app.nav_var.set(key);app.page_title_var.set(row['title']);app.page_note_var.set(row['note']);break
    app.tabs.bind('<<NotebookTabChanged>>',selected,add='+')
    from scrollable_ui import ScrollablePage
    app.main_scroll=ScrollablePage(app.main_tab);app.main_scroll.pack(fill='both',expand=True);app.main_content=app.main_scroll.body
    app.voice_scroll=ScrollablePage(app.voice_tab);app.voice_scroll.pack(fill='both',expand=True);app.voice_content=app.voice_scroll.body
    app.settings_scroll=ScrollablePage(app.env_tab);app.settings_scroll.pack(fill='both',expand=True);app.settings_content=app.settings_scroll.body
    app.render_content=ttk.Frame(app.render_tab);app.render_content.pack(fill='both',expand=True)
    # Product-specific flow trail doubles as keyboard-accessible navigation.
    trail=ttk.Frame(app.main_content);trail.pack(fill='x',pady=(0,12))
    for label,page in [('01 素材复核',app.media_tab),('02 文案与声音',app.voice_tab),('03 组合方案',app.main_tab),('04 生成视频',app.render_tab)]:
        ttk.Button(trail,text=label,command=lambda p=page:app.tabs.select(p)).pack(side='left',padx=(0,8))
    common=ttk.Frame(app.main_content,padding=(12,8));common.pack(fill='x',pady=(0,8))
    ttk.Label(common,text='本批数量',style='Section.TLabel').pack(side='left')
    ttk.Entry(common,textvariable=app.var('count'),width=7).pack(side='left',padx=(12,20))
    app.opening_c_shortcut_label=ttk.Label(common,text='C/开头首镜至少')
    app.opening_c_shortcut_label.pack(side='left',padx=(0,3))
    app.opening_c_shortcut=app._entry(common,'opening_c_min_per_batch',6);app.opening_c_shortcut.pack(side='left',padx=(0,8))
    app.voice_config_shortcut=ttk.Button(common,text='文案 / 配音配置',command=lambda:app.tabs.select(app.voice_tab))
    app.voice_config_shortcut.pack(side='left',padx=(0,8))
    app.subtitle_style_shortcut=ttk.Button(common,text='字幕字体 / 样式',command=lambda:app.open_subtitle_editor())
    app.subtitle_style_shortcut.pack(side='left',padx=(0,8))
    def reveal(section):
        if hasattr(section,'set_expanded'):section.set_expanded(True)
        app.tabs.select(app.main_tab)
        window=getattr(app,'_qt_parent',None)
        if window:
            window.navigate('main')
            widget=window.presenter.nodes.get(str(section))
            if widget:window.pages['main'].ensureWidgetVisible(widget)
    ttk.Button(common,text='全部组合参数',command=lambda:reveal(app.parameter_section)).pack(side='left')
    shortcuts=ttk.Frame(app.main_content);shortcuts.pack(fill='x',pady=(0,8))
    for label,name in [('首镜 / 时长 / 去重','parameter_section'),('视频模式比例 / 顶部文案','mode_section'),('视频画幅 / 比例','canvas_panel')]:
        ttk.Button(shortcuts,text=label,command=lambda key=name:reveal(getattr(app,key))).pack(side='left',padx=(0,8))
    app.paths_section=Disclosure(app.settings_content,'素材、音乐和输出目录',expanded=True);app.paths_section.pack(fill='x',pady=(0,12))
    app.parameter_section=Disclosure(app.main_content,'首镜、组合时长与去重',expanded=True);app.parameter_section.pack(fill='x',pady=(0,12))
    app.mode_section=Disclosure(app.main_content,'视频模式比例与顶部文案时机',expanded=True);app.mode_section.pack(fill='x',pady=(0,12))
    canvas=ttk.LabelFrame(app.main_content,text='视频画幅 / 比例');canvas.pack(fill='x',pady=(0,12))
    app.canvas_panel=canvas
    import math
    ratios={'9:16 竖屏':(1080,1920),'16:9 横屏':(1920,1080),'1:1 方形':(1080,1080),'4:5 竖屏':(1080,1350)}
    app.aspect_ratio_var=tk.StringVar(value='自定义')
    ratio=ttk.Combobox(canvas,textvariable=app.aspect_ratio_var,values=tuple(ratios)+('自定义',),state='readonly',width=16)
    ttk.Label(canvas,text='画幅比例').pack(side='left',padx=(8,3));ratio.pack(side='left',padx=3);app.controls.append(ratio)
    def read_ratio(*_):
        try:
            width,height=int(app.var('width').get()),int(app.var('height').get());divisor=math.gcd(width,height)
            found=next((name for name,(w,h) in ratios.items() if width>0 and height>0 and (width//divisor,height//divisor)==(w//math.gcd(w,h),h//math.gcd(w,h))),'自定义')
        except (ValueError,ZeroDivisionError):found='自定义'
        app.aspect_ratio_var.set(found)
    def choose_ratio(_=None):
        value=ratios.get(app.aspect_ratio_var.get())
        if value:
            width,height=value;app.var('width').set(str(width));app.var('height').set(str(height))
    ratio.bind('<<ComboboxSelected>>',choose_ratio)
    for key in ('width','height'):app.var(key).trace_add('write',read_ratio)
    read_ratio()
    for key,label in [('width','宽度'),('height','高度'),('fps','帧率')]:
        ttk.Label(canvas,text=label).pack(side='left',padx=(8,3));app._entry(canvas,key,8).pack(side='left',padx=3)
    ttk.Label(canvas,text='像素；不同素材保持比例居中。只影响新方案。').pack(side='left',padx=8)

def attach_empty_states(app):
    app.plan_empty=ttk.Label(app.planning_area,text='尚未生成组合方案\n先扫描并复核素材，再点击“生成组合方案”。',style='Subtitle.TLabel',justify='center',anchor='center',padding=24)
    app.plan_empty.pack(fill='x',before=app.plan_detail_bar)
    app.render_empty=ttk.Label(app.video_area,text='尚未载入视频批次\n导入过往批次，或在工作台准备新方案后开始生成。',style='Subtitle.TLabel',justify='center',anchor='center',padding=24)
    app.render_empty.pack(fill='x')
    sync_empty_states(app)

def sync_empty_states(app):
    for name,batch in [('plan_empty',getattr(app,'batch',None)),('render_empty',getattr(app,'render_batch_state',None))]:
        if name=='render_empty' and getattr(app,'video_queue',None) and app.video_queue.pending():batch=True
        label=getattr(app,name,None)
        if label is None:continue
        tree=getattr(app,'plan_tree' if name=='plan_empty' else 'render_tree',None)
        if batch:
            label.pack_forget()
            if tree and not tree.master.winfo_manager():tree.master.pack(fill='both',expand=True,padx=8,pady=5)
        else:
            if tree:tree.master.pack_forget()
            if not label.winfo_manager():label.pack(fill='x')
