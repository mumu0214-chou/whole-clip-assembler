# -*- coding: utf-8 -*-
"""Manual scripts, local IndexTTS-2.5, subtitles and measured voice/BGM mixing."""
from __future__ import annotations
from mac_paths import path as _portable_path
import runtime_diagnostics as diag

from abc import ABC, abstractmethod
from hashlib import sha256
from pathlib import Path
import json
import hashlib
import math
import os
import random
import re
import socket
import subprocess
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
import wave


VOICE_ROOT = Path(_portable_path(r'D:\DY video\本地配音'))
SOVITS_ROOT = VOICE_ROOT / 'GPT-SoVITS' / 'GPT-SoVITS-v2pro-20250604-nvidia50'
INDEXTTS_ROOT = VOICE_ROOT / 'IndexTTS-2.5' / 'index-tts'
PROFILE_ROOT = VOICE_ROOT / '音色库'
CACHE_ROOT = VOICE_ROOT / 'voice_cache'
VOICE_TEMP_ROOT = Path(_portable_path(r'D:\GPTData\temp\assembler_voice_jobs'))
PRESENCE_DEFAULTS = {'presence_enabled': False, 'presence_gain_db': 0.0,
                     'presence_frequency_hz': 3800.0, 'presence_q': 0.9}
VOICE_POST_REVISION = 'voice_post_v2_presence'
MODES = {
    '纯画面+BGM': (False, False, True),
    '字幕+BGM': (False, True, True),
    '配音+BGM': (True, False, True),
    '配音+字幕+BGM': (True, True, True),
}
DEFAULT_SUBTITLE_PROFILE = {
    'profile_id': 'XHS_9_16_V1', 'font': 'Microsoft YaHei', 'font_size': 58,
    'font_weight': 800, 'color': '#ffffff', 'stroke': '#000000', 'stroke_width': 3,
    'max_lines': 2, 'max_chars_per_line': 16, 'position': '下中',
    'safe_margin_top': 0.10, 'safe_margin_bottom': 0.14,
    'safe_margin_left': 0.08, 'safe_margin_right': 0.08,
}
MIX_PROFILES = {
    '配音优先': {'voice_lufs': -16, 'bgm_lufs': -22, 'duck_db': 10, 'attack_ms': 80, 'release_ms': 350, 'hold_ms': 500},
    '均衡': {'voice_lufs': -16, 'bgm_lufs': -20, 'duck_db': 8, 'attack_ms': 80, 'release_ms': 350, 'hold_ms': 500},
    '音乐突出': {'voice_lufs': -16, 'bgm_lufs': -18, 'duck_db': 7, 'attack_ms': 80, 'release_ms': 350, 'hold_ms': 500},
}


class VoiceError(RuntimeError):
    pass


def voice_failure_stage(error: Exception | str) -> str:
    message = str(error).lower()
    for token, stage in (
        ('参考音频', '参考音频检查'), ('参考文本', '参考文本检查'),
        ('api', '本地配音服务'), ('模型', '本地配音模型'),
        ('tts', 'TTS请求'), ('缓存', '配音缓存'),
        ('后处理', '音频后处理'), ('ffmpeg', 'FFmpeg处理'),
        ('超时', '请求超时'), ('timeout', '请求超时')):
        if token in message:
            return stage
    return '配音执行'


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _file_hash(path):
    h = sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


_DIGITS = '零一二三四五六七八九'


def chinese_number(value: str) -> str:
    n = int(value)
    if n == 0:
        return '零'
    if n >= 10000:
        return ''.join(_DIGITS[int(d)] for d in value)
    def under_thousand(x):
        out = ''
        for unit, label in ((1000, '千'), (100, '百'), (10, '十')):
            q, x = divmod(x, unit)
            if q:
                if out.endswith('零'):
                    out = out[:-1]
                out += ('' if unit == 10 and q == 1 and not out else _DIGITS[q]) + label
            elif out and x and not out.endswith('零'):
                out += '零'
        if x:
            out += _DIGITS[x]
        return out
    return under_thousand(n)


