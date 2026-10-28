"""Small SK entry points using the existing workbench and material list."""
from copy import deepcopy
import tkinter as tk
from tkinter import ttk

from sk_materials import REVISION, TYPES, LABELS, PREFERENCES, active, summary, preferences


def summary_text(batch):
    report = batch.get('sk_summary', {})
    lines = ['类别｜目标占比｜规划实际占比｜实际秒数｜镜头数｜独立场景组数']
    for row in report.get('types', []):
        lines.append(f'{row["type"]} {row["label"]}｜{row["target_percent"]:.1f}%｜{row["actual_percent"]:.1f}%｜{row["seconds"]:.3f}｜{row["clip_count"]}｜{row["scene_count"]}')
    lines += [report.get('deviation_reason', ''),
              f'已规划{report.get("planned",0)}条，未规划{report.get("unplanned",0)}条。']
    hist = batch.get('sk_history_coverage', {})
    if hist:
        lines.append(f'历史窗口：{hist["window_start"]}～{hist["window_end"]}；成功{hist["success_records"]}条、有效预留{hist["reservation_records"]}条；{hist["coverage"]}')
    bias=report.get('material_bias',{})
    if bias.get('rules'):
        lines+=['','素材偏好｜提升倍数｜可用文件｜选中次数｜实际秒数｜整批时长占比']
        for row in bias['rules']:
            lines.append(f'{row["path"]}｜{row["factor"]:g}倍｜{row["available_files"]}｜{row["selected_clips"]}｜{row["seconds"]:.3f}｜{row["actual_percent"]:.1f}%')
        lines.append(bias['note']+' 汇总按更具体路径归属计数，不重复累计。')
    return '\n'.join(lines)


def detail_text(item):
    snapshot = item['sk_snapshot']
    lines = [f'SK方案 {item["number"]}；首镜类型：{snapshot["opening_type"]}；总时长：{item["duration"]:.3f}秒',
             '顺序｜类型｜相似场景组｜数字摆放组｜素材｜实际播放时长']
    for i, c in enumerate(item['clips']):
        lines.append(f'{i+1}｜{c["category"]}｜{c.get("sk_scene_path") or "独立素材"}｜{c.get("sk_placement_path") or "无"}｜{c["relative_path"]}｜{c["duration_frames"]/item["output_fps"]:.3f}秒')
    nearest=item.get('dedup_detail', {}).get('nearest') or {}
    scene=nearest.get('scene_nearest') or {}
    base=nearest.get('base_nearest') or {}
    nearest_text=(str(scene.get('other_id'))+'：'+scene.get('reason','')) if scene else ('同素材原片区间最高重合'+f'{base.get("max_ratio",0):.1%}' if base else '未发现相似记录')
    lines += ['', '各类实际秒数（转场各分摊一半）：' + '；'.join(f'{k} {LABELS[k]} {snapshot["category_seconds"][k]:.3f}秒' for k in TYPES),
              '本批时长偏好：' + '；'.join(f'{k} {v:g}%' for k,v in snapshot['preferences'].items()),
              f'允许的相似场景软调整：{snapshot["soft_scene_adjustments"]}次',
              '最相似组合及原因：' + nearest_text,
              '比例为整批软目标，单条允许偏离；数字摆放互斥及相同内容重复不放宽。']
    bias=snapshot.get('material_bias',{}).get('rules',[])
    if bias:lines+=['本批素材偏好：'+'；'.join(f'{r["path"]} ×{r["factor"]:g}' for r in bias)]
    return '\n'.join(lines)


