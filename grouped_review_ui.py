"""Group-level human review, paged exceptions and independent cut permissions."""
import tkinter as tk
from tkinter import ttk, messagebox

from grouped_materials import Coverage
from paged_tree import PagedTree


LABELS = {'overlap': '重复', 'clear': '不重复', 'pending': '待确认'}


def scope_key(row):
    return tuple(sorted({row['left'].split('/')[0], row['right'].split('/')[0]}))


def review_scopes(rows):
    scopes = {}
    for row in rows:
        scopes.setdefault(scope_key(row), []).append(row)
    return scopes


class GroupedReviewWindow(tk.Toplevel):
    def __init__(self, app, clips, root):
        super().__init__(app)
        self.app, self.clips = app, clips
        self.coverage = Coverage(app.store, root)
        self.rows = []
        self.scope_rows = {}
        self.scope_ids = {}
        self.status = tk.StringVar(self, '')
        self.pair_scope = None
        self.title('G组素材复核 · 按组确认与例外管理')
        self.geometry('1160x780')
        self.minsize(980, 660)
        ttk.Label(self, text='按组确认一次命名规则，个别重复画面到“关系例外”修改。关系确认与安全裁切分别保存；新内容仍需复核。',
                  wraplength=1100).pack(fill='x', padx=12, pady=8)
        self.pages = ttk.Notebook(self)
        self.pages.pack(fill='both', expand=True, padx=10)
        group_page, pair_page, cut_page = [ttk.Frame(self.pages) for _ in range(3)]
        for page, label in [(group_page, '按组确认'), (pair_page, '关系例外 / 明细'), (cut_page, '安全裁切 / 开头排除')]:
            self.pages.add(page, text=label)
        self.pair_page = pair_page
        ttk.Label(group_page, text='组内：组合与其成员重复，其他不重叠片段可组合。组间：核对不同G组没有重复画面后，可一次确认整组之间的关系。',
                  wraplength=1100).pack(fill='x', padx=8, pady=6)
        self.groups = self.table(group_page, [('scope','确认范围',210), ('rule','范围规则',360),
            ('pairs','关系数',85), ('pending','待确认',85), ('exceptions','人工例外',85)])
        buttons = ttk.Frame(group_page); buttons.pack(fill='x', padx=8, pady=5)
        self.button(buttons, '按规则确认所选范围', self.confirm_scopes)
        self.button(buttons, '查看所选范围明细', self.show_scope)
        self.details = tk.StringVar(self, '')
        ttk.Label(group_page, textvariable=self.details, wraplength=1100).pack(fill='x', padx=8, pady=5)
        self.groups.bind('<<TreeviewSelect>>', lambda _: self.group_details())

        filters = ttk.Frame(pair_page); filters.pack(fill='x', padx=8, pady=6)
        self.pair_filter = tk.StringVar(self, '待确认')
        ttk.Label(filters, text='状态').pack(side='left')
        choice = ttk.Combobox(filters, textvariable=self.pair_filter,
                             values=('全部','待确认','重复','不重复','仅人工例外'), width=13, state='readonly')
        choice.pack(side='left', padx=4)
        choice.bind('<<ComboboxSelected>>', lambda _: self.refresh_pairs())
        self.button(filters, '显示全部范围', self.all_scopes)
        self.scope_note = tk.StringVar(self, '当前范围：全部')
        ttk.Label(filters, textvariable=self.scope_note).pack(side='left', padx=10)
        self.pairs = self.table(pair_page, [('left','片段一',330), ('right','片段二',330),
                                              ('suggestion','命名建议',110), ('state','人工状态',110)])
        buttons = ttk.Frame(pair_page); buttons.pack(fill='x', padx=8, pady=5)
        for label, state in [('选中设为重复','overlap'),('选中设为不重复','clear'),('选中恢复待确认','pending')]:
            self.button(buttons, label, lambda state=state: self.confirm_pairs(state))
        self.button(buttons, '播放片段一', lambda: self.play_pair('left'))
        self.button(buttons, '播放片段二', lambda: self.play_pair('right'))

        ttk.Label(cut_page, text='“不重复”只决定能否组合。“允许安全裁切”另行授权缩短素材；完整动作尚未核对的文件请保持完整。',
                  wraplength=1100).pack(fill='x', padx=8, pady=6)
        self.assets = self.table(cut_page, [('path','完整相对路径',440), ('kind','镜头类型',90), ('cut','可安全裁切',190), ('opening','开头选片',150)])
        buttons = ttk.Frame(cut_page); buttons.pack(fill='x', padx=8, pady=5)
        self.button(buttons, '选中镜头允许安全裁切', lambda: self.cut(True))
        self.button(buttons, '选中镜头保持完整', lambda: self.cut(False))
        self.button(buttons, '选中禁止作开头', lambda: self.opening(True))
        self.button(buttons, '选中恢复可作开头', lambda: self.opening(False))
        self.button(buttons, '播放选中镜头', self.play_asset)
        ttk.Label(self, textvariable=self.status, wraplength=1100).pack(fill='x', padx=12, pady=7)
        if getattr(app, 'scan_warnings', None):
            warnings = tk.Text(self, height=3, wrap='word')
            warnings.pack(fill='x', padx=10, pady=(0, 8))
            warnings.insert('1.0', '异常文件不参与选片：\n'+'\n'.join(app.scan_warnings))
            warnings.configure(state='disabled')
        self.refresh()

    def button(self, parent, label, action):
        ttk.Button(parent, text=label, command=lambda: self.guard(action)).pack(side='left', padx=3)

    def guard(self, action):
        try:
            action()
        except Exception as exc:
            self.status.set(str(exc))
            messagebox.showerror('素材复核', str(exc), parent=self)

    def table(self, parent, columns):
        frame = ttk.Frame(parent); frame.pack(fill='both', expand=True, padx=8, pady=5)
        tree = PagedTree(frame, columns=[c[0] for c in columns], show='headings', height=12)
        for key, label, width in columns:
            tree.heading(key, text=label); tree.column(key, width=width, minwidth=65)
        scroll = ttk.Scrollbar(frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky='nsew'); scroll.grid(row=0, column=1, sticky='ns')
        x = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(xscrollcommand=x.set); x.grid(row=1, column=0, sticky='ew')
        tree.controls(frame).grid(row=2, column=0, columnspan=2, sticky='ew', pady=5)
        frame.columnconfigure(0, weight=1); frame.rowconfigure(0, weight=1)
        return tree

    def refresh(self):
        self.rows = self.coverage.rows(self.clips)
        self.scope_rows = review_scopes(self.rows)
        self.groups.delete(*self.groups.get_children())
        self.scope_ids = {}
        for number, (scope, rows) in enumerate(sorted(self.scope_rows.items())):
            iid = str(number); self.scope_ids[iid] = scope
            self.groups.insert('', 'end', iid=iid, values=(
                '组内 '+scope[0] if len(scope)==1 else ' ↔ '.join(scope),
                '组合覆盖自己的成员；其余核对后可组合' if len(scope)==1 else '核对不同组画面不重复',
                len(rows), sum(r['state']=='pending' for r in rows),
                sum(r['state'] not in ('pending',r['suggestion']) for r in rows)))
        self.refresh_pairs(); self.refresh_assets()
        self.status.set(f'{len(self.clips)}个文件 → {len(self.scope_rows)}个确认范围；'
                        f'{sum(r["state"]=="pending" for r in self.rows)}对待确认。批量确认只写待确认项，保留已有人工例外。')

    def selected_scope_rows(self):
        return [r for iid in self.groups.selection() for r in self.scope_rows[self.scope_ids[iid]]]

    def confirm_scopes(self):
        pending = [r for r in self.selected_scope_rows() if r['state']=='pending']
        if not pending:
            self.status.set('所选范围没有待确认关系；已有人工状态保持。'); return
        if messagebox.askyesno('按组确认关系', f'选中{len(self.groups.selection())}个范围，包含{len(pending)}对待确认关系。\n'
                '确认已核对组内组合覆盖关系，以及所选组间画面不重复？\n'
                '只按命名规则填写待确认项；人工例外保留，不开放安全裁切。', parent=self):
            self.coverage.confirm(pending)
            self.refresh()

    def show_scope(self):
        self.pair_scope = {self.scope_ids[i] for i in self.groups.selection()}
        if not self.pair_scope:
            raise ValueError('请先选择确认范围')
        self.pair_filter.set('全部'); self.refresh_pairs(); self.pages.select(self.pair_page)

    def all_scopes(self):
        self.pair_scope = None; self.refresh_pairs()

    def refresh_pairs(self):
        rows = [r for r in self.rows if self.pair_scope is None or scope_key(r) in self.pair_scope]
        state = self.pair_filter.get()
        if state == '仅人工例外':
            rows = [r for r in rows if r['state'] not in ('pending',r['suggestion'])]
        elif state != '全部':
            rows = [r for r in rows if LABELS[r['state']] == state]
        self.pairs.delete(*self.pairs.get_children())
        for r in rows:
            self.pairs.insert('', 'end', iid=r['pair_key'], values=(r['left'],r['right'],LABELS[r['suggestion']],LABELS[r['state']]))
        self.scope_note.set('当前范围：'+('全部' if self.pair_scope is None else '；'.join(' ↔ '.join(s) for s in sorted(self.pair_scope))))

    def confirm_pairs(self, state):
        selected = set(self.pairs.selection())
        rows = [r for r in self.rows if r['pair_key'] in selected]
        if not rows:
            raise ValueError('请先选择关系，可全选本页或筛选结果')
        self.coverage.confirm(rows, state); self.refresh()

    def refresh_assets(self):
        self.coverage.annotate(self.clips)
        self.assets.delete(*self.assets.get_children())
        for c in self.clips:
            self.assets.insert('', 'end', iid=c['relative_path'], values=(c['relative_path'],
                '组合' if c['asset_kind']=='combo' else '独立', '允许' if c['safe_cut_confirmed'] else '保持完整动作',
                '禁止作开头' if c.get('opening_excluded') else '可作开头'))

    def opening(self,excluded):
        selected=set(self.assets.selection());clips=[c for c in self.clips if c['relative_path'] in selected]
        if not clips:raise ValueError('请先选择镜头，可多选或全选筛选结果')
        self.coverage.exclude_opening(clips,excluded);self.refresh_assets()
        self.status.set(f'已更新{len(clips)}个镜头：'+('禁止作开头' if excluded else '可作开头')+'；仍可用于中后段，仅影响牛肉丸新方案，已冻结批次保持。')

    def cut(self, enabled):
        selected = set(self.assets.selection())
        clips = [c for c in self.clips if c['relative_path'] in selected]
        if not clips:
            raise ValueError('请先选择镜头')
        if enabled and not messagebox.askyesno('确认安全裁切', f'允许缩短选中的{len(clips)}个镜头？\n请确认不会截坏完整动作。原素材文件保持。', parent=self):
            return
        self.coverage.safe_cut(clips, enabled); self.refresh_assets()
        self.status.set(f'已更新{len(clips)}个镜头的安全裁切许可；已冻结批次保持原许可。')

    def group_details(self):
        scopes = [self.scope_ids[i] for i in self.groups.selection()]
        groups = {g for scope in scopes for g in scope}
        combos = {}
        for c in self.clips:
            if c['source_group'] in groups:
                combos.setdefault(c['combo_id'], []).append(c['relative_path'].split('/')[-1])
        self.details.set('命名结构：'+'；'.join(k+': '+', '.join(v) for k,v in combos.items()))

    def play(self, relative):
        from core import open_local
        path = next(c['path'] for c in self.clips if c['relative_path']==relative)
        open_local(path)

    def play_pair(self, side):
        selected = self.pairs.selection()
        if len(selected)!=1:
            raise ValueError('播放时请选择一对关系')
        self.play(next(r for r in self.rows if r['pair_key']==selected[0])[side])

    def play_asset(self):
        selected = self.assets.selection()
        if len(selected)!=1:
            raise ValueError('播放时请选择一个镜头')
        self.play(selected[0])