def normalize_speech(raw: str, custom: dict[str, str] | None = None) -> str:
    """Deterministic reading aid. Never edits the supplied raw text."""
    text = raw.strip()
    for source, target in sorted((custom or {}).items(), key=lambda p: -len(p[0])):
        if source:
            text = text.replace(source, target)
    text = re.sub(r'(?i)(?<![A-Za-z])JD(?![A-Za-z])', '京东', text)
    text = re.sub(r'(?i)(?<![A-Za-z])APP(?![A-Za-z])', '应用', text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*元\s*/\s*(\d+(?:\.\d+)?)\s*(斤|公斤|克)',
                  lambda m: f'{_read_decimal(m[1])}元{_read_measure(m[2])}{m[3]}', text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*%', lambda m: '百分之' + _read_decimal(m[1]), text)
    text = re.sub(r'(\d+(?:\.\d+)?)\s*(斤|公斤|克|分钟|小时|秒)',
                  lambda m: _read_measure(m[1]) + m[2], text)
    text = re.sub(r'\d+(?:\.\d+)?', lambda m: _read_decimal(m[0]), text)
    text = re.sub(r'(?<=\S)\s*/\s*(?=\S)', '每', text)
    return text


def _read_decimal(value: str) -> str:
    if '.' not in value:
        return chinese_number(value)
    whole, fraction = value.split('.', 1)
    return chinese_number(whole) + '点' + ''.join(_DIGITS[int(d)] for d in fraction)


def _read_measure(value: str) -> str:
    return '两' if value == '2' else _read_decimal(value)


def parse_scripts(text: str, batch: bool, mode: str, voice_id: str = '',
                  custom: dict[str, str] | None = None) -> list[dict]:
    if mode not in MODES:
        raise VoiceError('未知文本模式。')
    # Explicit article markers are authoritative even when the UI still shows
    # its old single-entry toggle; ordinary line breaks remain one article.
    blocks = split_script_text(text)
    blocks = [block for block in blocks if block]
    narration, subtitle, bgm = MODES[mode]
    if (narration or subtitle) and not blocks:
        raise VoiceError('请先手动添加文案。')
    return [dict(script_id=f'S{i:03d}', raw_text=raw,
                 speech_text=normalize_speech(raw, custom), subtitle_text=raw,
                 speech_text_override='', mode=mode, voice_profile=voice_id,
                 subtitle_profile=DEFAULT_SUBTITLE_PROFILE['profile_id'],
                 narration_enabled=narration, subtitle_enabled=subtitle, bgm_enabled=bgm)
            for i, raw in enumerate(blocks, 1)]


def split_script_text(text: str) -> list[str]:
    """Split only explicit separators or a consecutive, numbered article series."""
    normalized = str(text).replace('\r\n', '\n').replace('\r', '\n').strip()
    if not normalized:
        return []
    if re.search(r'(?m)^\s*---\s*$', normalized):
        return [part.strip() for part in re.split(r'(?m)^\s*---\s*$', normalized) if part.strip()]
    marker = re.compile(r'(?m)^[ \t]*(\d{1,3})[.．、][ \t]*(?=\S)')
    found = list(marker.finditer(normalized))
    if len(found) >= 2 and found[0].start() == 0 and [int(m[1]) for m in found] == list(range(1, len(found)+1)):
        return [normalized[m.end():found[i+1].start() if i+1 < len(found) else len(normalized)].strip()
                for i, m in enumerate(found)]
    return [normalized]


def bind_scripts(scripts: list[dict], count: int, allow_repeat: bool = False) -> list[dict]:
    if not scripts:
        return []
    if len(scripts) < count and not allow_repeat:
        raise VoiceError(f'文案不足，还缺{count-len(scripts)}篇。')
    return [dict(scripts[i % len(scripts)]) for i in range(count)]


def ensure_mambo_template() -> Path:
    folder = PROFILE_ROOT / '曼波V1'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / 'profile.json'
    if path.exists():
        return path
    profile = dict(voice_id='mambo_v1', display_name='曼波V1', profile_version='1',
        reference_audio='reference.wav', reference_text='', reference_language='zh', target_language='zh',
        engine='indextts25', speed=1.0, pause=0.15,
        emotion_mode='speaker', emotion_reference_audio='', emotion_alpha=1.0,
        emotion_text='', emotion_vector=[0.0]*8,
        postprocess_enabled=True, bass_gain=2, bass_frequency=220,
        treble_gain=-2, treble_frequency=4500,
        compressor_threshold=-18, compressor_ratio=2.5, compressor_attack=10,
        compressor_release=120, compressor_makeup=2,
        target_lufs=-16, true_peak=-1.5, sample_rate=48000, enabled=False,
        validation_status='draft', validation_message='缺少用户确认的参考音频。')
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return path


def discover_profiles(root: Path = PROFILE_ROOT) -> list[dict]:
    profiles = []
    if not root.is_dir():
        return profiles
    required = ('voice_id', 'display_name', 'profile_version', 'reference_audio',
                'reference_language', 'target_language', 'speed', 'pause', 'sample_rate')
    post_fields = ('bass_gain', 'bass_frequency', 'treble_gain', 'treble_frequency',
                   'compressor_threshold', 'compressor_ratio', 'compressor_attack',
                   'compressor_release', 'compressor_makeup', 'target_lufs', 'true_peak')
    for file in sorted(root.glob('*/profile.json')):
        try:
            profile = json.loads(file.read_text(encoding='utf-8-sig'))
            # Fill only in memory. Reading an old profile never rewrites it.
            for key, value in PRESENCE_DEFAULTS.items():
                profile.setdefault(key, value)
            from voice_tuning import TUNING_DEFAULTS
            for key,value in TUNING_DEFAULTS.items():profile.setdefault(key,value)
            missing = [key for key in required if profile.get(key) in (None, '')]
            if profile.get('engine') != 'indextts25':
                missing.append('尚未迁移至IndexTTS-2.5')
            if profile.get('postprocess_enabled', True):
                missing.extend(key for key in post_fields if profile.get(key) in (None, ''))
            ref = (file.parent / str(profile.get('reference_audio', ''))).resolve()
            if not ref.is_relative_to(file.parent.resolve()) or not ref.is_file():
                missing.append('reference_audio文件')
            emotion_ref = None
            if profile.get('emotion_mode') == 'audio':
                emotion_ref = (file.parent / str(profile.get('emotion_reference_audio', ''))).resolve()
                if not emotion_ref.is_relative_to(file.parent.resolve()) or not emotion_ref.is_file():
                    missing.append('语气参考音频文件')
            if profile.get('validation_status') != 'ready':
                missing.append('validation_status=ready')
            if not profile.get('enabled'):
                missing.append('enabled')
            profile.update(profile_path=str(file), reference_path=str(ref),
                           emotion_reference_path=str(emotion_ref) if emotion_ref else '',
                           available=not missing, unavailable_reason='、'.join(missing))
        except (OSError, ValueError, TypeError) as exc:
            profile = dict(voice_id=file.parent.name, display_name=file.parent.name,
                           profile_path=str(file), available=False, unavailable_reason=str(exc))
        profiles.append(profile)
    return profiles


class TTSProvider(ABC):
    @abstractmethod
    def synthesize(self, speech_text: str, profile: dict, output: Path) -> None: ...


class IndexTTS25Provider(TTSProvider):
    """Reuse one loopback worker; never load a model for each sentence."""
    engine = 'indextts25'

    def __init__(self, root: Path = INDEXTTS_ROOT, port: int = 9881, log=lambda _: None):
        self.root = Path(root)
        self.port = port
        self.url = f'http://127.0.0.1:{port}'
        self.log = log
        self.owned_process = None
        self.last_result = None

    def _health(self):
        try:
            with urllib.request.urlopen(self.url + '/health', timeout=3) as response:
                state = json.load(response)
        except urllib.error.URLError:
            return None
        if state.get('engine') != self.engine or state.get('version') != '2.5':
            raise VoiceError(f'端口{self.port}被其他服务占用。')
        return state

    from background_tasks import worker_start_serial
    @worker_start_serial
    def ensure_ready(self, timeout: float = 480):
        state = self._health()
        if state is None:
            if self.owned_process and self.owned_process.poll() is None:
                pass
            else:
                try:
                    with socket.create_connection(('127.0.0.1', self.port), timeout=2):
                        raise VoiceError(f'端口{self.port}已有服务，未重复启动 Worker。')
                except OSError:
                    pass
                python = Path(_portable_path(r'D:\GPTData\venvs\indextts25\Scripts\python.exe'))
                entry = Path(__file__).with_name('indextts25_worker.py')
                if not python.is_file() or not entry.is_file():
                    raise VoiceError('缺少 IndexTTS-2.5 独立运行环境或 Worker。')
                logs = VOICE_ROOT / 'logs'; logs.mkdir(parents=True, exist_ok=True)
                Path(_portable_path(r'D:\GPTData\temp')).mkdir(parents=True, exist_ok=True)
                env = os.environ.copy()
                env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', TEMP=_portable_path(r'D:\GPTData\temp'), TMP=_portable_path(r'D:\GPTData\temp'))
                flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
                with (logs / 'indextts25_worker.log').open('ab') as output:
                    self.owned_process = subprocess.Popen([str(python), '-u', str(entry), '--port', str(self.port)], cwd=self.root, env=env, stdout=output, stderr=subprocess.STDOUT, creationflags=flags)
                self.log('正在启动 IndexTTS-2.5；模型加载完成后常驻，连续配音复用。')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self,"cancel_event",None) and self.cancel_event.is_set():raise VoiceError("等待模型就绪已取消；共享服务继续加载。")
            state = self._health()
            if state and state.get('error'):
                raise VoiceError('IndexTTS-2.5 模型加载失败：' + state['error'])
            if state and state.get('ready'):
                self.worker_performance_version=state.get('performance_version')
                return
            if self.owned_process and self.owned_process.poll() is not None:
                raise VoiceError('IndexTTS Worker 提前退出；请查看本地配音/logs/indextts25_worker.log。')
            time.sleep(1)
        raise VoiceError('IndexTTS-2.5 模型加载超时；保留现有进程，未重复启动。')

    from background_tasks import gpu_serial
    @gpu_serial
    def synthesize(self, speech_text: str, profile: dict, output: Path) -> None:
        if profile.get('engine') != self.engine or not profile.get('available'):
            raise VoiceError('该音色尚未通过 IndexTTS-2.5 试听审核。')
        self.ensure_ready()
        payload = dict(text=speech_text, reference_path=profile['reference_path'], output_path=str(Path(output).resolve()),
                       language=profile.get('target_language', 'zh'), speed=profile['speed'], pause=profile.get('pause', .15),
                       emotion_mode=profile.get('emotion_mode', 'speaker'), emotion_alpha=profile.get('emotion_alpha', 1.),
                       emotion_reference_path=profile.get('emotion_reference_path', ''), emotion_text=profile.get('emotion_text', ''),
                       emotion_vector=profile.get('emotion_vector', [0.] * 8))
        payload.update(getattr(self, 'request_context', {}))
        payload['voice_id'] = profile['voice_id']
        if profile.get('preview_seed') is not None:
            payload['sampling_seed'] = int(profile['preview_seed'])
        request = urllib.request.Request(self.url + '/synthesize', data=json.dumps(payload, ensure_ascii=False).encode('utf-8'), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=getattr(self,'request_timeout',900)) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise VoiceError('IndexTTS-2.5 配音失败：' + exc.read(131072).decode('utf-8', 'replace')) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise VoiceError(f'IndexTTS-2.5 请求失败：{exc}') from exc
        if result.get('engine') != self.engine or not Path(output).is_file():
            raise VoiceError('IndexTTS-2.5 未生成有效 WAV。')
        if result.get('voice_id') is not None and result['voice_id'] != profile['voice_id']:
            raise VoiceError('IndexTTS结果音色身份与冻结Plan不一致。')
        if result.get('text_sha256') and result['text_sha256'] != hashlib.sha256(speech_text.encode()).hexdigest():
            raise VoiceError('IndexTTS结果文案hash与请求不一致。')
        for key in ('video_id','job_id','script_id'):
            if payload.get(key) is not None and (result.get(key) is not None or getattr(self,'worker_performance_version',None)=='runtime_v1'):
                if result.get(key)!=payload[key]:raise VoiceError('IndexTTS结果身份与请求不一致：'+key)
        self.last_result = result

    def close_owned(self):
        # Retain the loopback worker across UI restarts; the next session reuses it.
        pass



