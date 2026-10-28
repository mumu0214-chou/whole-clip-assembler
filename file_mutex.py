"""Process + thread exclusion. OS releases byte locks after a process crash."""
from contextlib import contextmanager
from pathlib import Path
import os
import threading
import time

_guard = threading.Lock()
_locks = {}

@contextmanager
def file_mutex(path, timeout=1800):
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _guard:
        lock = _locks.setdefault(str(path).casefold(), threading.Lock())
    if not lock.acquire(timeout=timeout):
        raise TimeoutError('等待文件锁超时：' + str(path))
    stream = None
    locked = False
    try:
        stream = path.open('a+b')
        if path.stat().st_size == 0:
            stream.write(b'0'); stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('等待文件锁超时：' + str(path))
                time.sleep(.05)
        yield
    finally:
        if stream:
            if locked:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_UN)
            stream.close()
        lock.release()
