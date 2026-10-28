"""Local scrolling for the long composition page, without global wheel bindings."""
import tkinter as tk
from tkinter import ttk


class ScrollablePage(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas=tk.Canvas(self,highlightthickness=0,bg='#F4F6F8')
        y=ttk.Scrollbar(self,command=self.canvas.yview);self.y_scroll=y
        x=ttk.Scrollbar(self,orient='horizontal',command=self.canvas.xview);self.x_scroll=x
        self.canvas.configure(yscrollcommand=y.set,xscrollcommand=x.set)
        self.canvas.grid(row=0,column=0,sticky='nsew');y.grid(row=0,column=1,sticky='ns')
        x.grid(row=1,column=0,sticky='ew')
        self.rowconfigure(0,weight=1);self.columnconfigure(0,weight=1)
        self.body=ttk.Frame(self.canvas)
        self.window=self.canvas.create_window(0,0,window=self.body,anchor='nw')
        self.body.bind('<Configure>',lambda _:self.layout())
        self.canvas.bind('<Configure>',lambda _:self.layout())
        self._wheel_after=self.after_idle(self.bind_wheel)

    def layout(self):
        self.canvas.itemconfigure(self.window,width=max(self.canvas.winfo_width(),self.body.winfo_reqwidth()))
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))
        for bar,needed in ((self.x_scroll,self.body.winfo_reqwidth()>self.canvas.winfo_width()+1),(self.y_scroll,self.body.winfo_reqheight()>self.canvas.winfo_height()+1)):
            if needed and not bar.winfo_manager():bar.grid()
            elif not needed and bar.winfo_manager():bar.grid_remove()

    def bind_wheel(self):
        self._wheel_after=None
        def wheel(event):
            self.canvas.yview_scroll(int(-event.delta/120),'units')
            return 'break'
        def visit(widget):
            if widget.winfo_class() not in ('Text','Treeview','Listbox','TCombobox','Canvas','TSpinbox'):
                widget.bind('<MouseWheel>',wheel,add='+')
            for child in widget.winfo_children():visit(child)
        visit(self.body)

    def destroy(self):
        if self._wheel_after is not None:
            self.after_cancel(self._wheel_after)
            self._wheel_after=None
        super().destroy()