class GPTSoVITSProvider(TTSProvider):
    def __init__(self, root: Path = SOVITS_ROOT, port: int = 9880, log=lambda _: None):
        self.root = Path(root)
        self.url = f'http://127.0.0.1:{port}'
        self.port = port
        self.log = log
        self.owned_process = None
        self._selected_models = None

    def _ready(self) -> bool:
        try:
            with urllib.request.urlopen(self.url + '/openapi.json', timeout=2) as response:
                spec = json.load(response)
            if '/tts' not in spec.get('paths', {}) or '/set_gpt_weights' not in spec.get('paths', {}):
                raise VoiceError(f'端口{self.port}被其他服务占用，不能作为GPT-SoVITS API复用。')
            return True
        except urllib.error.URLError:
            return False

    def ensure_ready(self, timeout: float = 180) -> None:
        if self._ready():
            self.log('GPT-SoVITS API已就绪，复用现有服务。')
            return
        # A live owned process may still be loading its models. Never launch a
        # second server while that process owns this port.
        if self.owned_process and self.owned_process.poll() is None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and self.owned_process.poll() is None:
                if self._ready():
                    return
                time.sleep(1)
            raise VoiceError('GPT-SoVITS API进程仍在运行，但模型/API未Ready；未重复启动实例。')
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=2):
                raise VoiceError(f'端口{self.port}已有服务但GPT-SoVITS API未响应；未重复启动实例。')
        except OSError:
            pass
        executable = self.root / 'runtime' / 'python.exe'
        entry = self.root / 'api_v2.py'
        if not executable.is_file() or not entry.is_file():
            raise VoiceError('缺少当前GPT-SoVITS官方api_v2.py或运行时。')
        logs = VOICE_ROOT / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        output = (logs / f'api_{int(time.time())}.log').open('ab')
        env = os.environ.copy()
        env['PATH'] = str(self.root / 'runtime') + os.pathsep + env.get('PATH', '')
        env['PYTHONIOENCODING'] = 'utf-8'
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        config = VOICE_ROOT / 'config' / 'tts_infer_assembler_v2pro.yaml'
        if not config.is_file():
            raise VoiceError(f'缺少v2Pro模型配置：{config}')
        self.owned_process = subprocess.Popen([str(executable), '-s', str(entry), '-a', '127.0.0.1',
                                               '-p', str(self.port), '-c', str(config)],
                                              cwd=self.root, env=env, stdout=output, stderr=subprocess.STDOUT,
                                              creationflags=flags)
        self._selected_models = None
        output.close()
        self.log(f'正在启动GPT-SoVITS官方本地API，PID {self.owned_process.pid}。')
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.owned_process.poll() is not None:
                code = self.owned_process.returncode
                self.owned_process = None
                raise VoiceError(f'GPT-SoVITS API启动失败，退出码{code}；请查看{logs}。')
            if self._ready():
                self.log('GPT-SoVITS API模型已就绪。')
                return
            time.sleep(1)
        self.close_owned()
        raise VoiceError(f'GPT-SoVITS API在{timeout:.0f}秒内未就绪。')

    def synthesize(self, speech_text: str, profile: dict, output: Path) -> None:
        if not profile.get('available'):
            raise VoiceError('音色不可用：' + profile.get('unavailable_reason', '缺少配置'))
        self.ensure_ready()
        if profile.get('gpt_model') or profile.get('sovits_model'):
            models = model_versions(self.root, profile)
            selected = (models['gpt']['path'], models['sovits']['path'])
            if self._selected_models != selected:
                for endpoint, target in (('/set_gpt_weights', selected[0]),
                                         ('/set_sovits_weights', selected[1])):
                    url = self.url + endpoint + '?' + urllib.parse.urlencode({'weights_path': target})
                    try:
                        with urllib.request.urlopen(url, timeout=120) as response:
                            if response.status != 200:
                                raise VoiceError(f'GPT-SoVITS模型未Ready：{endpoint}')
                    except (urllib.error.URLError, TimeoutError) as exc:
                        raise VoiceError(f'GPT-SoVITS模型切换失败：{endpoint}：{exc}') from exc
                self._selected_models = selected
        payload = dict(text=speech_text, text_lang=profile['target_language'],
                       ref_audio_path=profile['reference_path'], prompt_lang=profile['reference_language'],
                       prompt_text=profile['reference_text'], top_k=profile['top_k'], top_p=profile['top_p'],
                       temperature=profile['temperature'], speed_factor=profile['speed'],
                       fragment_interval=profile['pause'], media_type='wav', streaming_mode=False)
        payload.update(text_split_method=profile.get('text_split_method', 'cut5'),
                       parallel_infer=bool(profile.get('parallel_inference', True)),
                       seed=int(profile.get('seed', -1)))
        self.last_request_payload = dict(payload)
        self.log('TTS_REQUEST=' + json.dumps(payload, ensure_ascii=False))
        request = urllib.request.Request(self.url + '/tts',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                data = response.read()
        except urllib.error.HTTPError as exc:
            raise VoiceError(f'GPT-SoVITS配音失败：HTTP {exc.code} {exc.read(500).decode("utf-8", "replace")}') from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise VoiceError(f'GPT-SoVITS配音请求失败：{exc}') from exc
        if not data.startswith(b'RIFF') or len(data) < 44:
            raise VoiceError('GPT-SoVITS未返回有效WAV，未写入缓存。')
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_suffix('.part.wav')
        temp.write_bytes(data)
        os.replace(temp, output)

    def close_owned(self):
        if self.owned_process and self.owned_process.poll() is None:
            self.owned_process.terminate()
            try:
                self.owned_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.log('GPT-SoVITS API未按时退出，保留进程供人工检查。')


def model_versions(root: Path = INDEXTTS_ROOT, profile: dict | None = None) -> dict:
    profile = profile or {}
    names = ({'engine_config': 'checkpoints/config.yaml', 'speech_model': 'checkpoints/gpt.pth',
              'codec': 'checkpoints/codec.pth', 'mel_model': 'checkpoints/s2mel.pth',
              'emotion_model': 'checkpoints/qwen0.6bemo4-merge/model.safetensors'}
             if profile.get('engine') == 'indextts25' or Path(root) == INDEXTTS_ROOT else
             {'gpt': profile.get('gpt_model') or 'GPT_SoVITS/pretrained_models/s1v3.ckpt',
              'sovits': profile.get('sovits_model') or 'GPT_SoVITS/pretrained_models/v2Pro/s2Gv2Pro.pth'})
    result = {}
    for label, relative in names.items():
        path = (root / relative).resolve()
        if not path.is_file():
            raise VoiceError(f'缺少本地配音模型：{path}')
        stat = path.stat()
        result[label] = {'path': str(path), 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    return result


def _run(args, timeout=180):
    import runtime_diagnostics as diag
    if diag.hub():
        try:return diag.process([str(x) for x in args],timeout=timeout,stage_name=Path(args[0]).name,return_stderr=True)
        except (diag.ProcessFailure,OSError) as exc:raise VoiceError(str(exc)) from exc
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired as exc:
        raise VoiceError(f'音频处理超时（{timeout}秒）：{Path(args[0]).name}') from exc
    if result.returncode:
        raise VoiceError(result.stderr.decode('utf-8', 'replace')[-1500:])
    return result.stderr.decode('utf-8', 'replace')


def postprocess_filter_chain(profile: dict) -> str:
    """One filter definition shared by preview and production."""
    if not profile.get('postprocess_enabled', True):
        return 'aresample=48000'
    threshold = 10 ** (float(profile['compressor_threshold']) / 20)
    makeup = 10 ** (float(profile['compressor_makeup']) / 20)
    # Bass/Treble are shelves. The former t=h:w=1 peaking filters affected
    # only a 1 Hz band and made large UI gain changes nearly inaudible.
    filters = []
    if profile.get('pitch_enabled',False):
        semitones=float(profile.get('pitch_semitones',0))
        if not math.isfinite(semitones) or not -3<=semitones<=3:
            raise VoiceError('音高半音须在-3～+3之间。')
        filters.append(f'rubberband=tempo=1:pitch={2**(semitones/12):.12f}:formant=preserved')
    filters.append(f'bass=f={float(profile["bass_frequency"]):g}:t=s:w=0.5:g={float(profile["bass_gain"]):g}')
    presence = dict(PRESENCE_DEFAULTS, **{k:profile[k] for k in PRESENCE_DEFAULTS if k in profile})
    if presence['presence_enabled']:
        for key, low, high in (('presence_gain_db', -6, 6),
                               ('presence_frequency_hz', 2500, 6000), ('presence_q', .5, 2)):
            try:
                value = float(presence[key])
            except (TypeError, ValueError) as exc:
                raise VoiceError(f'{key}必须是有限数字。') from exc
            if not math.isfinite(value) or not low <= value <= high:
                raise VoiceError(f'{key}须在{low:g}～{high:g}之间。')
            presence[key] = value
        filters.append(f'equalizer=f={presence["presence_frequency_hz"]:g}:t=q:'
                       f'w={presence["presence_q"]:g}:g={presence["presence_gain_db"]:g}')
    filters.append(f'treble=f={float(profile["treble_frequency"]):g}:t=s:w=0.5:g={float(profile["treble_gain"]):g}')
    if profile.get('deesser_enabled',False):
        values=[float(profile.get(k,.5)) for k in ('deesser_intensity','deesser_max','deesser_frequency')]
        if any(not math.isfinite(v) or not 0<=v<=1 for v in values):raise VoiceError('齿音参数须在0～1之间。')
        filters.append(f'deesser=i={values[0]:g}:m={values[1]:g}:f={values[2]:g}:s=o')
    filters.extend([
               f'acompressor=threshold={threshold:.6f}:ratio={profile["compressor_ratio"]}:attack={profile["compressor_attack"]}:release={profile["compressor_release"]}:makeup={makeup:.6f}',
               f'loudnorm=I={profile["target_lufs"]}:TP={profile["true_peak"]}:LRA=9', 'aresample=48000'])
    return ','.join(filters)


@diag.measured("audio","voice_pipeline")
def postprocess(raw: Path, output: Path, profile: dict, ffmpeg: str, log=lambda _: None):
    from voice_tuning import filter_capabilities
    if profile.get('postprocess_enabled',True):
        for enabled,capability in (('pitch_enabled','pitch'),('deesser_enabled','deesser')):
            if profile.get(enabled) and not filter_capabilities(ffmpeg)[capability]:
                raise VoiceError('当前FFmpeg不支持'+capability+'，未假装应用处理。')
    output.parent.mkdir(parents=True, exist_ok=True)
    filters = postprocess_filter_chain(profile)
    log('POSTPROCESS=' + ('ON' if profile.get('postprocess_enabled', True) else 'OFF'))
    log('FFMPEG_FILTER_CHAIN=' + filters)
    temp = output.with_suffix('.part.wav')
    _run([ffmpeg, '-hide_banner', '-nostdin', '-xerror', '-y', '-i', raw, '-af', filters,
          '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', temp])
    with wave.open(str(temp), 'rb') as wav:
        if wav.getframerate() != 48000:
            raise VoiceError('配音后处理输出不是48000Hz。')
    os.replace(temp, output)
    return {'actual_ffmpeg_filter_chain': filters, 'postprocess_version': VOICE_POST_REVISION}


class VoicePostProcessor:
    filter_chain = staticmethod(postprocess_filter_chain)
    process = staticmethod(postprocess)


def effective_voice_range(path: Path, ffmpeg: str) -> tuple[float, float]:
    with wave.open(str(path), 'rb') as wav:
        duration = wav.getnframes() / wav.getframerate()
    stderr = _run([ffmpeg, '-hide_banner', '-nostdin', '-xerror', '-i', path, '-af',
                   'silencedetect=noise=-45dB:d=0.4', '-f', 'null', '-'], timeout=90)
    starts = [float(x) for x in re.findall(r'silence_start:\s*([\d.]+)', stderr)]
    ends = [float(x) for x in re.findall(r'silence_end:\s*([\d.]+)', stderr)]
    begin = min(duration, (ends[0] - 0.15)) if starts and ends and starts[0] <= 0.03 else 0.0
    finish = max(begin, starts[-1] + 0.15) if starts and starts[-1] > duration - 1.0 else duration
    return max(0.0, begin), min(duration, finish)


def voice_identity_keys(text, profile, models, reference_hash, emotion_hash):
    raw_key = _hash({'text': text, 'voice_id': profile['voice_id'],
                     'profile_version': profile['profile_version'], 'models': models,
                     'reference_sha256': reference_hash,
                     'reference_text': profile.get('reference_text', ''),
                     'speed': profile['speed'], 'pause': profile['pause'],
                     'engine': profile.get('engine'), 'emotion_reference_sha256': emotion_hash,
                     'emotion_mode': profile.get('emotion_mode', 'speaker'),
                     'emotion_alpha': profile.get('emotion_alpha', 1.),
                     'emotion_text': profile.get('emotion_text', ''),
                     'emotion_vector': profile.get('emotion_vector', [0.]*8),
                     **({'sampling_seed':int(profile['preview_seed'])} if profile.get('preview_seed') is not None else {})})
    post_params = {key: profile.get(key) for key in (
        'postprocess_enabled', 'bass_gain', 'bass_frequency', 'treble_gain', 'treble_frequency',
        'compressor_threshold', 'compressor_ratio', 'compressor_attack', 'compressor_release',
        'compressor_makeup', 'target_lufs', 'true_peak', 'sample_rate')}
    post_params.update({key:profile.get(key, default) for key, default in PRESENCE_DEFAULTS.items()})
    # Missing/off tuning fields preserve existing frozen identities.
    if profile.get('pitch_enabled'):
        post_params.update(pitch_enabled=True,pitch_semitones=profile.get('pitch_semitones',0.))
    if profile.get('deesser_enabled'):
        post_params.update({k:profile.get(k,.5) for k in ('deesser_intensity','deesser_max','deesser_frequency')},deesser_enabled=True)
    processed_key = _hash({'raw_key': raw_key, 'postprocess': post_params, 'version': VOICE_POST_REVISION})
    return raw_key, processed_key


def prepare_voice(script: dict, profile: dict, provider: TTSProvider, ffmpeg: str,
                  cache_root: Path = VOICE_TEMP_ROOT / 'direct', log=lambda _: None,
                  cache_enabled: bool = False, post_submit=None, force_regenerate=False, persistent_root=None) -> dict:
    preparation_started = time.perf_counter()
    if not profile.get('available'):
        raise VoiceError('音色不可用：' + profile.get('unavailable_reason', '缺少参考资料'))
    text = script.get('speech_text_override', '').strip() or script['speech_text']
    if not Path(profile['reference_path']).is_file():
        raise VoiceError(f'参考音频不存在：{profile["reference_path"]}')
    reference_hash = _file_hash(profile['reference_path'])
    if script.get('reference_sha256') and script['reference_sha256'] != reference_hash:
        raise VoiceError('冻结方案的参考音频内容已改变；重试不能替换原参考。')
    emotion_hash = ''
    if profile.get('emotion_mode') == 'audio':
        emotion_hash = _file_hash(profile['emotion_reference_path'])
        if script.get('emotion_reference_sha256') and script['emotion_reference_sha256'] != emotion_hash:
            raise VoiceError('冻结方案的语气参考音频内容已改变。')
    models = model_versions(getattr(provider, 'root', INDEXTTS_ROOT), profile)
    raw_key, processed_key = voice_identity_keys(text,profile,models,reference_hash,emotion_hash)
    if cache_enabled:
        from voice_cache import prepare, ROOT
        return prepare(script, profile, provider, ffmpeg, cache_root, log, post_submit,
                       text, models, reference_hash, emotion_hash, processed_key, preparation_started,
                       force=force_regenerate, root=persistent_root or ROOT)
    suffix = '' if cache_enabled else '-' + uuid.uuid4().hex
    raw = Path(cache_root) / 'raw' / f'{raw_key}{suffix}.wav'
    processed = Path(cache_root) / 'processed' / f'{processed_key}{suffix}.wav'
    processed_state = cache_state(processed, ffmpeg) if cache_enabled else 'CACHE_MISSING'
    if processed_state == 'CACHE_READY':
        log(f'配音缓存命中：{processed.name}')
    else:
        log(f'处理配音临时文件：{processed.name}' if not cache_enabled else
            f'处理配音缓存：{processed.name} {processed_state}')
        processed.unlink(missing_ok=True)
        processed.with_suffix('.part.wav').unlink(missing_ok=True)
        raw_state = cache_state(raw, ffmpeg) if cache_enabled else 'CACHE_MISSING'
        if raw_state != 'CACHE_READY':
            log(f'原始配音临时文件：{raw.name}，调用IndexTTS-2.5生成。' if not cache_enabled else
                f'原始配音缓存：{raw.name} {raw_state}，重新生成。')
            raw.unlink(missing_ok=True)
            raw.with_suffix('.part.wav').unlink(missing_ok=True)
            try:
                if isinstance(provider, IndexTTS25Provider):
                    provider.request_context = {key:script.get(key) for key in ('video_id','job_id','script_id')}
                provider.synthesize(text, profile, raw)
            except Exception:
                raw.unlink(missing_ok=True)
                raw.with_suffix('.part.wav').unlink(missing_ok=True)
                raise
            log(f'IndexTTS-2.5已生成原始配音：{raw.name}')
    from functools import partial
    finish = partial(_finish_prepared_voice, raw, processed, profile, ffmpeg, cache_enabled,
                     processed_state, processed_key, models, log, preparation_started,
                     dict(getattr(provider,'last_result',None) or {}), dict(script))
    return post_submit(finish) if post_submit is not None else finish()


def _finish_prepared_voice(raw, processed, profile, ffmpeg, cache_enabled,
                           processed_state, processed_key, models, log,
                           preparation_started, raw_result, script):
    post_started = time.perf_counter()
    if processed_state != 'CACHE_READY':
        try:
            if _wav_duration(raw)<=0:
                raise VoiceError('TTS返回音频为空。')
            # Post fully decodes Raw with -xerror; the silence pass below fully
            # decodes Processed with -xerror. Avoid two redundant FFmpeg launches.
            postprocess(raw, processed, profile, ffmpeg, log=log)
        except Exception:
            processed.unlink(missing_ok=True)
            processed.with_suffix('.part.wav').unlink(missing_ok=True)
            if not cache_enabled:
                raw.unlink(missing_ok=True)
            raise
    try:
        start, end = effective_voice_range(processed, ffmpeg)
        if end - start < 0.2:
            raise VoiceError('配音有效声音不足0.2秒，不能作为正式配音。')
    except Exception:
        if not cache_enabled:
            raw.unlink(missing_ok=True)
            processed.unlink(missing_ok=True)
        raise
    performance = dict(raw_result, voice_postprocess_seconds=time.perf_counter()-post_started,
                       total_seconds=time.perf_counter()-preparation_started,
                       video_id=script.get('video_id'),job_id=script.get('job_id'),
                       script_id=script.get('script_id'),voice_id=profile['voice_id'])
    with (processed.parent.parent/'voice_performance.jsonl').open('a',encoding='utf-8') as trace:
        trace.write(json.dumps(performance,ensure_ascii=False)+'\n')
    return dict(voice_cache_key=processed_key, voice_raw=str(raw), voice_processed=str(processed),
                temporary=not cache_enabled,
                voice_duration=_wav_duration(processed), effective_voice_duration=end-start,
                effective_voice_start=start, effective_voice_end=end,
                model_versions=models, performance=performance, tts_parameters={key:profile.get(key) for key in
                  ('engine', 'speed', 'pause', 'emotion_mode', 'emotion_alpha',
                   'emotion_reference_path', 'emotion_text', 'emotion_vector','preview_seed')})


def cache_state(path: Path, ffmpeg: str) -> str:
    path = Path(path)
    partial = path.with_suffix('.part.wav')
    if not path.is_file():
        return 'CACHE_PARTIAL' if partial.exists() else 'CACHE_MISSING'
    if path.stat().st_size < 44:
        return 'CACHE_PARTIAL'
    try:
        with wave.open(str(path), 'rb') as wav:
            if wav.getnframes() <= 0 or wav.getframerate() <= 0 or len(wav.readframes(wav.getnframes())) != wav.getnframes()*wav.getnchannels()*wav.getsampwidth():
                return 'CACHE_FAILED'
        _run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror',
              '-i', path, '-f', 'null', '-'], timeout=90)
    except (VoiceError, OSError, EOFError, wave.Error):
        return 'CACHE_FAILED'
    return 'CACHE_READY'