def build(controller, parent):
    c = controller
    row = ttk.Frame(parent); row.pack(fill='x', padx=5, pady=5)
    c.sk_scheme_row = row
    ttk.Label(row, text='牛肉剪辑方案').pack(side='left')
    c.sk_scheme_var = tk.StringVar(value='SK多样化混剪' if active(c.cfg) else '原牛肉方案')
    c.sk_scheme_control = ttk.Combobox(row, textvariable=c.sk_scheme_var,
        values=('原牛肉方案', 'SK多样化混剪'), state='readonly', width=18)
    c.sk_scheme_control.pack(side='left', padx=8)
    c.sk_scheme_control.bind('<<ComboboxSelected>>', lambda _: c.guard(lambda: switch(c)))
    c.sk_settings_control = ttk.Button(row, text='SK方案设置', command=lambda: c.guard(lambda: settings_dialog(c)))
    c.sk_settings_control.pack(side='left', padx=8)
    c.sk_summary_var = tk.StringVar()
    ttk.Label(row, textvariable=c.sk_summary_var, wraplength=650).pack(side='left', padx=5)
    c.sk_media_controls = ttk.Frame(c.media_tab)
    for text, action in [('标记所选可作首镜', lambda: mark(c, opening=True)),
                         ('取消所选首镜资格', lambda: mark(c, opening=False)),
                         ('允许所选安全裁切', lambda: mark(c, safe_cut=True)),
                         ('保持所选完整片段', lambda: mark(c, safe_cut=False))]:
        ttk.Button(c.sk_media_controls, text=text, command=lambda fn=action: c.guard(fn)).pack(side='left', padx=4, pady=5)
    ttk.Button(c.sk_media_controls,text='提高所选素材比例',command=lambda:c.guard(lambda:prefer_selected(c))).pack(side='left',padx=4,pady=5)
    c.sk_output_row = ttk.Frame(c.settings_content)
    ttk.Label(c.sk_output_row, text='SK画面适配').pack(side='left')
    c.sk_fit_var = tk.StringVar(value='裁切铺满' if c.cfg.get('sk_fit') == 'cover' else '保留完整画面＋黑色填充')
    fit = ttk.Combobox(c.sk_output_row, textvariable=c.sk_fit_var,
        values=('保留完整画面＋黑色填充', '裁切铺满'), state='readonly', width=25)
    fit.pack(side='left', padx=8)
    ttk.Button(c.sk_output_row, text='设为3:4画布', command=lambda: set_canvas(c)).pack(side='left', padx=8)
    ttk.Button(c.sk_output_row, text='查看画面适配预览', command=lambda: c.guard(lambda: fit_preview(c))).pack(side='left', padx=8)
    ttk.Label(c.sk_output_row, text='裁切铺满可能裁掉盘边或主体；请先预览。', wraplength=440).pack(side='left')
    c.sk_fit_var.trace_add('write', lambda *_: fit_changed(c))
    sync(c)


def sync(c):
    if not hasattr(c, 'sk_scheme_row'):
        return
    fresh = c.cfg.get('editing_profile_id') == 'fresh_beef_standard'
    enabled = active(c.cfg)
    c.sk_scheme_row.pack_forget()
    if fresh:
        c.sk_scheme_row.pack(fill='x', padx=5, pady=5)
    c.sk_scheme_var.set('SK多样化混剪' if enabled else '原牛肉方案')
    c.sk_settings_control.state(['!disabled' if enabled else 'disabled'])
    import math
    divisor=math.gcd(int(c.cfg['width']),int(c.cfg['height']))
    c.sk_summary_var.set(f'仅SK｜{c.cfg["width"]//divisor}:{c.cfg["height"]//divisor}画布｜A/E＋人工首镜｜类别偏好已配置｜近72小时＋本批' if enabled else '')
    c.sk_media_controls.pack_forget(); c.sk_output_row.pack_forget()
    if hasattr(c,'opening_c_shortcut_label'):
        if enabled:
            c.opening_c_shortcut_label.pack_forget();c.opening_c_shortcut.pack_forget()
        elif not c.opening_c_shortcut.winfo_manager():
            c.opening_c_shortcut_label.pack(side='left',padx=(0,3),before=c.voice_config_shortcut)
            c.opening_c_shortcut.pack(side='left',padx=(0,8),before=c.voice_config_shortcut)
    if hasattr(c,'group_folder_controls'):
        c.group_folder_controls.pack_forget()
        if not enabled:c.group_folder_controls.pack(fill='x',padx=10,pady=8)
    c.media_tree.heading('mutex',text='数字组 / 相似组' if enabled else '互斥组')
    c.media_tree.heading('member',text='数字组编号' if enabled else '组内成员')
    if enabled:
        c.sk_media_controls.pack(fill='x', padx=9, pady=3, before=c.media_tree.master)
        c.sk_output_row.pack(fill='x', padx=10, pady=8)
        c.sk_fit_var.set('裁切铺满' if c.cfg.get('sk_fit') == 'cover' else '保留完整画面＋黑色填充')


