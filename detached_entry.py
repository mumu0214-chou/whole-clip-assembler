"""Hidden WMI-launched entry: own log handles and verify no executor Job Object."""
from mac_paths import path as _portable_path
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import runpy
import sys

parser = argparse.ArgumentParser()
parser.add_argument('--kind', choices=('production', 'tts'), required=True)
parser.add_argument('--batch', type=Path)
parser.add_argument('--log', type=Path, required=True)
parser.add_argument('--record', type=Path, required=True)
args = parser.parse_args()
os.environ.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8',
                  TEMP=_portable_path(r'D:\GPTData\temp'), TMP=_portable_path(r'D:\GPTData\temp'))
os.environ['PATH'] = _portable_path(r'D:\AAA\bin;D:\node.js;') + os.environ['PATH']
args.log.parent.mkdir(parents=True, exist_ok=True)
sys.stdout = args.log.open('a', encoding='utf-8', buffering=1)
sys.stderr = sys.stdout
sys.stdin = open(os.devnull, 'r')
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
flag = wintypes.BOOL()
if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(flag)):
    raise ctypes.WinError(ctypes.get_last_error())
parent_in_job = None
parent = kernel.OpenProcess(0x1000, False, os.getppid())
if parent:
    parent_flag = wintypes.BOOL()
    if kernel.IsProcessInJob(parent, None, ctypes.byref(parent_flag)):
        parent_in_job = bool(parent_flag.value)
    kernel.CloseHandle(parent)
# CPython's venv redirector creates its own child-lifetime job. Its WMI-launched
# parent must be outside the host job; production uses the direct interpreter.
independent = not flag.value or (args.kind == 'tts' and parent_in_job is False)
record = {'pid':os.getpid(), 'parent_pid':os.getppid(), 'is_process_in_job':bool(flag.value),
          'parent_is_process_in_job':parent_in_job, 'independent_of_host':independent,
          'launch_method':'Win32_Process.Create', 'kind':args.kind, 'log_path':str(args.log)}
args.record.parent.mkdir(parents=True, exist_ok=True)
args.record.write_text(json.dumps(record, indent=2), encoding='utf-8')
if not independent:
    raise RuntimeError('Worker still belongs to a host Job Object; refusing unsafe background launch')
print('INDEPENDENT_PROCESS '+json.dumps(record), flush=True)
app = Path(__file__).resolve().parent
if args.kind == 'production':
    if args.batch is None:
        raise ValueError('--batch is required for production')
    sys.argv = [str(app/'production_worker.py'), '--batch', str(args.batch)]
else:
    sys.argv = [str(app/'indextts25_worker.py'), '--port', '9881']
runpy.run_path(sys.argv[0], run_name='__main__')