def _wav_duration(path: Path):
    with wave.open(str(path), 'rb') as wav:
        return wav.getnframes() / wav.getframerate()


def measure_loudness(path: Path, ffmpeg: str) -> dict:
    stderr = _run([ffmpeg, '-hide_banner', '-nostdin', '-i', path, '-af',
                   'loudnorm=I=-16:TP=-1:LRA=11:print_format=json', '-f', 'null', '-'], timeout=180)
    match = re.search(r'\{\s*"input_i".*?\}', stderr, re.S)
    if not match:
        raise VoiceError(f'无法测量最终音频响度与真峰值：{path}')
    info = json.loads(match.group(0))
    return {'integrated_lufs': float(info['input_i']), 'true_peak_dbtp': float(info['input_tp'])}


@diag.measured("preparation","voice_pipeline")
def prepare_script_packages(scripts: list[dict], cfg: dict, ffmpeg: str,
                            profiles: list[dict], provider: TTSProvider | None = None,
                            log=lambda _: None) -> list[dict]:
    """Freeze voice choice and measured audio before visual planning."""
    if cfg.get('voice_source') == 'external_files':
        from external_voice import prepare_packages
        return prepare_packages(scripts,cfg,ffmpeg,log)
    available = {p['voice_id']: p for p in profiles if p.get('available')}
    selected = [p for p in profiles if p.get('voice_id') in cfg.get('voice_selected_ids', []) and p.get('available')]
    strategy = cfg.get('voice_strategy', '固定')
    if strategy not in ('固定', '轮换', '随机'):
        raise VoiceError('未知音色策略。')
    rng = random.Random(str(cfg.get('seed', '')) + ':voice-choice')
    results = []
    for index, script in enumerate(scripts):
        package = dict(script)
        if not package['narration_enabled']:
            results.append(package)
            continue
        if package.get('voice_profile'):
            if package['voice_profile'] not in available:
                raise VoiceError(f'{package["script_id"]}指定音色不可用：{package["voice_profile"]}')
            profile = available[package['voice_profile']]
        elif selected:
            profile = (selected[0] if strategy == '固定' else
                       selected[index % len(selected)] if strategy == '轮换' else rng.choice(selected))
        else:
            raise VoiceError('没有可用且已勾选的音色；请先试听并保存为可用。')
        package['voice_profile'] = profile['voice_id']
        package['voice_profile_version'] = profile['profile_version']
        package['voice_profile_path'] = profile['profile_path']
        if cfg.get('voice_speed_revision'):
            from voice_speed import decision
            package['voice_speed_snapshot']=decision(cfg,'local:'+profile['voice_id'])
        frozen_profile = dict(profile)
        package['voice_request_profile'] = frozen_profile
        package['reference_sha256'] = _file_hash(profile['reference_path'])
        if profile.get('emotion_mode') == 'audio':
            package['emotion_reference_sha256'] = _file_hash(profile['emotion_reference_path'])
        try:
            if provider is None:
                provider = IndexTTS25Provider(log=log)
            package['frozen_model_versions'] = model_versions(getattr(provider, 'root', INDEXTTS_ROOT), frozen_profile)
            job_root = Path(cfg.get('voice_job_temp_dir') or VOICE_TEMP_ROOT / str(cfg.get('seed', 'direct')))
        except VoiceError as exc:
            package['voice_error'] = str(exc)
            package['voice_failure_stage'] = voice_failure_stage(exc)
            log(f'{package["script_id"]} 配音失败：{exc}')
        results.append(package)
    from voice_execution import execute_packages
    def completed(package):
        if package.get('voice_error'):
            package['voice_failure_stage']=voice_failure_stage(VoiceError(package['voice_error']))
            log(f'{package["script_id"]} 配音失败：{package["voice_error"]}')
        elif not cfg.get('video_policy_version'):
            package['target_video_duration']=package['voice']['effective_voice_duration']+1.5
    pending=[p for p in results if p.get('narration_enabled') and not p.get('voice_error')]
    if pending:
        execute_packages(pending,provider,ffmpeg,job_root,log=log,on_ready=completed,
                         cache_enabled=cfg.get('voice_cache_enabled',True),force_regenerate=cfg.get('voice_force_regenerate',False))
    return results