def switch(c):
    c.require_planning_idle()
    from sk_materials import configure, SCHEME_KEYS
    selected = REVISION if c.sk_scheme_var.get() == 'SK多样化混剪' else 'standard'
    try:
        cfg = configure(c.collect_config(), selected)
        c.cfg = cfg
        for key in SCHEME_KEYS:
            if key in c.vars and key in cfg:
                c.vars[key].set(str(cfg[key]))
            if key in c.processing_bools:
                c.processing_bools[key].set(bool(cfg.get(key, False)))
        c.clips = []; c.music = []; c.review_var.set(False); c.rights_var.set(False)
        c.load_profile_controls(); c.refresh_media(); c.refresh_music(); c.persist_config(cfg)
        c.log('已选择' + ('SK多样化混剪；请扫描SK素材。' if active(cfg) else '原牛肉方案；原设置已恢复。'))
    except Exception:
        sync(c)
        raise


def set_canvas(c):
    c.var('width').set('1080'); c.var('height').set('1440')
    c.log('SK输出画布设置为1080×1440；请保存或建立新方案。')


def fit_changed(c):
    if active(c.cfg) and hasattr(c, 'sk_fit_var'):
        c.cfg['sk_fit'] = 'cover' if c.sk_fit_var.get() == '裁切铺满' else 'contain'


def mark(c, *, opening=None, safe_cut=None):
    if not active(c.cfg):
        raise ValueError('请先选择SK多样化混剪方案')
    c.require_planning_idle()
    selected = [c.clips[int(i)] for i in c.media_tree.selection()]
    if not selected:
        raise ValueError('请先在素材清单选择一个或多个镜头')
    from sk_materials import Review
    review = Review(c.store, c.cfg['material_root'])
    if opening is not None:
        review.exclude_opening(selected, not opening)
    if safe_cut is not None:
        review.safe_cut(selected, safe_cut)
    c.refresh_media()
    c.log(f'SK已更新所选{len(selected)}个镜头；已冻结批次保持原快照。')


def prefer_selected(c):
    selected=[c.clips[int(i)]['path'] for i in c.media_tree.selection()]
    if not selected:raise ValueError('请先在素材清单选择一个或多个SK镜头')
    return settings_dialog(c,initial_bias_paths=selected)


