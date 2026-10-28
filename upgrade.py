# -*- coding: utf-8 -*-
"""In-place code upgrade. Preserve user settings, databases, texts, media and dependencies."""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
import shutil
import sys
import uuid

CODE_FILES = [
    'app.py', 'core.py', 'cli.py', 'timeline_processing.py', 'verify_timeline_render.py', 'video_processing.py', 'setup_renderer.py', 'verify_render.py', 'verify_processing_render.py',
    'renderer/render.cjs', 'renderer/src/index.jsx',
    'tests/test_timeline_processing.py', 'tests/test_core.py', 'tests/test_media.py', 'tests/test_processing.py', 'tests/test_upgrade.py',
    '使用说明.md', '测试记录.md', 'README_FIRST.txt', 'START.bat', 'SELFTEST.bat', 'TIMELINE_SELFTEST.bat', 'upgrade.py', 'UPGRADE.bat',
]

def upgrade_into(source: Path, target: Path) -> Path:
    from core import AssemblyError, instance_lock
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise AssemblyError('当前已经是这份新版程序。请直接用START.bat启动。')
    if not (target/'core.py').is_file() or not (target/'app.py').is_file() or not (target/'renderer').is_dir():
        raise AssemblyError('请选择旧版组合器程序目录：里面需要有app.py、core.py和renderer文件夹。')
    old_text = (target/'core.py').read_text(encoding='utf-8-sig')
    if 'Whole-clip' not in old_text or 'def render_batch' not in old_text:
        raise AssemblyError('该目录不像此整段组合器，未写入任何文件。')
    # All files are verified before touching destination code.
    missing = [p for p in CODE_FILES if not (source/p).is_file()]
    if missing:
        raise AssemblyError('更新包不完整：' + ', '.join(missing))
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:6]
    backup=target/'data'/'upgrade_backups'/stamp
    with instance_lock(target/'data'):
        backup.mkdir(parents=True, exist_ok=False)
        records=[]
        for name in CODE_FILES:
            dst=target/name
            existed=dst.is_file()
            if dst.exists() and not existed:
                raise AssemblyError(f'目标不是普通文件，停止：{dst}')
            if existed:
                bp=backup/name;bp.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(dst,bp)
            records.append({'file':name,'existed':existed})
        (backup/'upgrade_record.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        copied=[]
        try:
            for record in records:
                name=record['file'];dst=target/name;dst.parent.mkdir(parents=True,exist_ok=True)
                copied.append(record)
                shutil.copy2(source/name,dst)
        except Exception:
            for record in reversed(copied):
                name=record['file'];dst=target/name
                if record['existed']:shutil.copy2(backup/name,dst)
                else:dst.unlink(missing_ok=True)
            raise
    return backup

def main():
    import tkinter as tk
    from tkinter import filedialog,messagebox
    root=tk.Tk();root.withdraw()
    try:
        messagebox.showinfo('保留设置升级',
            '请先关闭所有旧版组合器窗口和正在运行的任务。\n'
            '本工具仅更新代码，保留data、文案库、依赖和所有视频。\n'
            '接下来选择旧版程序所在文件夹，不要选择素材文件夹。',parent=root)
        folder=filedialog.askdirectory(title='选择旧版组合器程序文件夹',parent=root)
        if not folder:return
        if not messagebox.askyesno('确认升级',f'更新程序：\n{folder}\n\n先备份旧代码，不修改素材和设置。继续？',parent=root):return
        backup=upgrade_into(Path(__file__).resolve().parent,Path(folder))
        messagebox.showinfo('升级完成',f'旧设置及数据已保留。\n代码备份：{backup}\n\n回到旧程序目录，双击START.bat启动新版。',parent=root)
    except Exception as exc:
        messagebox.showerror('升级未完成',str(exc),parent=root);raise
    finally:root.destroy()

if __name__=='__main__':main()
