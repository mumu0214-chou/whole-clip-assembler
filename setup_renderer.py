# -*- coding: utf-8 -*-
"""Optional first-run dependency installation. Does not modify the existing DY project."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent

def main():
    npm = shutil.which('npm') or shutil.which('npm.cmd')
    if not npm and os.name == 'nt':
        candidate = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'nodejs' / 'npm.cmd'
        if candidate.exists():
            npm = str(candidate)
    if not npm:
        print('未找到npm。请先安装Node.js（含npm），或在软件环境设置中选择你原项目的Remotion工程。')
        return 1
    print('将联网安装此工具的Remotion依赖；不会修改原项目已有Remotion工程。')
    print('已经有可用Remotion工程时，不必安装：在软件「环境设置」选择已有工程即可。')
    print('默认首次渲染可能还会下载Remotion所用的浏览器；也可在环境设置指定本地Chrome。')
    result = subprocess.run([npm, 'install', '--no-audit', '--no-fund'], cwd=ROOT / 'renderer')
    if result.returncode:
        print('安装失败，请检查网络或配置已有Remotion工程。')
        return result.returncode
    print('安装完成。重开整段组合器，点击环境检查。')
    return 0

if __name__ == '__main__':
    code = main()
    if sys.stdin.isatty():
        input('按回车退出……')
    raise SystemExit(code)