def settings_dialog(c,*,initial_bias_paths=()):
    if not active(c.cfg):
        raise ValueError('请先选择SK多样化混剪')
    cfg = deepcopy(c.collect_config())
    stats = summary(c.clips, getattr(c, 'scan_warnings', []))
    info = '\n'.join(f'{r["type"]} {r["label"]}：{r["files"]}文件 / {r["similar_groups"]}相似组 / {r["placement_groups"]}数字组 / {r["independent"]}独立镜头 / 可用场景{r["available_scenes"]} / 首镜{r["opening_candidates"]}' for r in stats['types'])
    info += '\n\nA/E默认可作首镜；其他类型仅人工指定的具体文件可用。\n首镜标记与安全裁切在原素材清单完成。'
    info += '\n\n本方案沿用“画面处理”和“时长与裁切”。裁短镜头需先在素材清单允许安全裁切；按配音时长匹配时按实际差额裁短。需要自由裁片时，可关闭工作台的“按配音时长选片并裁差额”。'
    if stats['warnings']:
        info += '\n\n待确认：\n' + '\n'.join(stats['warnings'])
    if getattr(c, '_qt_parent', None) is None:
        return _tk_settings(c, cfg, info,initial_bias_paths)
    from qt_workbench import W, C
    win = W.QDialog(c._qt_parent); win.setWindowTitle('SK多样化混剪 · 方案设置'); win.resize(860, 670)
    layout = W.QVBoxLayout(win); tabs = W.QTabWidget(); layout.addWidget(tabs)
    first = W.QWidget(); first_layout = W.QVBoxLayout(first)
    material = W.QPlainTextEdit(info); material.setReadOnly(True); first_layout.addWidget(material)
    jump = W.QPushButton('到素材清单标记首镜 / 安全裁切'); first_layout.addWidget(jump)
    jump.clicked.connect(lambda: (win.accept(), c.tabs.select(c.media_tab)))
    scan = W.QPushButton('扫描SK素材与音乐'); first_layout.addWidget(scan)
    scan.clicked.connect(lambda: (win.accept(), c.guard(c.scan)))
    output_settings=W.QPushButton('到通用输出设置调整画布 / 预览适配');first_layout.addWidget(output_settings)
    output_settings.clicked.connect(lambda: (win.accept(),c.tabs.select(c.env_tab)))
    tabs.addTab(first, '素材与首镜')
    page = W.QWidget(); form = W.QFormLayout(page); entries = {}
    for row in stats['types']:
        key = row['type']; field = W.QDoubleSpinBox(); field.setRange(0, 100); field.setDecimals(2)
        field.setObjectName('skCategory'+key)
        field.setValue(preferences(cfg)[key]); field.setSuffix(' %'); entries[key] = field
        title=key+' '+LABELS[key] if key!=LABELS[key] else key
        form.addRow(f'{title}（可用场景 {row["available_scenes"]}）', field)
    total = W.QLabel(); form.addRow('合计', total)
    def update_total():
        value = sum(f.value() for f in entries.values()); total.setText(f'{value:.2f}%'+('，可以保存' if abs(value-100)<.00001 else '，须合计100%'))
    for field in entries.values():
        field.valueChanged.connect(update_total)
    update_total()
    note = W.QLabel('按整批最终画面时长温和调整；单条可以偏离。0%类别首镜和后续均不使用。'); note.setWordWrap(True); form.addRow(note)
    buttons = W.QHBoxLayout(); defaults = W.QPushButton('恢复本方案默认值'); normalize = W.QPushButton('一键归一化')
    buttons.addWidget(defaults); buttons.addWidget(normalize); form.addRow(buttons)
    defaults.clicked.connect(lambda: [entries[k].setValue(PREFERENCES[k]) for k in TYPES])
    def normalized():
        values = [entries[k].value() for k in TYPES]; amount = sum(values)
        if amount <= 0:
            return W.QMessageBox.information(win, '类别时长偏好', '请先给至少一个类别填写大于0的比例。')
        rounded = [round(v/amount*100, 2) for v in values]
        target = max(range(len(values)), key=lambda j: values[j]); rounded[target] += 100-sum(rounded)
        for k, value in zip(TYPES, rounded):
            entries[k].setValue(value)
    normalize.clicked.connect(normalized)
    preference_scroll=W.QScrollArea();preference_scroll.setWidgetResizable(True);preference_scroll.setWidget(page)
    tabs.addTab(preference_scroll, '类别时长偏好')
    history_page = W.QWidget(); history_layout = W.QVBoxLayout(history_page)
    history = W.QPlainTextEdit('滚动72小时成功历史＋所有有效任务预留＋本批。\n任务预留不按创建时间自动释放。\n点击“载入历史覆盖情况”在后台核对当前范围。'); history.setReadOnly(True); history_layout.addWidget(history)
    refresh = W.QPushButton('载入历史覆盖情况'); history_layout.addWidget(refresh)
    def refresh_history():
        from sk_selection import load_history
        refresh.setEnabled(False)
        def finish(result):
            try:
                history.setPlainText(json_text(result[1])); refresh.setEnabled(True)
            except RuntimeError:
                pass
        c.launch_planning(lambda: load_history(c.store, cfg, deepcopy(c.clips)), finish)
    refresh.clicked.connect(lambda: c.guard(refresh_history))
    advanced = W.QGroupBox('高级去重参数（沿用通用去重强度）'); advanced.setCheckable(True); advanced.setChecked(False)
    mode_label='更严格' if cfg['dedup_mode']=='strict' else '均衡'
    advanced_layout = W.QVBoxLayout(advanced); threshold = W.QLabel(f'当前：{mode_label}；相同原片区间上限{cfg["selection_v2_interval_limit"]:.0%}。\n在原“首镜 / 时长 / 去重”区域调整。'); threshold.setWordWrap(True)
    advanced_layout.addWidget(threshold); threshold.setVisible(False); advanced.toggled.connect(threshold.setVisible); history_layout.addWidget(advanced)
    tabs.addTab(history_page, '历史去重')
    from sk_material_bias_ui import qt_panel
    bias=qt_panel(c,cfg);tabs.addTab(bias,'素材偏好');win.bias_panel=bias
    if initial_bias_paths:bias.add_paths(initial_bias_paths,'file');tabs.setCurrentWidget(bias)
    error = W.QLabel(); error.setWordWrap(True); error.setStyleSheet('color:#b42318'); layout.addWidget(error)
    actions = W.QDialogButtonBox(W.QDialogButtonBox.StandardButton.Save | W.QDialogButtonBox.StandardButton.Cancel); layout.addWidget(actions)
    actions.button(W.QDialogButtonBox.StandardButton.Save).setText('保存')
    actions.button(W.QDialogButtonBox.StandardButton.Cancel).setText('取消')
    def save():
        try:
            if not active(c.cfg):
                raise ValueError('方案已切换，请重新打开SK设置')
            values = {k: entries[k].value() for k in TYPES}; preferences(dict(cfg, sk_type_percentages=values))
            current = c.collect_config(); current['sk_type_percentages'] = values
            if current.get('product_spec_id')!=cfg.get('product_spec_id'):raise ValueError('商品规格已切换，请重新打开SK设置')
            from sk_material_bias import KEY
            current[KEY]=bias.rules()
            c.persist_config(current); sync(c); win.accept(); c.log('SK类别时长及素材偏好已保存，仅用于新规划。')
        except (ValueError, RuntimeError) as exc:
            error.setText(str(exc))
    actions.accepted.connect(save); actions.rejected.connect(win.reject)
    c.sk_settings_window = win; win.show(); return win


