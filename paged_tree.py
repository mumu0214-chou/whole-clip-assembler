"""Flat Tk tables with stable identities, page rendering and scoped bulk selection."""
import tkinter as tk
from tkinter import ttk


class PagedTree(ttk.Treeview):
    """Keep the existing Treeview API; only current-page rows occupy Tk widgets."""
    def __init__(self, parent, *, page_size=50, **kwargs):
        kwargs['selectmode'] = 'extended'
        super().__init__(parent, **kwargs)
        self.records = {}
        self.order = []
        self.chosen = set()
        self.page = 0
        self.page_size = page_size
        self.query = tk.StringVar(self, '')
        self.page_label = tk.StringVar(self, '')
        self.size_var = tk.StringVar(self, str(page_size))
        self._filtered = None
        self._after = None
        self._settle = None
        self._rendering = False
        self._shown = ()
        self._serial = 0
        self.query.trace_add('write', self._search)
        self.bind('<<TreeviewSelect>>', lambda _: None)
        self.bind('<Control-a>', lambda _: (self.select_all(), 'break')[1])

    def bind(self, sequence=None, func=None, add=None):
        if sequence and func is not None:
            if not hasattr(self,'_external_bindings'):self._external_bindings={}
            self._external_bindings[sequence]=func
        if sequence == '<<TreeviewSelect>>' and func is not None:
            callback = func
            def func(event):
                if getattr(self,'_external_view',None):return callback(event)
                if self._rendering:
                    return
                shown = set(self._shown)
                self.chosen.difference_update(shown)
                self.chosen.update(super(PagedTree, self).selection())
                self._label()
                return callback(event)
        return super().bind(sequence, func, add)

    def controls(self, parent, *, search=True):
        bar = ttk.Frame(parent)
        summary = ttk.Frame(bar); summary.pack(fill='x', pady=(0, 3))
        actions = ttk.Frame(bar); actions.pack(fill='x')
        if search:
            ttk.Label(summary, text='筛选').pack(side='left')
            ttk.Entry(summary, textvariable=self.query, width=17).pack(side='left', padx=3)
        for label, action in [('上一页', lambda: self.go(self.page-1)),
                              ('下一页', lambda: self.go(self.page+1)),
                              ('全选本页', self.select_page),
                              ('全选筛选结果', self.select_all),
                              ('取消全选', self.clear_selection)]:
            ttk.Button(actions, text=label, command=action).pack(side='left', padx=2)
        ttk.Label(summary, text='每页').pack(side='left', padx=(5, 1))
        size = ttk.Combobox(summary, textvariable=self.size_var, values=(20, 50, 100, 200),
                            width=4, state='readonly')
        size.pack(side='left')
        size.bind('<<ComboboxSelected>>', lambda _: self.set_page_size(int(self.size_var.get())))
        ttk.Label(summary, textvariable=self.page_label).pack(side='left', padx=6)
        self._label()
        return bar

    @staticmethod
    def _ids(items):
        if len(items) == 1 and isinstance(items[0], (tuple, list, set)):
            return tuple(str(i) for i in items[0])
        return tuple(str(i) for i in items)

    def filtered_ids(self):
        if self._filtered is None:
            query = self.query.get().strip().casefold()
            self._filtered = tuple(i for i in self.order if not query or query in
                ' '.join(str(v) for v in self.records[i].get('values', ())).casefold())
        return self._filtered

    def visible_ids(self):
        ids = self.filtered_ids()
        return ids[self.page*self.page_size:(self.page+1)*self.page_size]

    def _search(self, *_):
        self.page = 0
        self._filtered = None
        # A changed filter cannot leave invisible rows selected for a bulk action.
        self.chosen.intersection_update(self.filtered_ids())
        self._schedule()

    def _schedule(self):
        self._filtered = None
        if getattr(self,'_external_view',None):
            self._external_view()
            return
        if self._after is None:
            self._after = self.after_idle(self.flush)

    def _label(self):
        count = len(self.filtered_ids())
        pages = max(1, (count+self.page_size-1)//self.page_size)
        self.page_label.set(f'{self.page+1}/{pages}页 · {count}条 · 已选{len(self.chosen)}条')

    def flush(self):
        if getattr(self,'_external_view',None):
            self._filtered=None
            self.chosen.intersection_update(self.filtered_ids())
            self._label();self._external_view()
            return
        if self._after is not None:
            self.after_cancel(self._after)
            self._after = None
        ids = self.filtered_ids()
        self.chosen.intersection_update(ids)
        self.page = min(self.page, max(0, (len(ids)-1)//self.page_size))
        self._rendering = True
        if self._settle is not None:
            self.after_cancel(self._settle)
        children = super().get_children()
        if children:
            super().delete(*children)
        self._shown = self.visible_ids()
        for iid in self._shown:
            super().insert('', 'end', iid=iid, **self.records[iid])
        selected = [i for i in self._shown if i in self.chosen]
        if selected:
            super().selection_set(selected)
        self._label()
        self._settle = self.after_idle(self._settled)

    def _settled(self):
        self._rendering = False
        self._settle = None

    def go(self, page):
        self.page = max(0, min(page, max(0, (len(self.filtered_ids())-1)//self.page_size)))
        self.flush()

    def set_page_size(self, size):
        self.page_size = max(1, int(size))
        self.size_var.set(str(self.page_size))
        self.go(0)

    def select_page(self):
        self.selection_add(self.visible_ids())

    def select_all(self):
        self.selection_set(self.filtered_ids())

    def clear_selection(self):
        self.selection_set(())

    def insert(self, parent, index, iid=None, **kwargs):
        if parent:
            raise ValueError('分页表仅支持平面列表')
        if iid is None:
            self._serial += 1
            iid = f'paged_{self._serial}'
        iid = str(iid)
        if iid in self.records:
            raise ValueError(f'重复条目身份：{iid}')
        self.records[iid] = dict(kwargs)
        if index == 'end':
            self.order.append(iid)
        else:
            self.order.insert(int(index), iid)
        self._schedule()
        return iid

    def delete(self, *items):
        ids = set(self._ids(items))
        for iid in ids:
            self.records.pop(iid, None)
        self.order = [i for i in self.order if i not in ids]
        self.chosen.difference_update(ids)
        self._schedule()

    def get_children(self, item=None):
        return tuple(self.order) if not item else ()

    def exists(self, item):
        return str(item) in self.records

    def item(self, item, option=None, **kwargs):
        record = self.records[str(item)]
        if kwargs:
            record.update(kwargs)
            if getattr(self,'_external_view',None) and not self.query.get().strip():self._external_view()
            else:self._schedule()
        if option:
            return record.get(option, ())
        return dict(record)

    def selection(self):
        return tuple(i for i in self.order if i in self.chosen)

    def selection_set(self, *items):
        self.chosen = set(self._ids(items)).intersection(self.filtered_ids())
        self.flush()

    def selection_add(self, *items):
        self.chosen.update(set(self._ids(items)).intersection(self.filtered_ids()))
        self.flush()

    def selection_remove(self, *items):
        self.chosen.difference_update(self._ids(items))
        self.flush()

    def see(self, item):
        ids = self.filtered_ids()
        if str(item) in ids:
            self.page = ids.index(str(item))//self.page_size
            self.flush()
            if not getattr(self,'_external_view',None):super().see(str(item))

    def destroy(self):
        for pending in (self._after, self._settle):
            if pending is not None:
                self.after_cancel(pending)
        super().destroy()