@diag.measured("subtitles","voice_pipeline")
def freeze_script_on_item(item: dict, package: dict, cfg: dict) -> None:
    if cfg.get('voice_speed_revision') and package.get('narration_enabled') and package.get('voice'):
        from voice_speed import decision,speaker_key,apply as apply_voice_speed
        expected=decision(cfg,speaker_key(package))
        if not package.get('voice_speed_snapshot'):
            if expected['factor']!=1 or expected.get('pitch_semitones',0):raise VoiceError('配音变调/加速尚未准备完成，不能在选片后改变声音')
            package=dict(package,voice_speed_snapshot=expected,
                         voice=apply_voice_speed(package['voice'],expected,cfg.get('ffmpeg','ffmpeg')))
    from product_catalog import check_package,REVISION as SPEC_REVISION
    check_package(package,cfg)
    if cfg.get('product_spec_revision')==SPEC_REVISION:
        item['product_spec_snapshot']={k:cfg[k] for k in ('product_spec_revision','product_spec_id','product_spec_name','editing_profile_id','editing_profile_name')}
        item['script_profile_id']=package['script_profile_id'];item['script_spec_id']=package['script_spec_id']
    item['video_id'] = item['id']
    item['script_id'] = package['script_id']
    if package.get('script_library_id'):
        item['script_library_id'] = package['script_library_id']
    item['script_package'] = {key: package.get(key) for key in (
        'script_id', 'raw_text', 'speech_text', 'subtitle_text', 'speech_text_override',
        'mode', 'voice_profile', 'voice_profile_version', 'subtitle_profile',
        'voice_request_profile', 'reference_sha256', 'emotion_reference_sha256', 'voice_profile_path',
        'frozen_model_versions', 'script_library_id', 'narration_enabled',
        'subtitle_enabled', 'bgm_enabled','script_profile_id','script_spec_id','voice_speed_snapshot')}
    if package.get('voice_speed_snapshot'):
        from copy import deepcopy
        item['voice_speed_snapshot']=deepcopy(package['voice_speed_snapshot'])
    for key in ('voice_source','script_uid','body_version','body_hash','external_voice_name','external_version_id','external_resource_id','external_ref_id'):
        if key in package:
            item[key]=package[key]
            item['script_package'][key]=package[key]
    item['narration_enabled'] = bool(package['narration_enabled'])
    item['subtitle_enabled'] = bool(package['subtitle_enabled'])
    item['bgm_enabled'] = bool(package['bgm_enabled'])
    item['audio_mix_profile'] = cfg.get('audio_mix_profile', '配音优先')
    item['audio_mix_parameters'] = dict(MIX_PROFILES[item['audio_mix_profile']])
    item['audio_mix_revision'] = 'audio_mix_v1'
    item['voice_trim_db'] = float(cfg.get('voice_trim_db', 0))
    if cfg.get('bgm_rule_version'):
        item['voice_gain_db'] = float(cfg.get('voice_gain_db', 0))
    item['bgm_trim_db'] = float(cfg.get('bgm_trim_db', 0))
    profile = dict(DEFAULT_SUBTITLE_PROFILE)
    profile['position'] = cfg.get('subtitle_position', '下中')
    profile['custom_y'] = float(cfg.get('subtitle_custom_y', 0.72))
    profile['font_size'] = int(cfg.get('subtitle_font_size', profile['font_size']))
    profile['max_chars_per_line'] = int(cfg.get('subtitle_max_chars_per_line', profile['max_chars_per_line']))
    for side in ('top','bottom','left','right'):
        key = 'safe_margin_' + side
        profile[key] = float(cfg.get('subtitle_' + key, profile[key]))
    if cfg.get('subtitle_style'):
        from subtitle_fonts import freeze
        profile['style'] = freeze(cfg['subtitle_style'])
    if cfg.get('subtitle_system_version')=='smart_animation_v1' and 'style' not in profile:
        from subtitle_fonts import freeze
        from subtitle_styles import from_config
        profile['style']=freeze(from_config(cfg))
    if 'style' in profile:
        # Explicit canvas placement matches the editor. Historical profiles without
        # this marker retain their frozen legacy top-text avoidance behavior.
        profile['placement_revision'] = 'canvas_position_v1'
    item['subtitle_profile'] = profile
    if item['narration_enabled'] and 'voice' in package:
        voice = package['voice']
        item['voice'] = voice
        item['voice_profile'] = package['voice_profile']
        item['voice_profile_version'] = package['voice_profile_version']
        item['voice_cache_key'] = voice['voice_cache_key']
        item['voice_duration'] = voice['voice_duration']
        item['effective_voice_duration'] = voice['effective_voice_duration']
        if item['duration'] + 1e-6 < voice['effective_voice_duration']:
            raise VoiceError('画面无法完整容纳配音。')
    if item['subtitle_enabled']:
        spoken = package['voice']['effective_voice_duration'] if item['narration_enabled'] and 'voice' in package else item['duration']
        if cfg.get('subtitle_system_version')=='smart_animation_v1':
            from smart_subtitles import build
            try:
                item['subtitles'],item['subtitle_diagnostics']=build(package['subtitle_text'],profile['style'],spoken,cfg['fps'],
                    speech=package['speech_text'],voice=package.get('voice') if item['narration_enabled'] else None,
                    width=cfg.get('width',1080),height=cfg.get('height',1920),job_id=item['id'])
                diag.emit('subtitle_prepare_metrics','智能字幕准备完成',**item['subtitle_diagnostics'])
                diag.emit('subtitle_cues','字幕原文/显示/时间/字体/动画','DEBUG',job_id=item['id'],cues=item['subtitles'])
            except ValueError as exc:
                diag.emit('subtitle_review_required','智能字幕准备失败，需要复核','ERROR',job_id=item['id'],
                          failure_stage='subtitle_prepare',config_version=1,font=profile['style']['font_family'],exception=str(exc),
                          number=item.get('number'),audio_path=(package.get('voice') or {}).get('voice_processed'),
                          review_details=getattr(exc,'review_details',None))
                raise VoiceError('智能字幕需复核：'+str(exc)) from exc
        else:
            item['subtitles'] = WeightedSentenceTiming(profile['max_chars_per_line'], profile['max_lines'],
                                                   profile, cfg.get('width', 1080), cfg.get('height', 1920)).timings(
            package['subtitle_text'], spoken, cfg['fps'])
    else:
        item['subtitles'] = []
    if package.get('voice_error'):
        item['status'] = 'VOICE_FAILED'
        item['voice_failure_stage'] = package.get('voice_failure_stage', '配音执行')
        item['error'] = package['voice_error']