def json_text(value):
    labels = dict(window_start='窗口开始', window_end='窗口结束', hours='小时', success_records='成功历史条数',
                  reservation_records='有效预留条数', unknown_records='无法还原记录', unknown_time_records='时间未知记录',
                  warnings='覆盖问题', coverage='覆盖说明', load_seconds='载入秒数')
    return '\n'.join(f'{label}：{value.get(key)}' for key, label in labels.items())


def _tk_settings(c, cfg, info,initial_bias_paths=()):
    win = tk.Toplevel(c); win.title('SK方案设置'); win.geometry('850x650')
    tabs = ttk.Notebook(win); tabs.pack(fill='both', expand=True); fields = {}
    first = ttk.Frame(tabs); tabs.add(first, text='素材与首镜')
    text = tk.Text(first, wrap='word'); text.insert('1.0', info); text.configure(state='disabled'); text.pack(fill='both', expand=True)
    ttk.Button(first, text='到素材清单标记首镜 / 安全裁切', command=lambda: (win.destroy(), c.tabs.select(c.media_tab))).pack()
    page = ttk.Frame(tabs); tabs.add(page, text='类别时长偏好')
    for i, key in enumerate(TYPES):
        ttk.Label(page, text=key+' '+LABELS[key]).grid(row=i, column=0, padx=10, pady=6)
        fields[key] = tk.StringVar(value=str(preferences(cfg)[key])); ttk.Entry(page, textvariable=fields[key]).grid(row=i, column=1)
    error = tk.StringVar(); ttk.Label(page, textvariable=error).grid(row=9, column=0, columnspan=2)
    total=tk.StringVar();ttk.Label(page,textvariable=total).grid(row=8,column=0,columnspan=2)
    def update_total(*_):
        try:total.set(f'合计：{sum(float(v.get()) for v in fields.values()):.2f}%（须为100%）')
        except ValueError:total.set('请填写有效百分比')
    for var in fields.values():var.trace_add('write',update_total)
    update_total()
    def save():
        try:
            if not active(c.cfg):raise ValueError('方案已切换，请重新打开SK设置')
            values = {k: float(v.get()) for k, v in fields.items()}; preferences(dict(cfg, sk_type_percentages=values))
            current = c.collect_config(); current['sk_type_percentages'] = values
            if current.get('product_spec_id')!=cfg.get('product_spec_id'):raise ValueError('商品规格已切换，请重新打开SK设置')
            from sk_material_bias import KEY
            current[KEY]=bias.rules();c.persist_config(current); sync(c); win.destroy()
        except ValueError as exc:
            error.set(str(exc))
    ttk.Button(page, text='恢复本方案默认值', command=lambda: [fields[k].set(str(PREFERENCES[k])) for k in TYPES]).grid(row=10, column=0)
    def normalize():
        try:
            values={k:float(v.get()) for k,v in fields.items()};amount=sum(values.values())
            if amount<=0 or any(v<0 for v in values.values()):raise ValueError('请填写非负比例，且至少一个大于0')
            rounded={k:round(v/amount*100,2) for k,v in values.items()};largest=max(values,key=values.get)
            rounded[largest]+=100-sum(rounded.values())
            for k,v in rounded.items():fields[k].set(f'{v:.2f}')
        except ValueError as exc:error.set(str(exc))
    ttk.Button(page,text='一键归一化',command=normalize).grid(row=10,column=1)
    history = ttk.Frame(tabs); tabs.add(history, text='历史去重')
    ttk.Label(history, text='滚动72小时成功历史＋本批＋所有有效预留；诊断见规划汇总。', wraplength=700).pack(pady=20)
    history_body=tk.Text(history,wrap='word');history_body.pack(fill='both',expand=True)
    def load():
        from sk_selection import load_history
        def finish(result):
            if win.winfo_exists():history_body.delete('1.0','end');history_body.insert('1.0',json_text(result[1]))
        c.launch_planning(lambda:load_history(c.store,cfg,deepcopy(c.clips)),finish)
    ttk.Button(history,text='载入历史覆盖情况',command=lambda:c.guard(load)).pack(pady=5)
    from sk_material_bias_ui import tk_panel
    bias=tk_panel(c,cfg,tabs);tabs.add(bias,text='素材偏好');win.bias_panel=bias
    if initial_bias_paths:bias.add_paths(initial_bias_paths,'file');tabs.select(bias)
    ttk.Button(win, text='保存', command=save).pack(side='right', padx=10, pady=10)
    return win


