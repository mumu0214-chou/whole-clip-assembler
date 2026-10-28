"""Portable locations for the assembler; never relocate or delete user files."""
from pathlib import Path, PureWindowsPath
import os
import sys

PROJECT = Path(__file__).resolve().parent
HOME = Path(os.environ.get('ASSEMBLER_HOME', Path.home() / 'Movies' / 'WholeClipAssembler')).expanduser()
RUNTIME = Path(os.environ.get('ASSEMBLER_RUNTIME', HOME / 'runtime')).expanduser()
TEMP = Path(os.environ.get('ASSEMBLER_TEMP', HOME / 'temp')).expanduser()

def path(original, platform=None):
    if (platform or os.name) == 'nt':
        return original
    value = str(original).replace('/', '\\')
    low = value.lower()
    if low == r'd:\gptdata\venvs\indextts25\scripts\python.exe':
        return str(RUNTIME / 'index-tts' / '.venv' / 'bin' / 'python')
    if low == r'd:\gptdata\asr-projects\asr_20260907\.venv\scripts\python.exe':
        return str(RUNTIME / 'asr' / 'bin' / 'python')
    if low.startswith(r'd:\gptdata\asr-projects\asr_20260907\.models' + '\\'):
        return str(RUNTIME / 'models' / PureWindowsPath(value).name)
    if low.startswith(r'd:\dy video\整段组合器_v1.0.5' + '\\'):
        return str(PROJECT.joinpath(*PureWindowsPath(value).parts[3:]))
    if low == r'd:\dy video\本地配音\indextts-2.5\index-tts':
        return str(RUNTIME / 'index-tts')
    if low.startswith(r'd:\dy video\本地配音\indextts-2.5\index-tts' + '\\'):
        return str(RUNTIME.joinpath('index-tts', *PureWindowsPath(value).parts[5:]))
    if low == r'd:\dy video' or low.startswith(r'd:\dy video' + '\\'):
        return str(HOME.joinpath(*PureWindowsPath(value).parts[2:]))
    if low == r'd:\gptdata\temp' or low.startswith(r'd:\gptdata\temp' + '\\'):
        return str(TEMP.joinpath(*PureWindowsPath(value).parts[3:]))
    if low == r'd:\gptdata' or low.startswith(r'd:\gptdata' + '\\'):
        return str(RUNTIME.joinpath(*PureWindowsPath(value).parts[2:]))
    if low.startswith(r'c:\windows\fonts' + '\\'):
        bold = 'bd.' in low
        candidates = ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/STHeiti Medium.ttc'] if bold else ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/STHeiti Light.ttc']
        return next((x for x in candidates if Path(x).is_file()), candidates[0])
    return original
