"""Resolve the dedicated D-drive Qt runtime without changing global Python."""
from mac_paths import path as _portable_path
from pathlib import Path
import os,sys

RUNTIME=Path(os.environ.get('ASSEMBLER_QT_RUNTIME',_portable_path(r'D:\GPTData\cache\assembler-ui\pyside6')))

def activate():
    if RUNTIME.is_dir() and str(RUNTIME) not in sys.path:sys.path.insert(0,str(RUNTIME))
    try:import PySide6
    except ImportError as exc:
        raise RuntimeError('Qt界面依赖未就绪。请运行“安装Qt界面依赖.bat”；或运行“启动兼容界面.bat”。') from exc
    return PySide6.__version__