class SubtitleTimingProvider(ABC):
    @abstractmethod
    def timings(self, text: str, duration: float, fps: int) -> list[dict]: ...


class WeightedSentenceTiming(SubtitleTimingProvider):
    def __init__(self, max_chars_per_line=16, max_lines=2, profile=None, width=1080, height=1920):
        self.max_chars_per_line = max_chars_per_line
        self.max_lines = max_lines
        self.profile = profile or DEFAULT_SUBTITLE_PROFILE
        self.width = width
        self.height = height

    def _font(self):
        from PIL import ImageFont
        size = round(float(self.profile.get('font_size', 58)) * self.height / 1920)
        family = self.profile.get('font', 'Microsoft YaHei')
        if family == 'Microsoft YaHei':
            weight = int(self.profile.get('font_weight', 800))
            path = Path(_portable_path(r'C:\Windows\Fonts\msyhbd.ttc') if weight >= 600 else
                        _portable_path(r'C:\Windows\Fonts\msyh.ttc'))
        else:
            path = Path(family)
        if not path.is_file():
            raise VoiceError(f'字幕布局异常：未找到用于真实宽度测量的字体：{family}')
        return ImageFont.truetype(str(path), size)

    def _lines(self, value, font, available):
        value = value.strip()
        if not value:
            return []
        max_chars = max(3, int(self.max_chars_per_line))
        if len(value) <= max_chars and font.getlength(value) <= available:
            return [value]
        candidates = []
        for cut in range(3, len(value)-2):
            left, right = value[:cut].rstrip(), value[cut:].lstrip()
            if (len(left) > max_chars or len(right) > max_chars or
                    font.getlength(left) > available or font.getlength(right) > available):
                continue
            # Prefer balanced lines and breaks after punctuation; never leave a
            # one- or two-character tail merely to fill the first line.
            score = abs(font.getlength(left)-font.getlength(right))
            if left.endswith(('，', ',', '；', ';', '。', '！', '？')):
                score -= available
            candidates.append((score, [left, right]))
        if not candidates:
            return None
        return min(candidates, key=lambda value: value[0])[1]

    def _cards(self, text):
        font = self._font()
        safe = self.profile
        available = self.width * (1-float(safe.get('safe_margin_left', .08))-
                                  float(safe.get('safe_margin_right', .08))) - 12
        if available <= 0:
            raise VoiceError('字幕布局异常：安全宽度无效。')
        clauses = [part.strip() for part in re.findall(r'[^。！？!?，,；;\n]+[。！？!?，,；;]?', text)
                   if part.strip()]
        cards = []
        current = ''
        for clause in clauses:
            while clause:
                proposal = current + clause
                if len(proposal) <= min(24, 2*self.max_chars_per_line) and self._lines(proposal, font, available):
                    current = proposal
                    break
                if current:
                    cards.append(current)
                    current = ''
                    continue
                # Long clause: cut near the maximum fit, keeping at least 3
                # characters on the following card and respecting punctuation.
                limit = min(len(clause)-3, min(24, 2*self.max_chars_per_line))
                cut = next((i for i in range(limit, 2, -1)
                            if self._lines(clause[:i], font, available) and
                            (len(clause)-i >= 3)), None)
                if cut is None:
                    raise VoiceError(f'字幕布局异常：文字无法放入两行安全区：{clause[:20]}')
                cards.append(clause[:cut].strip())
                clause = clause[cut:].strip()
        if current:
            cards.append(current)
        if len(cards) > 1 and len(cards[-1]) <= 2:
            previous = cards[-2] + cards[-1]
            if self._lines(previous, font, available):
                cards[-2:] = [previous]
            else:
                raise VoiceError('字幕布局异常：末张字幕卡只有1～2字，无法安全合并。')
        result = []
        for card in cards:
            lines = self._lines(card, font, available)
            if not lines or len(lines) > min(2, self.max_lines) or any(len(x) <= 1 for x in lines):
                raise VoiceError(f'字幕布局异常：无法分页或存在孤字行：{card}')
            widths = [font.getlength(line) for line in lines]
            if max(widths) > available:
                raise VoiceError(f'字幕布局异常：超出安全宽度：{card}')
            result.append((card, lines, widths, available))
        return result

    def timings(self, text: str, duration: float, fps: int) -> list[dict]:
        cards = self._cards(text)
        if not cards or duration <= 0:
            return []
        total = max(1, round(duration * fps))
        if len(cards) > total:
            raise VoiceError('字幕布局异常：字幕卡数量超过可显示帧数。')
        weights = [max(2, len(card[0])) ** 0.75 for card in cards]
        boundaries = [0]
        cumulative = 0.0
        for weight in weights[:-1]:
            cumulative += weight
            boundaries.append(round(total * cumulative / sum(weights)))
        boundaries.append(total)
        if any(boundaries[i+1] <= boundaries[i] for i in range(len(cards))):
            raise VoiceError('字幕布局异常：字幕卡显示时间不足。')
        return [dict(text=card[0], lines=card[1], line_widths_px=[round(x, 2) for x in card[2]],
                     safe_width_px=round(card[3], 2),
                     start_frame=boundaries[i], end_frame=boundaries[i+1],
                     startMs=round(boundaries[i]*1000/fps), endMs=round(boundaries[i+1]*1000/fps),
                     timestampMs=None, confidence=None)
                for i, card in enumerate(cards)]


