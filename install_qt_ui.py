"""Install the pinned Qt wheels in the dedicated D-drive UI runtime.

Wheel extraction avoids Windows scanner locks during pip's temporary move.
Never edits global site-packages, user media, models, or running compositor jobs.
"""
from mac_paths import path as _portable_path
from pathlib import Path
import hashlib,os,subprocess,sys,time,zipfile
TARGET=Path(_portable_path(r'D:\GPTData\cache\assembler-ui\pyside6'))
DOWNLOADS=Path(_portable_path(r'D:\GPTData\downloads\assembler-qt'))
VERSION='6.10.2'

def install():
    environment=os.environ.copy();environment.update(PIP_CACHE_DIR=_portable_path(r'D:\GPTData\cache\pip'),TEMP=_portable_path(r'D:\GPTData\temp'),TMP=_portable_path(r'D:\GPTData\temp'))
    DOWNLOADS.mkdir(parents=True,exist_ok=True);TARGET.mkdir(parents=True,exist_ok=True)
    subprocess.run([sys.executable,'-m','pip','download','--only-binary=:all:','--no-deps','--dest',str(DOWNLOADS),f'PySide6_Essentials=={VERSION}',f'shiboken6=={VERSION}'],env=environment,check=True)
    target=TARGET.resolve()
    for package in ('pyside6_essentials','shiboken6'):
        matches=list(DOWNLOADS.glob(f'{package}-{VERSION}-*win_amd64.whl'))
        if len(matches)!=1:raise RuntimeError('未找到唯一的Windows Qt依赖包：'+package)
        with zipfile.ZipFile(matches[0]) as wheel:
            for entry in wheel.infolist():
                if entry.is_dir():continue
                destination=(target/entry.filename).resolve()
                if not destination.is_relative_to(target):raise RuntimeError('非法依赖路径')
                data=wheel.read(entry);destination.parent.mkdir(parents=True,exist_ok=True)
                if destination.is_file() and hashlib.sha256(destination.read_bytes()).digest()==hashlib.sha256(data).digest():continue
                for attempt in range(4):
                    try:destination.write_bytes(data);break
                    except PermissionError:
                        if attempt==3:raise
                        time.sleep(.25)
    # Verify in a fresh interpreter; this does not open a window.
    subprocess.run([sys.executable,'-c',f'import sys;sys.path.insert(0,{str(target)!r});from PySide6 import QtCore,QtGui,QtWidgets;print("Qt runtime ready:",QtCore.__version__)'],env=environment,check=True)

if __name__=='__main__':install()
