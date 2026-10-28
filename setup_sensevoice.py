"""Download exactly the two required official SenseVoice files, without archive path extraction."""
from pathlib import Path
import os, shutil, tarfile, tempfile, urllib.request
from mac_paths import path

target = Path(path(r'D:\GPTData\cache\codex-asr-models\sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'))
required = ('model.int8.onnx', 'tokens.txt')
if all((target / name).is_file() for name in required):
    print('SenseVoice files already exist')
    raise SystemExit(0)
target.mkdir(parents=True, exist_ok=True)
url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2'
fd, archive = tempfile.mkstemp(prefix='sensevoice-', suffix='.tar.bz2', dir=os.environ['ASSEMBLER_TEMP'])
os.close(fd)
try:
    urllib.request.urlretrieve(url, archive)
    with tarfile.open(archive, 'r:bz2') as data:
        for name in required:
            dest = target / name
            if dest.is_file(): continue
            members = [m for m in data.getmembers() if m.isfile() and Path(m.name).name == name]
            if len(members) != 1: raise RuntimeError('SenseVoice archive missing or ambiguous: ' + name)
            partial_fd, partial = tempfile.mkstemp(prefix='.sensevoice-', dir=target)
            try:
                with data.extractfile(members[0]) as src, os.fdopen(partial_fd, 'wb') as out:
                    shutil.copyfileobj(src, out)
                os.link(partial, dest)  # fail rather than replace an existing file
            finally:
                Path(partial).unlink(missing_ok=True)
finally:
    Path(archive).unlink(missing_ok=True)
