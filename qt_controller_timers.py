"""Qt owns timers for the hidden Tcl state controller; never pump Tk events.

Calling Tcl variables/commands from the GUI thread is supported. Dispatching
Windows Qt callbacks from inside Tcl's native event pump is not required.
Visible Tk editors have their own process and retain their original timers.
"""
import tkinter as tk
import uuid
import gc
from qt_runtime import activate
activate()
from PySide6 import QtCore as C

_after=tk.Misc.after
_idle=tk.Misc.after_idle
_cancel=tk.Misc.after_cancel
_update=tk.Misc.update
_update_idle=tk.Misc.update_idletasks
_owners={}


def owner(widget):
    try:return _owners.get(id(widget._root()))
    except (AttributeError,tk.TclError):return None


def after(widget,ms,func=None,*args):
    bridge=owner(widget)
    if bridge and func is not None:return bridge.schedule(widget,ms,func,args)
    return _after(widget,ms,func,*args)


def idle(widget,func,*args):
    bridge=owner(widget)
    if bridge:return bridge.schedule(widget,0,func,args)
    return _idle(widget,func,*args)


def cancel(widget,key):
    bridge=owner(widget)
    if bridge and (str(key).startswith('qt_after_') or key in bridge.aliases):bridge.cancel(key);return
    return _cancel(widget,key)


def update(widget):
    bridge=owner(widget)
    if bridge:
        # A caller's legacy update() must never enter Tcl's Windows event loop.
        if hasattr(bridge,'window'):bridge.window.presenter.sync()
        return
    return _update(widget)


def update_idle(widget):
    if owner(widget):return
    return _update_idle(widget)


def install():
    tk.Misc.after=after;tk.Misc.after_idle=idle;tk.Misc.after_cancel=cancel
    tk.Misc.update=update;tk.Misc.update_idletasks=update_idle


def release(root,enabled):
    _owners.pop(id(root),None)
    if not _owners:
        tk.Misc.after=_after;tk.Misc.after_idle=_idle;tk.Misc.after_cancel=_cancel
        tk.Misc.update=_update;tk.Misc.update_idletasks=_update_idle
        if enabled:gc.enable()


class PendingTimers:
    """Capture Python callbacks and their exact delays before Qt presentation."""
    def __init__(self,root):
        self.root=root;self.pending={};self.aliases={};self.gc_was_enabled=gc.isenabled();gc.disable();install()
    def schedule(self,widget,ms,func,args):
        key='qt_after_'+uuid.uuid4().hex;self.pending[key]=(widget,ms,func,args);return key
    def cancel(self,key):self.pending.pop(key,None)
    def close(self):self.pending.clear();release(self.root,self.gc_was_enabled)


def prepare(root):
    bridge=PendingTimers(root);_owners[id(root)]=bridge;root._qt_timer_bridge=bridge


class ControllerTimers(C.QObject):
    def __init__(self,window):
        super().__init__(window);self.window=window;self.root=window.controller;self.pending={};self.aliases={}
        previous=_owners.get(id(self.root));initial=dict(previous.pending) if isinstance(previous,PendingTimers) else {}
        self.closed=False;self.initial_delays=[row[1] for row in initial.values()]
        self.gc_was_enabled=previous.gc_was_enabled if previous else gc.isenabled();gc.disable()
        _owners[id(self.root)]=self;self.root._qt_timer_bridge=self
        self.gc_timer=C.QTimer(self);self.gc_timer.setInterval(5000);self.gc_timer.timeout.connect(gc.collect);self.gc_timer.start()
        install()
        # Original Python callables and delays are preserved. No Tcl eval or
        # event-loop migration is needed, including the 1000ms retention timer.
        for key,(widget,ms,func,args) in initial.items():self.schedule(widget,ms,func,args,key=key)
        if previous:previous.pending.clear()
    def schedule(self,widget,ms,func,args,*,key=None):
        key=key or 'qt_after_'+uuid.uuid4().hex;timer=C.QTimer(self);timer.setSingleShot(True);self.pending[key]=timer
        def fire():
            self.pending.pop(key,None);timer.deleteLater()
            if getattr(self.root,'_destroyed',False):return
            try:
                if widget.winfo_exists():func(*args)
            except Exception:
                import sys
                self.root.report_callback_exception(*sys.exc_info())
        timer.timeout.connect(fire);timer.start(max(0,int(ms)));return key
    def cancel(self,key):
        key=self.aliases.pop(key,key);timer=self.pending.pop(key,None)
        if timer:timer.stop();timer.deleteLater()
    def close(self):
        if self.closed:return
        self.closed=True
        self.gc_timer.stop()
        for key in list(self.pending):self.cancel(key)
        release(self.root,self.gc_was_enabled)
