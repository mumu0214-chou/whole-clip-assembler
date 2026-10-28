"""Tk bottom-caption editor backed by the production Remotion component."""
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, simpledialog, messagebox
from pathlib import Path
import queue
import threading
from copy import deepcopy
from PIL import Image, ImageTk
from subtitle_styles import DEFAULT, RANGES, BUBBLES, FANCY, normalize, from_config, preset, preview_text, Presets, atomic
from subtitle_fonts import scan, import_font, freeze
from subtitle_preview import PreviewRenderer
from subtitle_settings import SMART, ANIMATION, ENTRY, EXIT, LOOP

class SubtitleEditor(tk.Toplevel):
    def __init__(self,app):
        super().__init__(app);self.app=app;self.title('智能字幕编辑器 · 逐句动画');self.geometry(f'{min(1190,self.winfo_screenwidth()-40)}x{min(860,self.winfo_screenheight()-80)}')
        self.minsize(960,640);self.vars={};self.seq=0;self.current=0;self.timer=None;self.loading=True
        self.jobs=queue.PriorityQueue();self.results=queue.Queue();self.closed=False;self.images={}
        self.renderer=PreviewRenderer(app.collect_config())
        from subtitle_preview import preview_dimensions
        self.preview_w,self.preview_h=preview_dimensions(self.renderer.cfg)
        self.presets=None
        try:self.presets=Presets(app.data_dir/'subtitle_presets.json')
        except Exception as exc:messagebox.showwarning('预设恢复',str(exc),parent=self)
        style=from_config(app.collect_config())
        if not app.cfg.get('subtitle_style') and self.presets and self.presets.data['default']:
            style=self.presets.data['items'].get(self.presets.data['default'],style)
        self.fonts=scan()
        self.recent_path=app.data_dir/'subtitle_recent_fonts.json'
        try:
            import json
            self.recent=json.loads(self.recent_path.read_text(encoding='utf-8'))
        except (OSError,ValueError):self.recent=[]
        for key,value in style.items():
            if isinstance(value,dict):continue
            self.vars[key]=tk.BooleanVar(value=value) if isinstance(value,bool) else tk.StringVar(value=str(value))
        self.smart_vars={k:(tk.BooleanVar(value=v) if isinstance(v,bool) else tk.StringVar(value=str(v))) for k,v in style['smart'].items()}
        self.animation_vars={stage:{k:tk.StringVar(value=str(v)) for k,v in style['animation'][stage].items()} for stage in ('entry','exit','loop')}
        self.frames=[];self.play_index=0;self.playing=False;self.play_task=None;self.cues=[]
        body=ttk.Frame(self);body.pack(fill='both',expand=True,padx=12,pady=10)
        left=ttk.Frame(body);left.pack(side='left',fill='both',expand=True)
        quick=ttk.Frame(left);quick.pack(fill='x',pady=(0,5))
        ttk.Button(quick,text='青绿白底字幕条（样片同款）',
                   command=lambda:self.set_style(preset(next(i for i,(name,_) in enumerate(FANCY) if name=='青绿白底字幕条'),self.values()))).pack(side='left')
        ttk.Button(quick,text='黄白关键词黑描边（黄色放大）',
                   command=lambda:self.set_style(preset(next(i for i,(name,_) in enumerate(FANCY) if name=='黄白关键词黑描边'),self.values()))).pack(side='left',padx=8)
        right=ttk.Frame(body,width=300);right.pack(side='right',fill='y',padx=(12,0));right.pack_propagate(False)
        book=ttk.Notebook(left);book.pack(fill='both',expand=True)
        self.book=book
        basic=self.page(book,'基础');smart_page=self.page(book,'智能排版');bubble=self.page(book,'气泡');fancy=self.page(book,'花字');animations=self.page(book,'动画')
        self.smart_page(smart_page);self.animation_page(animations)
        fontrow=ttk.Frame(basic);fontrow.pack(fill='x',pady=5)
        ttk.Label(fontrow,text='字体').pack(side='left');ttk.Label(fontrow,textvariable=self.vars['font_family']).pack(side='left',padx=8)
        ttk.Button(fontrow,text='搜索 / 预览字体',command=self.font_picker).pack(side='left')
        ttk.Button(fontrow,text='导入 TTF / OTF',command=self.import_font).pack(side='left',padx=6)
        ttk.Label(basic,text='尺寸以 1080×1920 设计像素为基准；导出按画面高度缩放。').pack(anchor='w')
        self.number(basic,'字重（加粗时生效）','font_weight')
        self.number(basic,'字号','font_size',slider=True)
        row=ttk.Frame(basic);row.pack(fill='x',pady=4)
        for key,label in [('bold','加粗 B'),('italic','斜体 I'),('underline','下划线 U')]:ttk.Checkbutton(row,text=label,variable=self.vars[key]).pack(side='left',padx=8)
        self.color(basic,'文字颜色','color');self.number(basic,'文字不透明度','opacity')
        self.number(basic,'字间距','letter_spacing');self.number(basic,'行距倍数','line_spacing')
        self.choice(basic,'对齐','align',('left','center','right'))
        self.check(basic,'开启描边','stroke_enabled');self.color(basic,'描边颜色','stroke_color');self.number(basic,'描边粗细','stroke_width')
        self.check(basic,'开启阴影','shadow_enabled');self.color(basic,'阴影颜色','shadow_color')
        for key,label in [('shadow_opacity','阴影不透明度'),('shadow_x','阴影水平偏移'),('shadow_y','阴影垂直偏移'),('shadow_blur','阴影模糊')]:self.number(basic,label,key)
        ttk.Separator(basic).pack(fill='x',pady=8)
        for key,label in [('x','横向中心位置 0～1'),('y','纵向位置 0～1'),('max_width','最大宽度比例'),('safe_horizontal','水平安全边距比例'),('safe_bottom','底部安全边距比例'),('scale','整体缩放')]:self.number(basic,label,key)
        ttk.Label(basic,text='真实字体宽度排版；无法安全容纳时提示复核，不自动缩小文字。').pack(anchor='w')
        for name,values in BUBBLES.items():ttk.Button(bubble,text=name,command=lambda v=values:self.set_style(dict(self.values(),**v))).pack(fill='x',pady=3)
        self.color(bubble,'背景颜色','bubble_color')
        for key,label in [('bubble_opacity','背景不透明度'),('bubble_radius','圆角'),('padding_x','水平内边距'),('padding_y','垂直内边距'),('bubble_width','底框宽度（0=自动）'),('bubble_height','底框最小高度（0=自动）')]:self.number(bubble,label,key)
        ttk.Label(fancy,text='缩略图由实际字幕渲染器生成；点击应用后仍可编辑。').pack(anchor='w',pady=6)
        self.thumb_buttons={}
        grid=ttk.Frame(fancy);grid.pack(fill='both',expand=True)
        for i,(name,_) in enumerate(FANCY):
            b=ttk.Button(grid,text=name,compound='top',command=lambda n=i:self.set_style(preset(n,self.values())))
            b.grid(row=i//3,column=i%3,padx=4,pady=4,sticky='nsew');self.thumb_buttons[i]=b
        for i in range(3):grid.columnconfigure(i,weight=1)
        self.check(fancy,'渐变文字','gradient');self.color(fancy,'渐变终点色','gradient_color')
        keyword=ttk.Frame(fancy);keyword.pack(fill='x',padx=5,pady=4)
        ttk.Label(keyword,text='黄色放大关键词（逗号分隔）').pack(side='left')
        ttk.Entry(keyword,textvariable=self.vars['highlight_keywords']).pack(side='left',fill='x',expand=True,padx=5)
        ttk.Label(fancy,text='只放大字幕里实际出现的匹配词；用逗号分隔。词表随当前商品保存。').pack(anchor='w',padx=5)
        self.color(fancy,'关键词颜色','highlight_color')
        self.number(fancy,'关键词放大倍数','highlight_scale')
        import math
        cw,ch=self.renderer.cfg.get('width',1080),self.renderer.cfg.get('height',1920)
        divisor=math.gcd(cw,ch)
        ratio=f'{cw//divisor}:{ch//divisor}' if self.renderer.cfg.get('material_format')=='sk_diverse' else '9:16'
        ttk.Label(right,text=ratio+' 实际渲染预览',font=('',12,'bold')).pack(pady=5)
        self.images['placeholder']=ImageTk.PhotoImage(Image.new('RGB',(self.preview_w,self.preview_h),'#26313f'))
        self.preview=tk.Canvas(right,width=self.preview_w,height=self.preview_h,bg='#26313f',highlightthickness=0)
        self.preview.pack()
        self.preview_image=self.preview.create_image(0,0,anchor='nw',image=self.images['placeholder'])
        self.preview.bind('<ButtonPress-1>',self.position_start)
        self.preview.bind('<B1-Motion>',self.position_drag)
        self.preview.bind('<ButtonRelease-1>',self.position_finish)
        self.snap_centers=tk.BooleanVar(value=True)
        ttk.Checkbutton(right,text='拖动时识别并吸附横、纵中线',variable=self.snap_centers).pack(anchor='w')
        ttk.Label(right,text='在画面上拖动字幕框，松开后刷新预览；仅作用于新方案。',wraplength=285).pack()
        controls=ttk.Frame(right);controls.pack(fill='x',pady=4)
        for label,method in [('播放/暂停',self.toggle_play),('重播',self.replay),('上一句',lambda:self.jump(-1)),('下一句',lambda:self.jump(1))]:ttk.Button(controls,text=label,command=method,width=7).pack(side='left')
        self.clock=tk.StringVar(value='0.00 / 0.00 秒');ttk.Label(right,textvariable=self.clock).pack()
        self.seek=tk.DoubleVar(value=0);self.scrub=ttk.Scale(right,from_=0,to=1,variable=self.seek,command=self.seek_frame);self.scrub.pack(fill='x')
        ttk.Label(right,text='预览文字（仅预览，不触发配音）').pack(anchor='w',pady=(10,3))
        self.sample=tk.Text(right,height=4,wrap='word');self.sample.pack(fill='x')
        self.sample.insert('1.0',preview_text(style))
        self.sample_auto_text=preview_text(style)
        self.sample.bind('<KeyRelease>',lambda e:self.changed())
        self.status=tk.StringVar(value='正在准备动画预览…');ttk.Label(right,textvariable=self.status,wraplength=290).pack(fill='x',pady=8)
        ttk.Label(right,text='预览按阅读时间演示；正式口播使用真实配音对齐。',wraplength=285).pack(fill='x')
        footer=ttk.Frame(self);footer.pack(fill='x',padx=12,pady=4)
        ttk.Button(footer,text='恢复默认样式',command=lambda:self.set_style(DEFAULT)).pack(side='left')
        ttk.Label(footer,text='修改用于新任务；已有批次使用冻结样式。顶部文案独立。').pack(side='left',padx=12)
        ttk.Button(footer,text='应用并保存到新任务',command=self.apply).pack(side='right')
        presetbar=ttk.LabelFrame(self,text='我的字幕预设');presetbar.pack(fill='x',padx=12,pady=(0,12))
        self.preset_name=tk.StringVar();self.preset_box=ttk.Combobox(presetbar,textvariable=self.preset_name,width=22,state='readonly');self.preset_box.pack(side='left',padx=6,pady=8)
        self.preset_kind=tk.StringVar(value='完整方案');ttk.Combobox(presetbar,textvariable=self.preset_kind,values=['样式','动画','完整方案'],state='readonly',width=9).pack(side='left')
        for label,method in [('应用',self.load_preset),('保存为预设',self.save_preset),('重命名',self.rename_preset),('删除',self.delete_preset),('设为默认',self.default_preset)]:ttk.Button(presetbar,text=label,command=lambda m=method:self.guard(m)).pack(side='left',padx=3)
        self.refresh_presets()
        for var in self.vars.values():var.trace_add('write',lambda *_:self.changed())
        for var in self.smart_vars.values():var.trace_add('write',lambda *_:self.changed())
        for values in self.animation_vars.values():
            for var in values.values():var.trace_add('write',lambda *_:self.changed())
        self.loading=False;self.protocol('WM_DELETE_WINDOW',self.close)
        self.preview_thread=threading.Thread(target=self.worker,daemon=True,name='subtitle-preview');self.preview_thread.start()
        self.poll_id=self.after(100,self.poll);self.changed()
        self.thumb_id=self.after(1200,self.thumbnails)
    def guard(self,fn):
        try:fn()
        except Exception as exc:messagebox.showerror('字幕编辑器',str(exc),parent=self)
    def smart_page(self,parent):
        ttk.Label(parent,text='标点仅影响显示；配音原文、价格与规格不会被改写。').pack(anchor='w',pady=8)
        for key,label in [('enabled','启用智能语义断句'),('balanced','两行均衡排版'),('avoid_orphan','避免孤字与过短尾行'),('protect_specs','保护数字 / 价格 / 规格'),('protect_words','自动保护完整词语（中文分词）'),('pause_assist','参考真实语音停顿')]:
            ttk.Checkbutton(parent,text=label,variable=self.smart_vars[key]).pack(anchor='w',padx=8,pady=5)
        box=ttk.LabelFrame(parent,text='不拆词词库 · 独立于黄色放大关键词');box.pack(fill='x',padx=8,pady=8)
        ttk.Label(box,text='每行一个词，也可用逗号分隔。品牌、商品名、特殊术语填在这里；换行和字幕切换均保持完整。',wraplength=610).pack(anchor='w',padx=6,pady=4)
        self.terms_text=tk.Text(box,height=5,wrap='word',undo=True)
        self.terms_text.pack(fill='x',padx=6,pady=4)
        def sync_terms(*_):
            value=self.vars['protected_terms'].get()
            if self.terms_text.get('1.0','end-1c')!=value:
                self.terms_text.delete('1.0','end');self.terms_text.insert('1.0',value)
            self.terms_text.edit_modified(False)
        def edit_terms(_=None):
            if self.terms_text.edit_modified():
                self.terms_text.edit_modified(False)
                self.vars['protected_terms'].set(self.terms_text.get('1.0','end-1c'))
        sync_terms();self.terms_text.bind('<<Modified>>',edit_terms)
        self.vars['protected_terms'].trace_add('write',sync_terms)
        ttk.Label(box,text='内置保护：执行标准、牛肉丸等常用词，以及DBS44/005、SB/T 10379等编号。填好后点击“应用并保存到新任务”。',wraplength=610).pack(anchor='w',padx=6,pady=4)
        for key,label,choices in [('density','字幕密度',{'sparse':'较少','normal':'正常','dense':'较密'}),('punctuation','显示标点',{'simple':'简洁（保留？！小数与规格）','minimal':'极简','original':'保留原文'}),('max_lines','最多行数',{'1':'一行','2':'两行'})]:
            row=ttk.Frame(parent);row.pack(fill='x',padx=8,pady=6);ttk.Label(row,text=label).pack(side='left')
            var=tk.StringVar(value=choices[str(self.smart_vars[key].get())]);box=ttk.Combobox(row,textvariable=var,values=list(choices.values()),state='readonly',width=34);box.pack(side='right')
            box.bind('<<ComboboxSelected>>',lambda e,k=key,v=var,c=choices:self.smart_vars[k].set(next(x for x,y in c.items() if y==v.get())))
            self.smart_vars[key].trace_add('write',lambda *_,k=key,v=var,c=choices:v.set(c[str(self.smart_vars[k].get())]))
        ttk.Label(parent,text='正式配音使用离线声学对齐；对齐不可靠时阻止导出并提示复核。\n换字体会重新测量实际宽度；省略号与句末强边界不会被合并。',wraplength=650).pack(anchor='w',padx=8,pady=16)
    def animation_page(self,parent):
        ttk.Label(parent,text='逐句独立触发，短句自动压缩动画并保留稳定阅读时间。').pack(anchor='w',pady=8)
        book=ttk.Notebook(parent);book.pack(fill='both',expand=True)
        for stage,title,catalog in [('entry','入场',ENTRY),('exit','退场',EXIT),('loop','循环',LOOP)]:
            page=ttk.Frame(book);book.add(page,text=title);grid=ttk.Frame(page);grid.pack(fill='x',padx=6,pady=6)
            for i,(kind,label) in enumerate(catalog.items()):
                ttk.Button(grid,text=label,command=lambda a=stage,k=kind:self.animation_vars[a]['kind'].set(k)).grid(row=i//3,column=i%3,sticky='ew',padx=3,pady=4)
            for i in range(3):grid.columnconfigure(i,weight=1)
            current=tk.StringVar();ttk.Label(page,textvariable=current).pack(anchor='w',padx=8)
            rows={}
            for key,label,lo,hi,inc in [('duration','时长 / 循环周期（秒）',.01,8,.01),('delay','延迟（秒）',0,4,.01),('intensity','强度',0,2,.05),('distance','位移（设计像素）',0,160,1),('scale','缩放比例',.25,1.3,.01)]:
                row=ttk.Frame(page);ttk.Label(row,text=label,width=28).pack(side='left');ttk.Spinbox(row,from_=lo,to=hi,increment=inc,textvariable=self.animation_vars[stage][key],width=10).pack(side='right');rows[key]=row
            for key,label,choices in [('direction','方向',{'left':'左','right':'右','up':'上','down':'下'}),('easing','缓动',{'linear':'匀速','ease_in':'慢入','ease_out':'慢出','ease_in_out':'慢入慢出'})]:
                row=ttk.Frame(page);ttk.Label(row,text=label,width=28).pack(side='left');var=tk.StringVar(value=choices[self.animation_vars[stage][key].get()]);box=ttk.Combobox(row,values=list(choices.values()),textvariable=var,state='readonly',width=12);box.pack(side='right');rows[key]=row
                box.bind('<<ComboboxSelected>>',lambda e,a=stage,k=key,v=var,c=choices:self.animation_vars[a][k].set(next(x for x,y in c.items() if y==v.get())))
                self.animation_vars[stage][key].trace_add('write',lambda *_,a=stage,k=key,v=var,c=choices:v.set(c[self.animation_vars[a][k].get()]))
            def update(*_,a=stage,rs=rows,c=catalog,label=current):
                kind=self.animation_vars[a]['kind'].get();label.set('当前：'+c[kind])
                for row in rs.values():row.pack_forget()
                if kind=='none':return
                active=['duration','delay']
                if kind not in ('fade','cut','reveal','typewriter','wipe'):active.append('intensity')
                if kind.startswith('slide') or kind in ('bounce','float'):active.append('distance')
                if kind in ('pop','shrink','breath'):active.append('scale')
                if kind=='wipe':active.append('direction')
                if a!='loop' and kind not in ('cut','reveal','typewriter'):active.append('easing')
                for key in active:rs[key].pack(fill='x',padx=8,pady=4)
            self.animation_vars[stage]['kind'].trace_add('write',update);update()
    def show_frame(self):
        if not self.frames:return
        self.play_index=max(0,min(len(self.frames)-1,self.play_index));self.preview.itemconfigure(self.preview_image,image=self.frames[self.play_index])
        self.clock.set(f'{self.play_index/12:.2f} / {len(self.frames)/12:.2f} 秒');self.seek.set(self.play_index)

    def position_start(self,event):
        self.playing=False
        from subtitle_guides import box
        b=box(self.values(),getattr(self,'preview_w',225),getattr(self,'preview_h',400),self.position_cue())
        inside=abs(event.x-b['center_x'])<=b['width']/2 and abs(event.y-b['center_y'])<=b['height']/2
        self._drag_offset=(event.x-b['center_x'],event.y-b['center_y']) if inside else (0,0)
        self.position_drag(event)

    def position_cue(self):
        frame=round(self.play_index/12*30)
        return next((c for c in self.cues if c['start_frame']<=frame<c['end_frame']),None)

    def position_drag(self,event):
        from subtitle_guides import drag
        dx,dy=getattr(self,'_drag_offset',(0,0))
        pw,ph=getattr(self,'preview_w',225),getattr(self,'preview_h',400)
        pos=drag(self.values(),pw,ph,event.x-dx,event.y-dy,self.position_cue(),self.snap_centers.get())
        self._drag_position=(pos['x'],pos['y'])
        self.preview.delete('position-guide')
        cx=pos['center_x'];top=pos['center_y']-pos['height']/2
        self.preview.create_line(pw/2,0,pw/2,ph,fill='#49e4dc' if pos['vertical'] else '#87929f',dash=(4,3),tags='position-guide')
        self.preview.create_line(0,ph/2,pw,ph/2,fill='#49e4dc' if pos['horizontal'] else '#87929f',dash=(4,3),tags='position-guide')
        self.preview.create_rectangle(cx-pos['width']/2,top,cx+pos['width']/2,top+pos['height'],
                                      outline='#ffe133',width=2,tags='position-guide')
        label='横纵居中' if pos['vertical'] and pos['horizontal'] else '水平居中' if pos['vertical'] else '垂直居中' if pos['horizontal'] else '字幕位置'
        self.preview.create_text(cx,max(12,top-10),text=label,fill='#ffe133',tags='position-guide')

    def position_finish(self,event):
        if not hasattr(self,'_drag_position'):return
        x,y=self._drag_position
        self.preview.delete('position-guide')
        self.vars['vertical_anchor'].set('center')
        self.vars['x'].set(f'{x:.3f}');self.vars['y'].set(f'{y:.3f}')
        del self._drag_position
    def tick(self):
        self.play_task=None
        if self.closed:return
        if self.frames and self.playing:
            self.play_index=(self.play_index+1)%len(self.frames);self.show_frame()
        self.play_task=self.after(83,self.tick)
    def toggle_play(self):self.playing=not self.playing
    def replay(self):self.play_index=0;self.playing=True;self.show_frame()
    def seek_frame(self,value):
        self.play_index=round(float(value));self.show_frame()
    def jump(self,direction):
        seconds=self.play_index/12
        starts=[c['start_frame']/30 for c in self.cues]
        candidates=[t for t in starts if (t>seconds+.1 if direction>0 else t<seconds-.1)]
        if candidates:self.play_index=round((min(candidates) if direction>0 else max(candidates))*12);self.show_frame()
    def page(self,book,title):
        page=ttk.Frame(book);book.add(page,text=title);canvas=tk.Canvas(page,highlightthickness=0)
        scroll=ttk.Scrollbar(page,orient='vertical',command=canvas.yview);scroll.pack(side='right',fill='y');canvas.pack(fill='both',expand=True);canvas.configure(yscrollcommand=scroll.set)
        content=ttk.Frame(canvas);win=canvas.create_window((0,0),window=content,anchor='nw')
        content.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')))
        canvas.bind('<Configure>',lambda e:canvas.itemconfigure(win,width=e.width))
        canvas.bind('<MouseWheel>',lambda e:canvas.yview_scroll(-int(e.delta/120),'units'))
        return content
    def number(self,parent,label,key,slider=False):
        row=ttk.Frame(parent);row.pack(fill='x',padx=5,pady=3);ttk.Label(row,text=label,width=25).pack(side='left')
        lo,hi=RANGES[key]
        if slider:ttk.Scale(row,from_=lo,to=hi,variable=self.vars[key],command=lambda value:self.vars[key].set(f'{float(value):.1f}')).pack(side='left',fill='x',expand=True,padx=5)
        ttk.Spinbox(row,from_=lo,to=hi,increment=.01 if hi<=3 else 1,textvariable=self.vars[key],width=10).pack(side='right')
    def color(self,parent,label,key):
        row=ttk.Frame(parent);row.pack(fill='x',padx=5,pady=3);ttk.Label(row,text=label,width=25).pack(side='left')
        ttk.Entry(row,textvariable=self.vars[key],width=12).pack(side='right')
        def choose():
            color=colorchooser.askcolor(self.vars[key].get(),parent=self)[1]
            if color:self.vars[key].set(color)
        ttk.Button(row,text='选择颜色',command=choose).pack(side='right',padx=6)
    def check(self,parent,label,key):ttk.Checkbutton(parent,text=label,variable=self.vars[key]).pack(anchor='w',padx=5,pady=5)
    def choice(self,parent,label,key,choices):
        row=ttk.Frame(parent);row.pack(fill='x',padx=5,pady=3);ttk.Label(row,text=label).pack(side='left')
        labels={'left':'左对齐','center':'居中','right':'右对齐'}
        display=tk.StringVar(value=labels[self.vars[key].get()])
        box=ttk.Combobox(row,values=list(labels.values()),textvariable=display,state='readonly');box.pack(side='right')
        box.bind('<<ComboboxSelected>>',lambda e:self.vars[key].set(next(k for k,v in labels.items() if v==display.get())))
        self.vars[key].trace_add('write',lambda *_:display.set(labels[self.vars[key].get()]))
    def values(self):
        values={k:v.get() for k,v in self.vars.items()};values['smart']={k:v.get() for k,v in self.smart_vars.items()};values['smart']['version']=1
        values['animation']=dict(version=1,**{stage:{k:v.get() for k,v in vs.items()} for stage,vs in self.animation_vars.items()})
        return normalize(values)
    def set_style(self,style):
        self.loading=True
        for k,v in normalize(style).items():
            if k=='smart':
                for key,value in v.items():self.smart_vars[key].set(value)
            elif k=='animation':
                for stage in ('entry','exit','loop'):
                    for key,value in v[stage].items():self.animation_vars[stage][key].set(value)
            else:self.vars[k].set(v)
        # Refresh the generated example, preserving manually edited preview text.
        if self.sample.get('1.0','end-1c')==self.sample_auto_text:
            self.sample_auto_text=preview_text(style)
            self.sample.delete('1.0','end');self.sample.insert('1.0',self.sample_auto_text)
        self.loading=False;self.changed()
    def changed(self):
        if self.loading or self.closed:return
        if self.timer:self.after_cancel(self.timer)
        self.current+=1;self.timer=self.after(400,self.enqueue)
    def enqueue(self):
        self.timer=None
        try:style=self.values();freeze(style)
        except Exception as exc:self.status.set(str(exc));return
        text=self.sample.get('1.0','end-1c')[:1000]
        self.seq+=1;self.jobs.put((0,self.seq,('preview',self.current,style,text)))
        self.status.set('正在更新预览…')
    def thumbnails(self):
        if self.closed:return
        for i in range(len(FANCY)):
            style=preset(i,dict(DEFAULT,font_size=90,y=.60))
            text='降价 Aa 99元' if style['highlight_keywords'] else '字幕 Aa 99元'
            self.seq+=1;self.jobs.put((1,self.seq,('thumb',i,style,text)))
    def worker(self):
        while not self.closed:
            try:_,_,job=self.jobs.get(timeout=.2)
            except queue.Empty:continue
            kind,ident,style,text=job
            if kind=='preview' and ident!=self.current:continue
            try:
                if kind=='preview':path,cues=self.renderer.playback(style,text);self.results.put((kind,ident,(path,cues),self.renderer.last_seconds))
                else:path=self.renderer.render(style,text);self.results.put((kind,ident,path,self.renderer.last_seconds))
            except Exception as exc:self.results.put(('error',ident,str(exc),0))
        self.renderer.close()
    def poll(self):
        if self.closed:return
        while not self.results.empty():
            kind,ident,path,seconds=self.results.get()
            if kind=='error':self.status.set('预览失败：'+str(path));continue
            if kind=='preview':
                if ident!=self.current:continue
                path,self.cues=path
                self.frames=[]
                with Image.open(path) as src:
                    from PIL import ImageSequence
                    self.frames=[ImageTk.PhotoImage(frame.convert('RGB')) for frame in ImageSequence.Iterator(src)]
                self.play_index=0;self.playing=True;self.scrub.configure(to=max(0,len(self.frames)-1));self.show_frame()
                if not self.play_task:self.play_task=self.after(83,self.tick)
                note=''
                if self.vars['highlight_keywords'].get().strip() and not any(c.get('highlight') for c in self.cues):
                    note=' · 预览文字未命中关键词，请填写文案中实际出现的词'
                self.status.set(f'实际 Remotion 动画已更新 · {seconds:.2f}秒 · {len(self.cues)}句'+note);continue
            with Image.open(path) as src:
                if kind=='thumb':im=src.crop((0,1080,1080,1530)).resize((180,75),Image.Resampling.LANCZOS)
                else:im=src.resize((self.preview_w,self.preview_h),Image.Resampling.LANCZOS)
            image=ImageTk.PhotoImage(im)
            if kind=='thumb':self.images['thumb'+str(ident)]=image;self.thumb_buttons[ident].configure(image=image)
            elif ident==self.current:self.images['preview']=image;self.preview.itemconfigure(self.preview_image,image=image);self.status.set(f'预览已更新 · 单帧 {seconds:.2f} 秒')
        self.poll_id=self.after(100,self.poll)
    def font_picker(self):
        dialog=tk.Toplevel(self);dialog.title('本机字体 · 搜索与实际字形');dialog.geometry('660x610')
        query=tk.StringVar();ttk.Entry(dialog,textvariable=query).pack(fill='x',padx=8,pady=8)
        book=ttk.Notebook(dialog);book.pack(fill='both',expand=True);body=self.page(book,'字体列表（最近使用优先）')
        def fill(*_):
            for w in body.winfo_children():w.destroy()
            found=[f for f in self.fonts if query.get().casefold() in f['family'].casefold()]
            found.sort(key=lambda f:(f['family'] not in self.recent,self.recent.index(f['family']) if f['family'] in self.recent else 999))
            for f in found:
                def choose(font=f):
                    self.loading=True;self.vars['font_family'].set(font['family']);self.vars['font_file'].set(font['path'] if font['imported'] else '');self.vars['font_sha256'].set('');self.loading=False
                    self.recent=([font['family']]+[x for x in self.recent if x!=font['family']])[:12];atomic(self.recent_path,self.recent);self.changed();dialog.destroy()
                tk.Button(body,text=f["family"]+'    字体预览 Aa 123',font=(f['family'],13),anchor='w',command=choose).pack(fill='x',pady=2)
        query.trace_add('write',fill);fill()
    def import_font(self):
        path=filedialog.askopenfilename(parent=self,filetypes=[('字体','*.ttf *.otf')])
        if path:
            def run():self.set_style(dict(self.values(),**import_font(path)));self.fonts=scan()
            self.guard(run)
    def apply(self):
        def run():
            style=freeze(self.values());self.app.cfg['subtitle_style']=deepcopy(style)
            self.app.save_settings();self.status.set('完整字幕样式已保存；新任务生效。')
        self.guard(run)
    def refresh_presets(self):self.preset_box.configure(values=list(self.presets.data['items']) if self.presets else [])
    def require_presets(self):
        if self.presets is None:raise ValueError('预设文件损坏，未覆盖原文件。请恢复 subtitle_presets.json.bak 后重新打开编辑器。')
    def load_preset(self):
        self.require_presets();name=self.preset_name.get();saved=self.presets.data['items'][name];kind=self.presets.data.get('kinds',{}).get(name,'full');current=self.values()
        if kind=='animation':current['animation']=deepcopy(saved['animation'])
        elif kind=='style':current.update({k:deepcopy(v) for k,v in saved.items() if k not in ('smart','animation')})
        else:current=deepcopy(saved)
        self.set_style(current)
    def save_preset(self):
        self.require_presets();name=simpledialog.askstring('保存字幕预设','预设名称',parent=self)
        if name:
            if name in self.presets.data['items'] and not messagebox.askyesno('覆盖预设',f'替换预设“{name}”？',parent=self):return
            self.presets.save(name,self.values(),{'样式':'style','动画':'animation','完整方案':'full'}[self.preset_kind.get()]);self.refresh_presets();self.preset_name.set(name)
    def rename_preset(self):
        self.require_presets();name=simpledialog.askstring('重命名预设','新名称',parent=self)
        if name:self.presets.rename(self.preset_name.get(),name);self.refresh_presets();self.preset_name.set(name)
    def delete_preset(self):
        self.require_presets();name=self.preset_name.get()
        if name and messagebox.askyesno('删除预设',f'删除“{name}”？',parent=self):self.presets.delete(name);self.refresh_presets();self.preset_name.set('')
    def default_preset(self):
        self.require_presets();name=self.preset_name.get();self.presets.set_default(name);self.set_style(self.presets.data['items'][name]);self.apply()
    def close(self):
        self.closed=True
        for task in (self.timer,getattr(self,'poll_id',None),getattr(self,'thumb_id',None),self.play_task):
            if task:
                try:self.after_cancel(task)
                except tk.TclError:pass
        self.destroy()
