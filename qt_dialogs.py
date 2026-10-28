"""Native modal dialogs, preserving the established Tk return-value contracts."""
from mac_paths import path as _portable_path
from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox,simpledialog,colorchooser
from PySide6 import QtWidgets as W,QtGui as G

class DialogBridge:
    def __init__(self,parent):
        self.parent=parent;self.previous=[]
        # Fail safely on an unaudited legacy popup route. All supported editor
        # routes use a native Qt window or the isolated single-Tk host.
        original_toplevel=tk.Toplevel.__init__
        def toplevel(window,master=None,*args,**kwargs):
            owner=master if master is not None else tk._default_root
            root=owner._root() if isinstance(owner,tk.Misc) else None
            if root is parent.controller:
                raise RuntimeError('此窗口尚未接入独立编辑界面，已安全阻止；工作台及生成任务继续。')
            original_toplevel(window,master,*args,**kwargs)
        self.previous.append((tk.Toplevel,'__init__',original_toplevel));tk.Toplevel.__init__=toplevel
        # A transient of a withdrawn Tk owner is unmapped on Windows. The Qt
        # workbench owns the visible main window, so legacy editors need an
        # independent Tk window when their requested owner is hidden.
        original=tk.Wm.wm_transient
        def transient(window,master=None):
            if master is not None and str(master):
                try:
                    owner=master if isinstance(master,tk.Misc) else window.nametowidget(str(master))
                    if getattr(window._root(),'_qt_parent',None) is not None and not owner.winfo_viewable():master=''
                except (tk.TclError,KeyError,AttributeError):pass
            return original(window,master)
        for name in ('wm_transient','transient'):
            self.previous.append((tk.Wm,name,getattr(tk.Wm,name)));setattr(tk.Wm,name,transient)
        for module,names,handler in (
            (messagebox,('showinfo','showwarning','showerror','askyesno','askyesnocancel','askokcancel','askretrycancel','askquestion'),self.message),
            (filedialog,('askopenfilename','askopenfilenames','asksaveasfilename','askdirectory'),self.file),
            (simpledialog,('askstring','askinteger','askfloat'),self.input)):
            for name in names:
                self.previous.append((module,name,getattr(module,name)))
                setattr(module,name,lambda *args,_name=name,_handler=handler,**kwargs:_handler(_name,*args,**kwargs))
        self.previous.append((colorchooser,'askcolor',colorchooser.askcolor));colorchooser.askcolor=self.color
    def close(self):
        for module,name,previous in reversed(self.previous):setattr(module,name,previous)
        self.previous.clear()
    def message(self,kind,title=None,message=None,**kwargs):
        title=str(title or '整段组合器');message=str(message or '')
        parent=getattr(kwargs.get('parent'),'_qt_dialog_parent',None) or self.parent
        if kind in ('showinfo','showwarning','showerror'):
            method={'showinfo':W.QMessageBox.information,'showwarning':W.QMessageBox.warning,'showerror':W.QMessageBox.critical}[kind]
            method(parent,title,message);return 'ok'
        buttons=W.QMessageBox.StandardButton
        if kind in ('askyesno','askquestion','askyesnocancel'):
            choices=buttons.Yes|buttons.No
            if kind=='askyesnocancel':choices|=buttons.Cancel
            result=W.QMessageBox.question(parent,title,message,choices,buttons.No)
            if result==buttons.Cancel:return None
            yes=result==buttons.Yes;return ('yes' if yes else 'no') if kind=='askquestion' else yes
        positive=buttons.Retry if kind=='askretrycancel' else buttons.Ok
        result=W.QMessageBox.question(parent,title,message,positive|buttons.Cancel,buttons.Cancel);return result==positive
    def file(self,kind,**kwargs):
        directory=str(Path(kwargs.get('initialdir') or _portable_path(r'D:\DY video'))/str(kwargs.get('initialfile') or ''))
        title=kwargs.get('title') or ('选择目录' if kind=='askdirectory' else '选择文件')
        filters=';;'.join(str(label)+' ('+' '.join(pattern if isinstance(pattern,(tuple,list)) else [pattern])+')' for label,pattern in kwargs.get('filetypes',[]))
        if kind=='askdirectory':return W.QFileDialog.getExistingDirectory(self.parent,title,directory)
        if kind=='askopenfilenames':return tuple(W.QFileDialog.getOpenFileNames(self.parent,title,directory,filters)[0])
        if kind=='asksaveasfilename':
            path=W.QFileDialog.getSaveFileName(self.parent,title,directory,filters)[0];extension=kwargs.get('defaultextension')
            if path and extension and not Path(path).suffix:path+=extension
            return path
        return W.QFileDialog.getOpenFileName(self.parent,title,directory,filters)[0]
    def input(self,kind,title,prompt,**kwargs):
        if kind=='askinteger':value,ok=W.QInputDialog.getInt(self.parent,title,prompt,int(kwargs.get('initialvalue') or 0),int(kwargs.get('minvalue',-2147483647)),int(kwargs.get('maxvalue',2147483647)))
        elif kind=='askfloat':value,ok=W.QInputDialog.getDouble(self.parent,title,prompt,float(kwargs.get('initialvalue') or 0),float(kwargs.get('minvalue',-1e9)),float(kwargs.get('maxvalue',1e9)),6)
        else:value,ok=W.QInputDialog.getText(self.parent,title,prompt,W.QLineEdit.EchoMode.Password if kwargs.get('show')=='*' else W.QLineEdit.EchoMode.Normal,str(kwargs.get('initialvalue') or ''))
        return value if ok else None
    def color(self,color=None,**kwargs):
        selected=W.QColorDialog.getColor(G.QColor(color or '#ffffff'),self.parent,kwargs.get('title','选择颜色'))
        return ((selected.red(),selected.green(),selected.blue()),selected.name()) if selected.isValid() else (None,None)
