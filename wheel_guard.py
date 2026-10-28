"""Wheel scrolls the page; changing parameter values requires an explicit action."""


def install_qt(application):
    if getattr(application,'_parameter_wheel_guard',None) is not None:return
    from PySide6 import QtCore as C,QtGui as G,QtWidgets as W
    class Guard(C.QObject):
        def eventFilter(self,obj,event):
            if event.type()!=C.QEvent.Type.Wheel or not isinstance(obj,W.QWidget):return False
            widget=obj;protected=None
            while widget is not None:
                # Open choice lists and table scrollbars retain ordinary scrolling.
                if isinstance(widget,(W.QAbstractItemView,W.QScrollBar)):return False
                if isinstance(widget,(W.QComboBox,W.QAbstractSpinBox,W.QSlider,W.QDial)):
                    protected=widget;break
                widget=widget.parentWidget()
            if protected is None:return False
            ancestor=protected.parentWidget()
            while ancestor is not None:
                if isinstance(ancestor,W.QAbstractScrollArea):
                    target=ancestor.viewport()
                    local=target.mapFromGlobal(event.globalPosition().toPoint())
                    forwarded=G.QWheelEvent(C.QPointF(local),event.globalPosition(),event.pixelDelta(),
                        event.angleDelta(),event.buttons(),event.modifiers(),event.phase(),event.inverted())
                    W.QApplication.sendEvent(target,forwarded)
                    break
                ancestor=ancestor.parentWidget()
            event.accept();return True
    guard=Guard(application);application.installEventFilter(guard);application._parameter_wheel_guard=guard


def install_tk(root):
    if getattr(root,'_parameter_wheel_guard',False):return
    root._parameter_wheel_guard=True
    tag='ParameterWheelGuard'
    classes={'TCombobox','Spinbox','TSpinbox','Scale','TScale'}
    def wheel(event):
        widget=event.widget
        while widget is not None:
            if widget.winfo_class()=='Canvas':
                delta=getattr(event,'delta',0)
                step=(-1 if delta>0 else 1) if delta else (-1 if getattr(event,'num',0)==4 else 1)
                widget.yview_scroll(step,'units');break
            widget=getattr(widget,'master',None)
        return 'break'
    def attach(widget):
        if not hasattr(widget,'winfo_class'):return
        if widget.winfo_class() in classes and tag not in widget.bindtags():
            widget.bindtags((tag,*widget.bindtags()))
    for sequence in ('<MouseWheel>','<Button-4>','<Button-5>'):root.bind_class(tag,sequence,wheel)
    root.bind_all('<Map>',lambda event:attach(event.widget),add='+')
    def visit(widget):
        attach(widget)
        for child in widget.winfo_children():visit(child)
    visit(root)
