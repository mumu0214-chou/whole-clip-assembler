# -*- coding: utf-8 -*-
"""Category-specific manual script management over the existing library."""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from editing_profiles import FRESH, BALL, NAMES, SCRIPT_NAMES
from script_library import ScriptLibrary, normalize_text
from voice_pipeline import VoiceError, split_script_text
from paged_tree import PagedTree
from script_tools_ui import ScriptToolsMixin
from script_spec_ui import ScriptSpecControls




class ScriptLibraryWindow(ScriptSpecControls,ScriptToolsMixin, tk.Toplevel):
    def __init__(self, app, library: ScriptLibrary, *, profile_id=None):
        super().__init__(app)
        self.app, self.library = app, library
        self.profile_names = library.catalog.categories()
        self.script_names={k:v+'文案' for k,v in self.profile_names.items()}
        self.active_profile = profile_id or app.cfg.get('editing_profile_id', FRESH)
        self.active_spec=app.cfg.get('product_spec_id','default') if self.active_profile==app.cfg.get('editing_profile_id') else 'default'
        self.library.catalog.get(self.active_profile,self.active_spec)
        self._search_after = None
        if self.active_profile not in NAMES:
            self.destroy()
            raise VoiceError('未知文案品类。')
        self.selected_id = None
        self.original_text = ''
        self.drafts = {}
        self._refreshing = False
        self.status = tk.StringVar(value='')
        self.category_note = tk.StringVar(value='')
        self.editor_note = tk.StringVar(value='')
        self.title('文案库 · 分品类管理')
        self.geometry('1100x760')
        self.minsize(850, 620)
        self.transient(app)
        self._build()
        self.refresh()

    def _button(self, parent, name, action):
        ttk.Button(parent, text=name, command=lambda: self._guard(action)).pack(side='left', padx=3)

    def _guard(self, action):
        try:
            action()
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror('文案库', str(exc), parent=self)

    def _build(self):
        self.categories = ttk.Notebook(self, height=1)
        self.categories.pack(fill='x', padx=10, pady=(10, 3))
        self.category_tabs = {}
        for key, name in self.script_names.items():
            tab = ttk.Frame(self.categories)
            self.categories.add(tab, text=name)
            self.category_tabs[key] = tab
        self.categories.select(self.category_tabs[self.active_profile])
        self.categories.bind('<<NotebookTabChanged>>', lambda _: self._guard(self._category_changed))
        self.build_script_spec_controls()
        ttk.Label(self, textvariable=self.category_note, wraplength=1060).pack(fill='x', padx=12, pady=3)

        filters = ttk.Frame(self)
        filters.pack(fill='x', padx=10, pady=5)
        ttk.Label(filters, text='搜索文案或ID').pack(side='left')
        self.query = tk.StringVar(value='')
        search = ttk.Entry(filters, textvariable=self.query, width=28)
        search.pack(side='left', padx=5)
        search.bind('<Return>', lambda _: self._guard(self.search))
        search.bind('<KeyRelease>', self._schedule_search)
        self._button(filters, '搜索', self.search)
        self._button(filters, '清空搜索', self.clear_search)
        ttk.Label(filters, text='状态').pack(side='left', padx=(12, 0))
        self.status_filter = tk.StringVar(value='全部')
        state = ttk.Combobox(filters, values=('全部', '启用', '停用'), textvariable=self.status_filter,
                             state='readonly', width=7)
        self.status_selector = state
        state.pack(side='left', padx=5)
        state.bind('<<ComboboxSelected>>', lambda _: self._guard(self.refresh))
        ttk.Label(filters, text='排序').pack(side='left')
        self.sort = tk.StringVar(value='ID升序')
        order = ttk.Combobox(filters, values=('ID升序', 'ID降序', '使用次数', '最近使用', '状态'), textvariable=self.sort,
                             state='readonly', width=9)
        self.sort_selector = order
        order.pack(side='left', padx=5)
        order.bind('<<ComboboxSelected>>', lambda _: self._guard(self.refresh))
        self._button(filters, '刷新', self.refresh)

        buttons = ttk.Frame(self)
        buttons.pack(fill='x', padx=10, pady=5)
        for name, action in (('新增文案', self.new), ('保存新增', self.add), ('保存修改', self.edit),
                             ('批量导入', self.import_many), ('启用', lambda: self.enable(True)),
                             ('停用', lambda: self.enable(False)), ('删除', self.delete)):
            self._button(buttons, name, action)
        self.add_tools(buttons)

        footer = ttk.Frame(self)
        footer.pack(side='bottom', fill='x', padx=10, pady=4)
        panes = ttk.Panedwindow(self, orient='vertical')
        panes.pack(fill='both', expand=True, padx=10, pady=3)
        listing = ttk.Frame(panes)
        panes.add(listing, weight=3)
        cols = ('id', 'preview', 'status', 'usage', 'reserved', 'last', 'external')
        self.tree = PagedTree(listing, columns=cols, show='headings', height=8)
        for key, name, width in (('id', 'ID', 90), ('preview', '文案预览', 480),
                                 ('status', '状态', 60), ('usage', '使用次数', 75),
                                 ('reserved', '预留', 55), ('last', '最近使用', 180), ('external','配音总数/可用/复核/缺失',190)):
            self.tree.heading(key, text=name)
            self.tree.column(key, width=width, minwidth=45, stretch=key == 'preview')
        self.tree.heading('id', command=lambda: self._guard(self.toggle_id_sort))
        y = ttk.Scrollbar(listing, orient='vertical', command=self.tree.yview)
        x = ttk.Scrollbar(listing, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        self.tree.grid(row=0, column=0, sticky='nsew')
        y.grid(row=0, column=1, sticky='ns')
        x.grid(row=1, column=0, sticky='ew')
        self.tree.controls(listing, search=False).grid(row=2, column=0, columnspan=2, sticky='ew', pady=5)
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.tree.bind('<<TreeviewSelect>>', lambda _: self._guard(self.load_selected))
        editing = ttk.Frame(panes)
        panes.add(editing, weight=1)
        ttk.Label(editing, textvariable=self.editor_note).pack(anchor='w', pady=(4, 2))
        editor_frame = ttk.Frame(editing)
        editor_frame.pack(fill='both', expand=True)
        self.editor = tk.Text(editor_frame, height=4, wrap='word', undo=True)
        editor_y = ttk.Scrollbar(editor_frame, orient='vertical', command=self.editor.yview)
        self.editor.configure(yscrollcommand=editor_y.set)
        self.editor.pack(side='left', fill='both', expand=True)
        editor_y.pack(side='right', fill='y')
        options = ttk.Frame(footer)
        options.pack(fill='x', pady=3)
        self.reset_after_edit = tk.BooleanVar(value=False)
        ttk.Checkbutton(options, text='保存修改时重置使用次数', variable=self.reset_after_edit).pack(side='left')
        self.move_target = tk.StringVar(value=next(self.profile_names[k] for k in NAMES if k != self.active_profile))
        self.move_target_selector = ttk.Combobox(options, textvariable=self.move_target,
                                               values=[self.profile_names[k] for k in NAMES if k != self.active_profile],
                                               state='readonly', width=20)
        self.move_target_selector.pack(side='left', padx=5)
        self._button(options, '移到另一品类', self.assign_profile)
        self._button(options, '重置使用次数', self.reset_usage)
        ttk.Label(footer, text='新增/导入归属当前页；默认保留使用历史。批量导入支持 --- 或连续编号 1.、2.。',
                  wraplength=800).pack(fill='x', padx=2, pady=2)
        ttk.Label(footer, textvariable=self.status, wraplength=800).pack(fill='x', padx=2, pady=(2, 4))
        self._update_notes()

    def _update_notes(self):
        name = self.script_names[self.active_profile]+' / '+self.spec_choice.get()
        if hasattr(self, 'move_target_selector'):
            targets = [self.profile_names[k] for k in NAMES if k != self.active_profile]
            self.move_target_selector.configure(values=targets)
            if self.move_target.get() not in targets: self.move_target.set(targets[0])
        rules = '原有鲜切牛肉五阶段规则' if self.active_profile == FRESH else 'G组随机组合、覆盖去重和配音时长规则'
        self.category_note.set(f'当前管理：{name} → {rules}。生成时只取对应剪辑方案的文案。')
        self.editor_note.set(f'{name} · ' + (f'正在编辑 {self.selected_id}（点“保存修改”）' if self.selected_id
                                          else '新增草稿（输入后点“保存新增”）'))
        self.title(f'文案库 · {name}')

    def _remember_draft(self):
        self.drafts[(self.active_profile,self.active_spec)] = dict(selected_id=self.selected_id,
            text=self.editor.get('1.0', 'end-1c'), original=self.original_text,
            reset=self.reset_after_edit.get())

    def _category_changed(self):
        selected = self.categories.select()
        key = next(k for k, tab in self.category_tabs.items() if str(tab) == selected)
        if key != self.active_profile:
            self.show_profile(key)

    def show_profile(self, profile_id,spec_id=None):
        if profile_id not in NAMES:
            raise VoiceError('未知文案品类。')
        if profile_id==self.active_profile and spec_id is not None:
            self.show_spec(spec_id);return
        if profile_id != self.active_profile:
            if not self.resolve_draft():
                self.categories.select(self.category_tabs[self.active_profile])
                return
            self._remember_draft()
            self.active_profile = profile_id
            self.active_spec=spec_id or 'default'
            self.refresh_spec_choices()
            self.query.set('')
            self.status_filter.set('全部')
            draft = self.drafts.get((profile_id,self.active_spec), {})
            self._set_editor(draft.get('selected_id'), draft.get('text', ''), draft.get('original', ''))
            self.reset_after_edit.set(draft.get('reset', False))
        self.categories.select(self.category_tabs[profile_id])
        self.refresh()

    def _schedule_search(self, event=None):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(180, lambda: self._guard(self.search))

    def search(self):
        if self._search_after:
            self.after_cancel(self._search_after)
            self._search_after = None
        self.refresh()

    def clear_search(self):
        self.query.set('')
        self.search()

    def toggle_id_sort(self):
        self.sort.set('ID降序' if self.sort.get() == 'ID升序' else 'ID升序')
        self.refresh()

    def refresh(self):
        self._refreshing = True
        try:
            selected=self.tree.selection()
            self.tree.delete(*self.tree.get_children())
            rows = self.library.list(self.status_filter.get(), self.sort.get(),
                                     profile_id=self.active_profile, query=self.query.get(),spec_id=self.active_spec)
            for item in rows:
                stats=self.library.external.summary(item['script_id'])
                self.tree.insert('', 'end', iid=item['script_id'], values=(
                    item['script_id'], item['raw_text'].replace('\n', ' ')[:110],
                    '启用' if item['enabled'] else '停用', item['usage_count'],
                    item['reserved_count'], item['last_used_at'] or '—',
                    f'{stats["bound"]}/{stats["valid"]}/{stats["review"]}/{stats["missing"]}'))
            retained=[sid for sid in selected if self.tree.exists(sid)]
            if retained:self.tree.selection_set(retained)
            elif self.selected_id and self.tree.exists(self.selected_id):
                self.tree.selection_set(self.selected_id)
            elif self.selected_id:
                self.selected_id = None
            self._update_notes()
            self.status.set(f'{self.script_names[self.active_profile]}：当前显示 {len(rows)} 篇。')
        finally:
            self._refreshing = False

    def _set_editor(self, script_id, text, original=None):
        self.selected_id = script_id
        self.original_text = text if original is None else original
        self.editor.delete('1.0', 'end')
        self.editor.insert('1.0', text)
        self.reset_after_edit.set(False)
        self._update_notes()

    def load_selected(self):
        selected = self.tree.selection()
        if len(selected)!=1 or selected[0] == self.selected_id or self._refreshing:
            return
        if self._text() != normalize_text(self.original_text):
            if not self.resolve_draft():
                self.tree.selection_remove(*selected)
                if self.selected_id and self.tree.exists(self.selected_id):
                    self.tree.selection_set(self.selected_id)
                return
        item = self.library.get(selected[0])
        self.library._check_profile(item, self.active_profile,self.active_spec)
        self._set_editor(item['script_id'], item['raw_text'])

    def _text(self):
        return normalize_text(self.editor.get('1.0', 'end-1c'))

    def _selected(self):
        if self.selected_id:self.library._check_profile(self.library.get(self.selected_id),self.active_profile,self.active_spec)
        if len(self.tree.selection())!=1:
            raise VoiceError('编辑原文时请选择一篇；批量启停、删除等可多选。')
        if not self.selected_id or not self.tree.exists(self.selected_id):
            raise VoiceError('请先在当前品类列表中选中一篇文案。')
        self.library._check_profile(self.library.get(self.selected_id), self.active_profile)
        return self.selected_id

    def _selected_many(self):
        selected = self.tree.selection()
        if not selected:
            raise VoiceError('请先选择文案，可全选本页或当前筛选结果。')
        for sid in selected:self.library._check_profile(self.library.get(sid),self.active_profile,self.active_spec)
        return selected

    def current_profile(self):
        return self.active_profile

    def new(self):
        if self._text() != normalize_text(self.original_text):
            if not messagebox.askyesno('未保存的文案', '新建会清空当前未保存内容，继续吗？', parent=self):
                return
        self.tree.selection_remove(*self.tree.selection())
        self._set_editor(None, '')
        self.editor.focus_set()

    def _show_saved(self, script_id):
        self.query.set('')
        self.status_filter.set('全部')
        self._set_editor(script_id, self.library.get(script_id)['raw_text'])
        self.refresh()
        self.tree.see(script_id)

    def add(self):
        script_id = self.library.add(self._text(), profile_id=self.active_profile,spec_id=self.active_spec)
        self._show_saved(script_id)
        self.status.set(f'已新增到{self.script_names[self.active_profile]}：{script_id}。')

    def import_many(self):
        blocks = split_script_text(self._text())
        if not blocks:
            raise VoiceError('请先粘贴待导入的文案。')
        preview = '\n'.join(f'{i}. {value[:32].replace(chr(10), " ")}' for i, value in enumerate(blocks, 1))
        if not messagebox.askokcancel('确认批量导入',
                f'识别到 {len(blocks)} 篇，将加入{self.script_names[self.active_profile]}：\n\n{preview}\n\n确认导入？', parent=self):
            self.status.set('已取消导入，文案库未变更。')
            return
        added, duplicates = self.library.import_text(self._text(), profile_id=self.active_profile,spec_id=self.active_spec)
        self.query.set('')
        self.refresh()
        self.status.set(f'{self.script_names[self.active_profile]}已导入{len(added)}篇；重复跳过{len(duplicates)}篇。')

    def edit(self):
        script_id = self._selected()
        reset = self.reset_after_edit.get()
        self.library.edit(script_id, self._text(), reset_usage=reset, profile_id=self.active_profile)
        self._show_saved(script_id)
        self.status.set(f'{script_id}已更新；' + ('使用次数已重置。' if reset else '品类及使用历史保留。'))

    def enable(self, value):
        ids = self._selected_many()
        self.library.bulk_action(ids, 'enable' if value else 'disable', profile_id=self.active_profile)
        self.refresh()
        self.status.set(f'已批量处理{len(ids)}篇：' + ('启用。' if value else '停用。'))

    def delete(self):
        return ScriptToolsMixin.delete(self)

    def assign_profile(self):
        ids = self._selected_many()
        target = next((key for key, name in self.profile_names.items() if name == self.move_target.get()), None)
        if target is None or target == self.active_profile:
            raise VoiceError('请选择另一目标商品。')
        if not messagebox.askyesno('确认移动品类',
                f'将选中的{len(ids)}篇从{self.script_names[self.active_profile]}移到{self.script_names[target]}？\n'
                '请确认内容适用于目标商品。原文和使用历史保留，已冻结任务不改变。', parent=self):
            return
        self.library.bulk_action(ids, 'move', profile_id=self.active_profile, target_profile=target)
        self._set_editor(None, '')
        self.refresh()
        self.status.set(f'{len(ids)}篇已移到{self.script_names[target]}；历史记录保留。')

    def reset_usage(self):
        ids = self._selected_many()
        if not messagebox.askyesno('重置使用次数', f'将选中的{len(ids)}篇正式使用次数归零？', parent=self):
            return
        self.library.bulk_action(ids, 'reset', profile_id=self.active_profile)
        self.refresh()

    def destroy(self):
        if self._search_after:
            self.after_cancel(self._search_after)
            self._search_after = None
        super().destroy()