def mix_voice_bgm(item: dict, cfg: dict, voice: dict, profile_name: str,
                  ffmpeg: str, output: Path, voice_gain: float = 0, bgm_trim: float = 0,
                  *, defer_peak_validation: bool = False):
    if profile_name not in MIX_PROFILES:
        raise VoiceError('未知混音模式。')
    profile = item.get('audio_mix_parameters') or MIX_PROFILES[profile_name]
    output.parent.mkdir(parents=True, exist_ok=True)
    bgm = item['bgm']
    duration = float(item['duration'])
    start = voice['effective_voice_start']
    end = voice['effective_voice_end']
    if end - start > duration + 0.02:
        raise VoiceError('画面时长不足以容纳完整配音。')
    # The first input fixes output duration. The voice is the sidechain control and
    # remains audible independently; short gaps recover slowly rather than pumping.
    bgm_lufs = profile['bgm_lufs']
    threshold = 0.085
    ratio = min(20, max(2, 1 + profile['duck_db'] / 1.3))
    # Keep the existing sidechain attack/release/hold. New-policy plans taper
    # the first silent control frame; old manifests retain their prior mix.
    voice_end = end - start
    recovery = (profile['release_ms'] + profile['hold_ms']) / 1000
    from video_plan_policy import REVISION as VIDEO_POLICY_REVISION, PREVIOUS_REVISION, LEGACY_REVISION
    relative_ducking = item.get('duration_policy_version') == VIDEO_POLICY_REVISION
    if relative_ducking:
        # The compressor ratio is not a dB value. Calibrate the new policy's
        # ratio to its requested attenuation while preserving old manifests.
        ratio = min(8, max(2, 1 + profile['duck_db'] / 4))
        threshold = 0.24
    post_ramp = (f"volume='if(lt(t,{voice_end:.6f}),1,"
                 f"if(lt(t,{voice_end+recovery:.6f}),"
                 f"0.5+0.5*(t-{voice_end:.6f})/{recovery:.6f},1))':eval=frame"
                 if item.get('duration_policy_version') in (VIDEO_POLICY_REVISION,
                                                             PREVIOUS_REVISION, LEGACY_REVISION) else 'anull')
    fades = ''
    if not bgm.get('natural', True):
        fade_in = min(float(cfg.get('fade_in', 0)), duration/3)
        fade_out = min(float(cfg.get('fade_out', 0)), duration/3)
        if fade_in > 0: fades += f',afade=t=in:st=0:d={fade_in:.6f}'
        if fade_out > 0: fades += f',afade=t=out:st={duration-fade_out:.6f}:d={fade_out:.6f}'
    music_base = (f'aresample=48000,atrim=start={bgm["start"]:.9f}:duration={duration:.9f},'
                  f'asetpts=PTS-STARTPTS,volume={float(cfg.get("bgm_gain", .85)):.6f}'
                  if relative_ducking else
                  f'atrim=start={bgm["start"]:.6f}:duration={duration:.6f},'
                  f'asetpts=PTS-STARTPTS,aresample=48000,loudnorm=I={bgm_lufs}:TP=-2:LRA=9')
    # Strict video length is ceil(actual speech * fps). Keep both sidechain and
    # spoken streams alive through that same final frame; never cut speech or
    # synthesize missing BGM. Legacy items retain their original stream length.
    spoken_tail=(f'apad=whole_dur={duration:.9f}' if item.get('voice_match_revision') else 'anull')
    mix_duration='longest' if item.get('voice_match_revision') else 'first'
    # Equal audio packet sizes prevent a sidechain/amix EOF race. Any packet
    # padding is beyond the verified target and removed at that exact sample;
    # no missing music is synthesized inside the video interval.
    packet=',asetnsamples=n=1024:p=1' if item.get('voice_match_revision') else ''
    sample_end=f',atrim=end_sample={round(duration*48000)}' if item.get('voice_match_revision') else ''
    fc = (f'[0:a]{music_base},volume={10**(bgm_trim/20):.6f}{fades}{packet}[music];'
          f'[1:a]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,'
          f'aresample=48000,volume={10**(voice_gain/20):.6f},asplit=2[control0][spoken0];'
          f'[spoken0]{spoken_tail}{packet}[spoken];'
          f'[control0]apad=whole_dur={duration:.6f}{packet}[control];'
          f'[music][control]sidechaincompress=threshold={threshold}:ratio={ratio:.3f}:'
          f'attack={profile["attack_ms"]}:release={profile["release_ms"]+profile["hold_ms"]}[ducked0];'
          f'[ducked0]{post_ramp}[ducked];'
          f'[ducked][spoken]amix=inputs=2:duration={mix_duration}:dropout_transition=0,'
          f'alimiter=limit=0.85:level=false,aresample=48000{sample_end}[aout]')
    temp = output.with_suffix('.part.wav')
    _run([ffmpeg, '-hide_banner', '-nostdin', '-y', '-i', bgm['path'], '-i', voice['voice_processed'],
          '-filter_complex', fc, '-map', '[aout]', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', temp],
         timeout=max(120, duration * 5))
    with wave.open(str(temp), 'rb') as wav:
        if wav.getframerate() != 48000 or wav.getnchannels() != 2:
            raise VoiceError('最终混音不是48000Hz立体声。')
        if item.get('voice_match_revision') and wav.getnframes()!=round(duration*48000):
            raise VoiceError('严格配音混音采样数与视频帧边界不一致，未补齐音乐或截断配音。')
    measured = measure_loudness(temp, ffmpeg)
    peak=measured['true_peak_dbtp']
    if math.isnan(peak) or peak==math.inf:
        raise VoiceError('基础混音真峰值测量无效，禁止继续封装。')
    if peak > -1.0 and not defer_peak_validation:
        raise VoiceError(f'最终混音真峰值{measured["true_peak_dbtp"]:.2f}dBTP超过-1dBTP。')
    os.replace(temp, output)
    mix_info = dict(audio_mix_profile=profile_name, voice_lufs=profile['voice_lufs'],
                    bgm_lufs=None if relative_ducking else bgm_lufs,
                    duck_db=profile['duck_db'], sample_rate=48000, channels=2, **measured)
    if relative_ducking:
        mix_info['bgm_base_gain'] = cfg.get('bgm_gain', .85)
    if defer_peak_validation:
        mix_info['pcm_peak_validation']='deferred_to_final_aac'
        mix_info['pcm_peak_requires_final_check']=peak > -1.0
    return mix_info
