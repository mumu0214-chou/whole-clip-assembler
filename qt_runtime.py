"""Resolve the dedicated D-drive Qt runtime without changing global Python."""
from mac_paths import path as _portable_path
from pathlib import Path
import os,sys

RUNTIME=Path(os.environ.get('ASSEMBLER_QT_RUNTIME',_portable_path(r'D:\GPTData\cache\assembler-ui\pyside6')))

def activate():
    if RUNTIME.is_dir() and str(RUNTIME) not in sys.path:sys.path.insert(0,str(RUNTIME))
    try:import PySide6
    except ImportError as exc:
        raise RuntimeError('Qt界面依赖未就绪。Mac 请运行 bash setup_mac.sh，或使用 bash start_mac.sh --tk。') from exc
    return PySide6.__version__
