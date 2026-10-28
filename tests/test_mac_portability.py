import unittest
from unittest.mock import patch, Mock
from pathlib import Path, PureWindowsPath
import sys
import types
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mac_paths

class Paths(unittest.TestCase):
    def test_windows_paths_are_preserved_on_windows(self):
        source = r'D:\DY video\人工处理素材\牛肉\sk'
        self.assertEqual(mac_paths.path(source, platform='nt'), source)

    def test_mac_resources_are_inside_separate_workspace(self):
        for source in (r'D:\DY video\人工处理素材\牛肉\sk',
                       r'D:\DY video\音乐库', r'D:\GPTData\temp\assembler_voice_jobs'):
            path = Path(mac_paths.path(source, platform='posix'))
            self.assertTrue(path.is_relative_to(mac_paths.HOME), path)

    def test_tts_and_asr_interpreters_are_distinct(self):
        tts=Path(mac_paths.path(r'D:\GPTData\venvs\indextts25\Scripts\python.exe', platform='posix'))
        asr=Path(mac_paths.path(r'D:\GPTData\asr-projects\ASR_20260907\.venv\Scripts\python.exe', platform='posix'))
        self.assertEqual(tts, mac_paths.RUNTIME/'index-tts/.venv/bin/python')
        self.assertEqual(asr, mac_paths.RUNTIME/'asr/bin/python')

    def test_index_source_matches_worker_directory(self):
        self.assertEqual(Path(mac_paths.path(r'D:\DY video\本地配音\IndexTTS-2.5\index-tts', platform='posix')), mac_paths.RUNTIME/'index-tts')

    def test_worker_selects_mps_without_using_cuda(self):
        import indextts25_worker as worker
        fake_torch = types.ModuleType('torch')
        fake_torch.cuda = Mock()
        fake_torch.cuda.is_available.return_value = False
        fake_torch.backends = types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda:True))
        package=types.ModuleType('indextts');package.__path__=[]
        official=types.ModuleType('indextts.infer_v2_5')
        official.IndexTTS2=Mock(return_value=types.SimpleNamespace(device='mps',use_cuda_kernel=False))
        with patch.dict(sys.modules, {'torch':fake_torch,'indextts':package,'indextts.infer_v2_5':official}), \
             patch.object(worker.sys, 'platform', 'darwin'), \
             patch.object(worker, 'instrument_official'), patch.object(worker, 'ReferenceRuntime'), \
             patch.dict(worker.options, {'runtime_cache':False}), patch.dict(worker.state, {'ready':False,'error':None}):
            worker.load()
            self.assertTrue(worker.state['ready'], worker.state.get('error'))
            kwargs=official.IndexTTS2.call_args.kwargs
            self.assertEqual(kwargs['device'], 'mps')
            self.assertFalse(kwargs['use_bf16'])
            self.assertFalse(kwargs['use_cuda_kernel'])
            fake_torch.cuda.get_device_name.assert_not_called()

if __name__ == '__main__': unittest.main()