def show_report(c, item=None):
    if not c.batch:
        raise ValueError('请先规划或载入组合方案')
    text = detail_text(item) if item else summary_text(c.batch)
    if getattr(c, '_qt_parent', None) is not None:
        from qt_workbench import W
        win = W.QDialog(c._qt_parent); win.setWindowTitle('SK组合明细' if item else 'SK批次时长与历史汇总'); win.resize(1050, 650)
        layout = W.QVBoxLayout(win); body = W.QPlainTextEdit(text); body.setReadOnly(True); layout.addWidget(body)
        close = W.QPushButton('关闭'); close.clicked.connect(win.accept); layout.addWidget(close)
        c.sk_report_window = win; win.show(); return win
    win = tk.Toplevel(c); win.title('SK规划明细'); body = tk.Text(win, wrap='word'); body.insert('1.0', text); body.configure(state='disabled'); body.pack(fill='both', expand=True)
    return win


def fit_preview(c):
    if not c.clips:
        raise ValueError('请先扫描SK素材，在清单中选择要预览的镜头')
    from pathlib import Path
    from core import resolve_tool, run_capture
    selected = c.media_tree.selection()
    clip = c.clips[int(selected[0])] if selected else c.clips[0]
    cfg = c.collect_config(); folder = c.data_dir/'preview'/'sk_fit'; folder.mkdir(parents=True, exist_ok=True)
    output = folder/'fit_preview.png'
    width, height = cfg['width'], cfg['height']
    fit = 'increase' if cfg.get('sk_fit') == 'cover' else 'decrease'
    operation = f'crop={width}:{height}' if fit == 'increase' else f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black'
    def worker():
        run_capture([resolve_tool('ffmpeg', cfg['ffmpeg']), '-v', 'error', '-y', '-i', clip['path'],
            '-vf', f'scale={width}:{height}:force_original_aspect_ratio={fit},{operation},setsar=1', '-frames:v', '1', str(output)], 90)
        return output
    def finish(path):
        if getattr(c, '_qt_parent', None) is not None:
            from qt_workbench import W, G, C
            win = W.QDialog(c._qt_parent); win.setWindowTitle('SK画面适配预览 · '+c.sk_fit_var.get()); win.resize(650, 760)
            layout = W.QVBoxLayout(win); image = W.QLabel(); image.setAlignment(C.Qt.AlignmentFlag.AlignCenter)
            image.setPixmap(G.QPixmap(str(path)).scaled(600, 660, C.Qt.AspectRatioMode.KeepAspectRatio, C.Qt.TransformationMode.SmoothTransformation)); layout.addWidget(image)
            label = W.QLabel('预览仅检查画面适配；裁切铺满可能裁掉主体，原素材不修改。'); label.setWordWrap(True); layout.addWidget(label)
            c.sk_fit_preview_window = win; win.show()
        else:
            from app import open_local
            open_local(str(path))
    c.launch_planning(worker, finish)
