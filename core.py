# -*- coding: utf-8 -*-
"""Whole-clip folder assembler. No AI calls. Originals and the project index are read-only."""
from __future__ import annotations
from mac_paths import path as _portable_path

import csv
import hashlib
import json
import math
import os
from pathlib import Path
import random
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable

from video_processing import (PROCESSING_DEFAULTS, processing_settings, validate_processing,
                              normalize_processing_config, processing_warnings,
                              prepare_sampled_media, file_md5, build_clip_effects)
from local_media import local_media_server
from dedup_v2 import DEFAULTS as DEDUP_V2_DEFAULTS, validate_settings as validate_dedup_settings

APP_VERSION = "1.0.5-ab1-dedup2-stage5-cnumeric3-renderperf1-cache2-subtitle1-background1-logs1-framesafe1-smart2-wordfix1-profiles1-gcoverage1-voicematch1-package1-microtrim1-scriptcrud2-batchui1-voiceedge1-packagefit2-shottrim2-logfooter1-capacity2-externalvoice1-scriptexport1-recycle1-nopackage1-humanreview1-batchcontinue1-planrender2"
APP_VERSION += '-voicefilter1-history72h1-voicepair1-gainrange2-bgmlower1'
APP_VERSION += '-slotssearch1-historytime1'
APP_VERSION += '-historylockfix1'
APP_VERSION += '-gpuencode1'
APP_VERSION += '-workbenchui1-historyimport1'
APP_VERSION += '-qtworkbench1'
APP_VERSION += '-clipsfx1-ballmusic1'
APP_VERSION += '-aacpeak1'
APP_VERSION += '-sfxbatchgain1'
APP_VERSION += '-reviewqtfix1'
APP_VERSION += '-reviewnative2'
APP_VERSION += '-voiceentryqt1'
APP_VERSION += '-legacywindowfix1'
APP_VERSION += '-premixpeak2'
APP_VERSION += '-editorhost1-qtstateonly1-controlsrestore1'
APP_VERSION += '-framewait1'
APP_VERSION += '-scriptbalance2-shantouproduct1'
APP_VERSION += '-scriptidsort1'
APP_VERSION += '-ballscenerotation1'
APP_VERSION += '-sfxswitch1'
APP_VERSION += '-captionwords1-ballopening1'
APP_VERSION += '-productspec1-renderqueue1'
APP_VERSION += '-stackedlayout1-tealbar1-centerguide1-voicerate1'
APP_VERSION += '-unifiedvisual1'
APP_VERSION += '-toptextvisual1'
APP_VERSION += '-editorialtags1-speechpace1-firstreview1-pitch1-editstyles1'
APP_VERSION += '-captionposition1'
APP_VERSION += '-skdiverse1'
APP_VERSION += '-skscriptscope1'
APP_VERSION += '-subtitlethentop1'
APP_VERSION += '-commondurationtail1'
APP_VERSION += '-keywordpreset2'
APP_VERSION += '-skmaterialbias1'
APP_VERSION += '-groupfolderswitch1'
APP_DIR = Path(__file__).resolve().parent
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm", ".wmv", ".mts", ".m2ts"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg", ".opus", ".wma"}
GROUPS = ("A", "B", "C", "D")
STAGE_GROUPS = ("A", "B", "C", "D", "E")
LEGACY_STAGE_RULE_VERSION = "five_stage_v1_20260930"
C_FAMILY_RULE_VERSION = "five_stage_c_family_v2_20260930"
STAGE_RULE_VERSION = "five_stage_c_numeric_v3_20260930"
STAGE_RULE_VERSIONS = (LEGACY_STAGE_RULE_VERSION, C_FAMILY_RULE_VERSION, STAGE_RULE_VERSION)
BGM_DIRECTORY_RULE = 'classified_bgm_v1'


def groups_for_config(cfg):
    if cfg.get('material_format') == 'sk_diverse':
        from sk_materials import TYPES
        return TYPES
    return STAGE_GROUPS if cfg.get('selection_rule_version') in STAGE_RULE_VERSIONS else GROUPS
DEFAULTS = {
    "material_root": _portable_path(r"D:\DY video\人工处理素材\牛肉" if os.name != 'nt' else r"D:\DY video\人工处理素材"),
    "music_root": _portable_path(r"D:\DY video\音乐库"),
    "output_root": _portable_path(r"D:\DY video\整段组合成片"),
    "index_path": _portable_path(r"D:\DY video\项目管理\素材索引.csv"),
    "group_folders": {"A": "A", "B": "B", "C": "C", "D": "D", "E": "E"},
    "selection_rule_version": STAGE_RULE_VERSION,
    "count": 10, "min_seconds": 15.0, "max_seconds": 30.0,
    "preferred_min": 20.0, "preferred_max": 28.0,
    "c_min": 1, "c_max": 2, "max_clips": 24,
    "opening_c_min_per_batch": 1,
    "script_mode": "配音+字幕+BGM", "script_input_mode": "单篇",
    "video_mode_percentages": {"纯画面+BGM": 20, "字幕+BGM": 25,
                               "配音+BGM": 25, "配音+字幕+BGM": 30},
    "top_text_use_policy": "ratio", "top_text_use_percent": 40,
    "top_text_timing": "auto", "intro_top_text_duration": 2.5,
    "voice_end_hold_seconds": 0.8,
    "allow_script_repeat": False, "voice_strategy": "固定", "voice_selected_ids": [],
    "subtitle_position": "下中", "audio_mix_profile": "配音优先",
    "subtitle_custom_y": 0.72,
    "subtitle_font_size": 58, "subtitle_max_chars_per_line": 16,
    "subtitle_safe_margin_top": 0.10, "subtitle_safe_margin_bottom": 0.14,
    "subtitle_safe_margin_left": 0.08, "subtitle_safe_margin_right": 0.08,
    "voice_trim_db": 0.0, "voice_gain_db": 0.0, "bgm_trim_db": 0.0,
    "voice_cache_enabled": True,
    "a_weight": 0.6, "dedup_mode": "balanced", "pair_overlap_limit": 0.70,
    "music_mode": "natural", "selected_music": "", "bgm_gain": 0.85,
    "fade_out": 0.6, "fade_in": 0.0,
    "fps": 30, "width": 1080, "height": 1920, "crf": 18, "concurrency": 4,
    "ffmpeg": "", "ffprobe": "", "node": "", "remotion_project": "",
    "browser": "", "seed": "", "plan_seconds_per_video": 4.0,
    "attempts_per_video": 160, "source_audio": "discard",
    "top_text_enabled": True, "top_text_library": str(APP_DIR / "顶部文案库.txt"),
    "top_text_mode": "random", "top_text_fixed_index": 0,
    "top_text_font_size": 60, "top_text_top": 90, "top_text_max_width": 0.78,
    "top_text_stroke": 3,
}

DEFAULTS.update(PROCESSING_DEFAULTS)
DEFAULTS.update(DEDUP_V2_DEFAULTS)
from visual_layout import DEFAULTS as LAYOUT_DEFAULTS
from voice_speed import DEFAULTS as VOICE_SPEED_DEFAULTS
DEFAULTS.update(LAYOUT_DEFAULTS)
DEFAULTS.update(VOICE_SPEED_DEFAULTS)


def upgrade_render_settings(cfg: dict) -> dict:
    """Upgrade the old default once; later manual concurrency choices take priority."""
    result = dict(cfg)
    if 'render_performance_revision' not in result:
        if result.get('concurrency') == 2:
            result['concurrency'] = 4
        result['render_performance_revision'] = 1
    return result


class AssemblyError(RuntimeError):
    pass


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("w", encoding="utf-8", newline="\n") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        for attempt in range(6):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.10 * (attempt + 1))
    finally:
        if temp.exists():
            temp.unlink()


def read_json(path: Path, default: Any = None) -> Any:
    if not Path(path).exists():
        return default
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        raise AssemblyError(f"JSON文件无法读取，未自动覆盖：{path}\n{exc}") from exc


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def object_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()




def load_top_texts(path: str | Path) -> list[str]:
    """Read blank-line separated top-overlay texts. Internal line breaks are preserved."""
    p = Path(path)
    if not p.is_file():
        raise AssemblyError(f"顶部文案库不存在：{p}")
    raw = p.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    blocks = []
    current = []
    for line in raw.split("\n"):
        if line.strip():
            current.append(line.strip())
        elif current:
            blocks.append("\n".join(current).strip())
            current = []
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]

def natural_key(text: str):
    import re
    return [(1, int(p)) if p.isdecimal() else (0, p.casefold())
            for p in re.split(r"(\d+)", str(text))]


def creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def resolve_tool(name: str, explicit: str = "") -> str:
    if explicit.strip():
        p = Path(explicit.strip().strip('"')).expanduser()
        if p.is_file():
            return str(p.resolve())
        raise AssemblyError(f"配置的{name}不存在：{p}")
    found = shutil.which(name)
    if found:
        return found
    suffix = ".exe" if os.name == "nt" else ""
    for base in (APP_DIR / "tools", APP_DIR / "tools" / "ffmpeg" / "bin"):
        candidate = base / (name + suffix)
        if candidate.is_file():
            return str(candidate)
    if os.name == "nt":
        roots = {"node": [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs"],
                 "ffmpeg": [Path(r"C:\ffmpeg\bin"), Path(_portable_path(r"D:\ffmpeg\bin"))],
                 "ffprobe": [Path(r"C:\ffmpeg\bin"), Path(_portable_path(r"D:\ffmpeg\bin"))]}
        for base in roots.get(name, []):
            candidate = base / (name + ".exe")
            if candidate.is_file():
                return str(candidate)
    raise AssemblyError(f"未找到{name}。请在「环境设置」中选择{name}{suffix}，或加入PATH。")


def run_capture(args: list[str], timeout: float = 90) -> str:
    import runtime_diagnostics as diag
    if diag.hub():
        try:return diag.process(args,timeout=timeout,stage_name=Path(args[0]).name)
        except (diag.ProcessFailure,OSError) as exc:raise AssemblyError(str(exc)) from exc
    try:
        p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=creation_flags())
    except subprocess.TimeoutExpired as exc:
        raise AssemblyError(f"命令超时（{timeout}秒）：{Path(args[0]).name}") from exc
    except OSError as exc:
        raise AssemblyError(f"无法启动：{args[0]}\n{exc}") from exc
    if p.returncode:
        raise AssemblyError(f"{Path(args[0]).name} 执行失败：\n{(p.stderr or p.stdout)[-3500:]}")
    return p.stdout


def _duration(stream: dict, container: dict) -> tuple[float, str]:
    candidates = []
    try:
        from fractions import Fraction
        candidates.append((float(stream["duration_ts"]) * float(Fraction(stream["time_base"])), "stream_ts"))
    except (ValueError, KeyError, TypeError, ZeroDivisionError):
        pass
    for key, obj in (("stream", stream), ("container_fallback", container)):
        try:
            candidates.append((float(obj["duration"]), key))
        except (ValueError, KeyError, TypeError):
            pass
    for value, origin in candidates:
        if math.isfinite(value) and value > 0:
            return value, origin
    raise AssemblyError("无法读取有效媒体时长")


def probe(path: Path, ffprobe: str) -> dict:
    data = json.loads(run_capture([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)], 90))
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    primary = video or audio
    if not primary:
        raise AssemblyError(f"文件没有可用视频或音频流：{path}")
    duration, origin = _duration(primary, data.get("format", {}))
    audio_duration = _duration(audio, data.get("format", {}))[0] if audio else None
    rotation = float(next((s.get('rotation', 0) for s in (video or {}).get('side_data_list', []) if 'rotation' in s),
                          (video or {}).get('tags', {}).get('rotate', 0))) % 360
    width, height = (video or {}).get('width', 0), (video or {}).get('height', 0)
    sar = (video or {}).get('sample_aspect_ratio', '1:1')
    try:
        sa, sb = (float(v) for v in sar.split(':')); display_width = round(width * sa / sb) if sa and sb else width
    except (ValueError, ZeroDivisionError):
        display_width = width
    display_width, display_height = ((height, display_width) if round(rotation) % 180 == 90 else (display_width, height))
    return {"duration": duration, "duration_basis": origin,
            "rotation": rotation, "display_width": display_width, "display_height": display_height,
            "sample_aspect_ratio": sar,
            "has_video": bool(video), "has_audio": bool(audio),
            "video_codec": (video or {}).get("codec_name"), "audio_codec": (audio or {}).get("codec_name"),
            "audio_sample_rate": int((audio or {}).get("sample_rate") or 0),
            "width": (video or {}).get("width", 0), "height": (video or {}).get("height", 0),
            "fps_native": (video or {}).get("avg_frame_rate", "0/1"),
            "nb_frames": (video or {}).get("nb_frames"), "audio_duration": audio_duration,
            "audio_stream_count": sum(s.get("codec_type") == "audio" for s in streams),
            "video_stream_count": sum(s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams)}


class Store:
    """Technical cache and composition usage only. NOT a replacement project Material Index."""
    def __init__(self, data_dir: Path):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.dbpath = self.root / "assembly_runtime.sqlite3"
        with self.connect() as c:
            c.executescript("""
              CREATE TABLE IF NOT EXISTS media_cache(path TEXT PRIMARY KEY,size INTEGER,mtime_ns INTEGER,payload TEXT);
              CREATE TABLE IF NOT EXISTS audio_duration_cache(hash TEXT PRIMARY KEY,duration REAL);
              CREATE TABLE IF NOT EXISTS flags(hash TEXT PRIMARY KEY,enabled INTEGER NOT NULL DEFAULT 1,reviewed INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS history(output_id TEXT PRIMARY KEY,sequence_hash TEXT NOT NULL,set_hash TEXT NOT NULL,payload TEXT NOT NULL,used_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS reservations(output_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,sequence_hash TEXT NOT NULL,payload TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS batch_retention(batch_id TEXT PRIMARY KEY,last_started_at TEXT NOT NULL,expires_at TEXT NOT NULL,state TEXT NOT NULL,completed_count INTEGER NOT NULL DEFAULT 0);
            """)

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.dbpath, timeout=30)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA busy_timeout=30000")
            with c:
                yield c
        finally:
            c.close()

    def inspect(self, path: Path, tool: str) -> tuple[dict, bool]:
        path = Path(path).resolve()
        before = path.stat()
        with self.connect() as c:
            row = c.execute("SELECT size,mtime_ns,payload FROM media_cache WHERE path=?", (str(path),)).fetchone()
        if row and (row[0], row[1]) == (before.st_size, before.st_mtime_ns):
            result = json.loads(row[2])
            return result, True
        result = probe(path, tool)
        digest = sha256(path)
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise AssemblyError(f"扫描期间文件被修改，请完成编辑后重新扫描：{path}")
        result.update(path=str(path), name=path.name, hash=digest, size=after.st_size, mtime_ns=after.st_mtime_ns)
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO media_cache VALUES(?,?,?,?)",
                      (str(path), after.st_size, after.st_mtime_ns, json.dumps(result, ensure_ascii=False)))
        return result, False

    def music_duration(self, media: dict, ffmpeg: str) -> float:
        # MP3/AAC container duration may include encoder delay/padding. Decode audio once
        # to a null sink and cache the final sample time; never modify the music file.
        with self.connect() as c:
            row = c.execute("SELECT duration FROM audio_duration_cache WHERE hash=?", (media["hash"],)).fetchone()
        if row:
            return row[0]
        text = run_capture([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-nostats",
                            "-progress", "pipe:1", "-i", media["path"], "-map", "0:a:0", "-vn",
                            "-af", "asetpts=PTS-STARTPTS", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", "-f", "null", "-"], 180)
        times = [float(line.split("=", 1)[1])/1_000_000 for line in text.splitlines() if line.startswith("out_time_us=") and line.split("=",1)[1] not in ("N/A", "")]
        duration = max(times, default=0)
        if duration <= 0:
            raise AssemblyError("无法确认音乐解码后的有效时长")
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO audio_duration_cache VALUES(?,?)", (media["hash"], duration))
        return duration

    def flags(self, digest: str) -> dict:
        with self.connect() as c:
            row = c.execute("SELECT enabled,reviewed FROM flags WHERE hash=?", (digest,)).fetchone()
        return {"enabled": bool(row[0]) if row else True, "reviewed": bool(row[1]) if row else False}

    def set_flag(self, digest: str, *, enabled=None, reviewed=None):
        flags = self.flags(digest)
        if enabled is not None:
            flags["enabled"] = bool(enabled)
        if reviewed is not None:
            flags["reviewed"] = bool(reviewed)
        with self.connect() as c:
            c.execute("INSERT OR REPLACE INTO flags VALUES(?,?,?)", (digest, int(flags["enabled"]), int(flags["reviewed"])))

    def usage(self, exclude_batch: str = "") -> list[dict]:
        with self.connect() as c:
            used = c.execute("SELECT payload FROM history ORDER BY used_at DESC").fetchall()
            reserved = c.execute("SELECT payload FROM reservations WHERE batch_id != ?", (exclude_batch,)).fetchall()
            retired={r[0] for r in c.execute("SELECT batch_id FROM batch_retention WHERE state!='open'")}
        records = [json.loads(x[0]) for x in used + reserved]
        known = {item.get('id') for item in records}
        # Recover valid old manifests without decoding media or inventing usage.
        # Abandoned plans are deliberately excluded: cancellation releases them.
        for path in (self.root / 'batches').glob('*/batch_manifest.json'):
            try:
                batch = read_json(path)
                if batch.get('id') in retired:continue
                if not isinstance(batch, dict) or not isinstance(batch.get('items'), list):
                    raise ValueError('缺少批次项目')
                if batch.get('id') == exclude_batch:
                    continue
                abandoned = bool(batch.get('abandoned_at'))
                for item in batch.get('items', []):
                    if not isinstance(item, dict) or not item.get('id'):
                        raise ValueError('项目缺少成片编号')
                    if item['id'] in known:
                        continue
                    status = item.get('status')
                    if status == 'completed' or (not abandoned and status in ('planned', 'failed', 'rendering')):
                        records.append(item)
                        known.add(item['id'])
            except (OSError, ValueError, TypeError, KeyError, AssemblyError) as exc:
                raise AssemblyError(f'无法完整读取历史Manifest：{path}；{exc}') from exc
        return records

    def reserve(self, batch: dict):
        from sk_selection import assert_reservable
        assert_reservable(batch, self)
        with self.connect() as c:
            for item in batch["items"]:
                if item.get('status') != 'planned':
                    continue
                c.execute("INSERT OR REPLACE INTO reservations VALUES(?,?,?,?)",
                          (item["id"], batch["id"], item["sequence_hash"], json.dumps(item, ensure_ascii=False)))

    def abandon(self, batch_id: str):
        with self.connect() as c:
            c.execute("DELETE FROM reservations WHERE batch_id=?", (batch_id,))

    def record_success(self, item: dict):
        with self.connect() as c:
            c.execute("INSERT OR IGNORE INTO history VALUES(?,?,?,?,?)", (item["id"], item["sequence_hash"], item["set_hash"], json.dumps(item, ensure_ascii=False), now()))
            c.execute("DELETE FROM reservations WHERE output_id=?", (item["id"],))


def index_links(path: str, log: Callable[[str], None]) -> tuple[dict, dict]:
    """Read-only identity association by exact path or explicit clip content hash. No approvals."""
    if not path or not Path(path).is_file():
        return {}, {}
    rows = None
    for enc in ("utf-8-sig", "gb18030"):
        try:
            with open(path, encoding=enc, newline="") as f:
                rows = list(csv.DictReader(f))
            break
        except UnicodeError:
            pass
    if rows is None:
        log("现有素材索引编码无法读取；未写入或修改该索引。")
        return {}, {}
    by_path, by_hash = {}, {}
    for row in rows:
        info = {k: row.get(k, "") for k in ("clip_id", "source_id", "mixing_eligible", "commerce_safety", "rights_status", "usable_status")}
        p = row.get("clip_path", "").strip()
        if p:
            by_path[os.path.normcase(os.path.abspath(p))] = info
        # Deliberately exclude source_fingerprint: it identifies a different file.
        for key in ("clip_sha256", "file_sha256", "media_sha256", "clip_hash"):
            val = row.get(key, "").strip().lower()
            if len(val) == 64 and all(ch in "0123456789abcdef" for ch in val):
                by_hash.setdefault(val, []).append(info)
    log(f"只读关联现有索引 {len(rows)} 行；不会据此新增身份或审批。")
    return by_path, by_hash


def validate_config(cfg: dict):
    from sk_materials import validate_config as validate_sk
    validate_sk(cfg)
    from visual_layout import settings as layout_settings
    from voice_speed import settings as speed_settings
    layout_settings(cfg);speed_settings(cfg)
    from voice_edge_fitting import validate_settings as validate_voice_edges
    try:validate_voice_edges(cfg)
    except ValueError as exc:raise AssemblyError(str(exc)) from exc
    if cfg.get('material_format') == 'grouped_combo_independent':
        if cfg.get('sequence_strategy','random') != 'random':
            raise AssemblyError('G组第一版只支持随机混剪，不使用烹饪/首镜算法。')
        if not cfg.get('coverage_dedup_enabled',True):
            raise AssemblyError('G组的覆盖硬约束不能关闭。')
        if cfg.get('package_count_matching_enabled'):
            margin=float(cfg.get('package_anchor_margin_seconds',.2))
            if not math.isfinite(margin) or not 0<=margin<=.5:
                raise AssemblyError('包装锚点结束余量须在0～0.5秒之间。')
    if cfg.get('video_policy_version'):
        from video_plan_policy import REVISION, PREVIOUS_REVISION, LEGACY_REVISION, mode_counts, _percent
        if cfg['video_policy_version'] not in (REVISION, PREVIOUS_REVISION, LEGACY_REVISION):
            raise AssemblyError('未知视频模式/顶部文案策略版本。')
        try:
            mode_counts(cfg)
            if cfg.get('top_text_use_policy') not in ('none', 'all', 'ratio'):
                raise ValueError('未知顶部文案使用策略。')
            if cfg.get('top_text_timing') not in ('auto', 'start', 'after_voice', 'full'):
                raise ValueError('未知顶部文案出现时机。')
            _percent(cfg.get('top_text_use_percent'))
            if cfg['video_policy_version'] == LEGACY_REVISION:
                if cfg.get('post_voice_policy') not in ('plain', 'top', 'mix'):
                    raise ValueError('未知配音后顶部文案策略。')
                plain = _percent(cfg.get('post_voice_plain_percent'))
                top = _percent(cfg.get('post_voice_top_percent'))
                if cfg.get('post_voice_policy') == 'mix' and plain + top != 100:
                    raise ValueError('配音结束后两种方式的比例合计必须为100%。')
            from video_plan_policy import intro_duration_used
            if (cfg['video_policy_version'] != REVISION or intro_duration_used(cfg)) and not 0 < float(cfg.get('intro_top_text_duration')) <= 10:
                raise ValueError('开头顶部文案时长需大于0且不超过10秒。')
            if not 0.5 <= float(cfg.get('voice_end_hold_seconds')) <= 1.0:
                raise ValueError('配音结尾保护需在0.5～1.0秒。')
        except (ValueError, TypeError) as exc:
            raise AssemblyError(str(exc)) from exc
    if cfg.get('script_mode', '纯画面+BGM') not in ('纯画面+BGM', '字幕+BGM', '配音+BGM', '配音+字幕+BGM'):
        raise AssemblyError('未知文案/配音模式。')
    if cfg.get('subtitle_position', '下中') not in ('顶部', '上中', '居中', '下中', '底部安全区', '自定义'):
        raise AssemblyError('未知字幕位置。')
    if not 0.1 <= float(cfg.get('subtitle_custom_y', 0.72)) <= 0.9:
        raise AssemblyError('自定义字幕高度必须在0.1到0.9之间。')
    if not 28 <= int(cfg.get('subtitle_font_size', 58)) <= 100 or not 8 <= int(cfg.get('subtitle_max_chars_per_line', 16)) <= 30:
        raise AssemblyError('字幕字号需28～100，每行字数需8～30。')
    if any(not 0 <= float(cfg.get(key, default)) <= 0.3 for key,default in (
        ('subtitle_safe_margin_top',0.10),('subtitle_safe_margin_bottom',0.14),
        ('subtitle_safe_margin_left',0.08),('subtitle_safe_margin_right',0.08))):
        raise AssemblyError('字幕安全边距需在0到0.3之间。')
    if cfg.get('audio_mix_profile', '配音优先') not in ('配音优先', '均衡', '音乐突出'):
        raise AssemblyError('未知混音模式。')
    gain = float(cfg.get('voice_gain_db', 0))
    if not -30 <= gain <= 24 or not math.isfinite(gain) or gain != round(gain):
        raise AssemblyError('配音音量必须在-30到+24 dB，步进1 dB。')
    if not -6 <= float(cfg.get('voice_trim_db',0)) <= 6:
        raise AssemblyError('旧配音微调增益只能在-6到+6 dB。')
    if not -60 <= float(cfg.get('bgm_trim_db',0)) <= 12:
        raise AssemblyError('BGM音量必须在-60到+12 dB。')
    validate_dedup_settings(cfg)
    try:
        validate_processing(cfg)
    except (TypeError, ValueError) as exc:
        raise AssemblyError(str(exc)) from exc
    for k in ("min_seconds", "max_seconds", "preferred_min", "preferred_max", "plan_seconds_per_video"):
        if not math.isfinite(float(cfg[k])) or float(cfg[k]) <= 0:
            raise AssemblyError(f"参数{k}必须是大于0的有限数值")
    if not 0 < cfg["min_seconds"] <= cfg["preferred_min"] <= cfg["preferred_max"] <= cfg["max_seconds"] <= 600:
        raise AssemblyError("时长必须满足：最短 ≤ 优先最短 ≤ 优先最长 ≤ 最长，且最长不超过600秒。")
    if groups_for_config(cfg) == GROUPS and not 0 <= cfg["c_min"] <= cfg["c_max"] <= 10:
        raise AssemblyError("中间2数量须满足 0 ≤ 最少 ≤ 最多 ≤ 10。")
    if not 1 <= cfg["count"] <= 500:
        raise AssemblyError("批量数量为1～500。")
    if not 0 <= int(cfg.get("opening_c_min_per_batch", 0)) <= cfg["count"]:
        raise AssemblyError("C/开头首镜最低条数须在0到本批生成数量之间。")
    if not 2 <= cfg["max_clips"] <= 100:
        raise AssemblyError("片段数量上限须为2～100。")
    if cfg["fps"] not in (24, 25, 30, 50, 60):
        raise AssemblyError("输出帧率请选择24、25、30、50或60。")
    if any(int(cfg[k]) < 64 or int(cfg[k]) % 2 for k in ("width", "height")):
        raise AssemblyError("输出宽高必须为不小于64的偶数。")
    if not 0 < float(cfg["bgm_gain"]) <= 1 or not 0 <= float(cfg["fade_out"]) <= 5 or not 0 <= float(cfg["fade_in"]) <= 5:
        raise AssemblyError("BGM音量范围(0,1]，淡入/淡出范围0～5秒。")
    if cfg["music_mode"] not in ("natural", "fade"):
        raise AssemblyError("未知音乐结束模式。")
    if cfg.get("source_audio") != "discard":
        raise AssemblyError("本版本固定去除素材原音轨，不支持保留原声。")
    if cfg["dedup_mode"] not in ("balanced", "strict"):
        raise AssemblyError("未知去重模式。")
    if cfg.get('top_text_style'):
        from top_text_styles import normalize as normalize_top_style
        try:normalize_top_style(cfg['top_text_style'])
        except ValueError as exc:raise AssemblyError(str(exc)) from exc
    if cfg.get("top_text_mode") not in ("random", "sequence"):
        raise AssemblyError("顶部文案抽取模式只能是random或sequence。")
    if not 28 <= int(cfg.get("top_text_font_size", 60)) <= 140:
        raise AssemblyError("顶部文案字号请使用28～140。")
    if not 0 <= int(cfg.get("top_text_top", 90)) <= 700:
        raise AssemblyError("顶部文案上边距请使用0～700像素。")
    if not 0.35 <= float(cfg.get("top_text_max_width", 0.78)) <= 0.95:
        raise AssemblyError("顶部文案最大宽度请使用0.35～0.95。")
    if not 0 <= int(cfg.get("top_text_stroke", 3)) <= 8:
        raise AssemblyError("顶部文案描边请使用0～8像素。")
    if int(cfg.get("top_text_fixed_index", 0)) < 0:
        raise AssemblyError("固定文案编号不能小于0；0表示自动选择。")
    if not 0 < float(cfg["pair_overlap_limit"]) <= 1 or not 0 <= cfg["a_weight"] <= 1:
        raise AssemblyError("相似度/抽取权重参数不合法。")
    folders = cfg["group_folders"]
    expected_groups = groups_for_config(cfg)
    if set(folders) != set(expected_groups) or len(set(folders.values())) != len(expected_groups):
        raise AssemblyError(f"{len(expected_groups)}组文件夹必须分别指定且不能相同。")
    for val in folders.values():
        if Path(val).name != val or val in ("", ".", "..") or "/" in val or "\\" in val:
            raise AssemblyError("组文件夹名只能是素材根目录下的单层目录名。")


def scan_library(cfg: dict, store: Store, log=lambda msg: None, stop=None) -> tuple[list[dict], list[dict], list[str]]:
    tool = resolve_tool("ffprobe", cfg["ffprobe"])
    audio_tool = resolve_tool("ffmpeg", cfg["ffmpeg"])
    root, music_root = Path(cfg["material_root"]), Path(cfg["music_root"])
    if not root.is_dir() or not music_root.is_dir():
        raise AssemblyError("素材根目录或音乐目录不存在，请先选择正确路径。")
    from editing_profiles import grouped
    by_path, by_hash = ({},{}) if grouped(cfg) else index_links(cfg.get("index_path", ""), log)
    clips, music, warnings = [], [], []
    hits = 0
    if cfg.get('material_format') == 'sk_diverse':
        from sk_materials import scan_materials
        clips,warnings=scan_materials(cfg,store,tool,log,stop=stop)
    elif grouped(cfg):
        from grouped_materials import scan_materials
        clips,warnings=scan_materials(cfg,store,tool,log)
    for group in (() if grouped(cfg) else groups_for_config(cfg)):
        folder = root / cfg["group_folders"][group]
        if not folder.is_dir():
            warnings.append(f"缺少组文件夹：{folder}")
            continue
        stage5 = groups_for_config(cfg) == STAGE_GROUPS
        paths = sorted((p for p in (folder.rglob('*') if stage5 else folder.iterdir())
                        if p.is_file() and p.suffix.lower() in VIDEO_EXTS),
                       key=lambda p: natural_key(p.relative_to(root).as_posix()))
        for p in paths:
            try:
                meta, hit = store.inspect(p, tool)
                hits += int(hit)
                if not meta["has_video"]:
                    raise AssemblyError("没有视频流")
                relative = p.relative_to(root).as_posix()
                item = dict(meta, group=group, display=relative, **store.flags(meta["hash"]))
                if stage5:
                    from selection_stages import classify_relative
                    item.update(classify_relative(relative, cfg))
                row = by_path.get(os.path.normcase(str(p.resolve())))
                linked = by_hash.get(meta["hash"], [])
                if row is None and len(linked) == 1:
                    row = linked[0]
                item["project_link"] = row or {}
                item["identity_key"] = ("clip:" + row["clip_id"]) if row and row.get("clip_id") else "sha256:" + meta["hash"]
                item["source_id"] = (row or {}).get("source_id", "")
                clips.append(item)
                if meta["duration_basis"] == "container_fallback":
                    warnings.append(f"{item['display']}：视频流缺少独立时长，使用容器时长；请确认末尾无多余静帧。")
                log(f"{group} | {p.name} | {meta['duration']:.3f}秒 | {'缓存' if hit else '已读取'}")
            except Exception as exc:
                warnings.append(f"跳过 {p.relative_to(root).as_posix()}：{exc}")
    # New beef-ball profiles use the explicitly selected dedicated pool.
    from editing_profiles import dedicated_ball_music
    music_paths = []
    for p in (() if dedicated_ball_music(cfg) else music_root.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            warnings.append(f'发现未分类BGM，本次不会自动使用：{p.name}')
    if dedicated_ball_music(cfg):
        music_paths.extend((p, 'beef_ball_pool') for p in music_root.rglob('*')
                           if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    for folder_name, bgm_type in (() if dedicated_ball_music(cfg) else (('歌词', 'lyrics'), ('纯音乐', 'instrumental'))):
        folder = music_root / folder_name
        if folder.is_dir():
            music_paths.extend((p, bgm_type) for p in folder.rglob('*')
                               if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    for p, bgm_type in sorted(music_paths, key=lambda entry: natural_key(str(entry[0]))):
        try:
            meta, hit = store.inspect(p, tool)
            if not meta["has_audio"]:
                raise AssemblyError("没有音频流")
            item = dict(meta, bgm_type=bgm_type)
            item["duration"] = store.music_duration(meta, audio_tool)
            item["duration_basis"] = "decoded_audio_samples"
            music.append(item)
        except Exception as exc:
            warnings.append(f"跳过音乐 {p.name}：{exc}")
    log(f"扫描完成：{len(clips)}条视频，{len(music)}首音乐；视频缓存命中{hits}。")
    return clips, music, warnings


def clip_frames(seconds: float, fps: int) -> int:
    # Ceiling prevents intentionally trimming the last fractional frame. Playback remains 1x.
    return max(1, math.ceil(seconds * fps - 1e-7))


def choice_weighted(rng: random.Random, values: list, weights: list[float]):
    if not values:
        raise AssemblyError("没有可选候选")
    return rng.choices(values, weights=weights, k=1)[0]


def sequence_key(clips: list[dict]) -> str:
    return object_hash([c["identity_key"] for c in clips])


def set_key(clips: list[dict]) -> str:
    return object_hash(sorted(c["identity_key"] for c in clips))


def validate_sequence(clips: list[dict], cfg: dict):
    from editing_profiles import grouped
    if grouped(cfg):
        from grouped_materials import validate_sequence as validate_grouped
        return validate_grouped(clips,cfg)
    if groups_for_config(cfg) == STAGE_GROUPS:
        from selection_stages import validate_stage_sequence
        return validate_stage_sequence(clips, cfg)
    if len(clips) < 2 or clips[0]["group"] != "A" or clips[-1]["group"] != "D":
        raise AssemblyError("组合必须开头池起镜、结尾池结束。")
    entered_c = False
    c_count = 0
    ids, hashes = set(), set()
    for i, item in enumerate(clips):
        g = item["group"]
        if g == "D" and i != len(clips) - 1:
            raise AssemblyError("结尾只能使用1段，且必须最后出现。")
        if g == "C":
            entered_c = True
            c_count += 1
        if entered_c and g in ("A", "B"):
            raise AssemblyError("进入中间2后不能回到开头/中间。")
        if g not in GROUPS:
            raise AssemblyError("出现未知组")
        if item["identity_key"] in ids or item["hash"] in hashes:
            raise AssemblyError("同一条视频不能重复使用同素材/相同内容。")
        ids.add(item["identity_key"])
        hashes.add(item["hash"])
    if not cfg["c_min"] <= c_count <= cfg["c_max"]:
        raise AssemblyError("中间2数量不满足配置。")


def music_candidates(music: list[dict], ranges: list[dict], cfg: dict,
                     narration_enabled: bool = False) -> list[dict]:
    result = []
    classified = cfg.get('bgm_rule_version') == BGM_DIRECTORY_RULE
    from editing_profiles import dedicated_ball_music
    dedicated = dedicated_ball_music(cfg)
    allowed = {m["hash"]: m for m in music
               if (not cfg["selected_music"] or m["hash"] == cfg["selected_music"])
               and (not classified or m.get('bgm_type') in (('beef_ball_pool',) if dedicated else ('lyrics', 'instrumental')))
               and (not classified or dedicated or not narration_enabled or m.get('bgm_type') == 'instrumental')}
    if cfg["music_mode"] == "natural":
        for r in ranges:
            m = allowed.get(r.get("music_hash"))
            if m is None or not r.get("confirmed"):
                continue
            start, end = float(r["start"]), float(r["end"])
            if not 0 <= start < end <= m["duration"] + 1e-6:
                continue
            n = math.floor((end - start) * cfg["fps"] + 1e-7)
            if (cfg.get('match_video_to_voice') and n>0 or
                    math.ceil(cfg["min_seconds"] * cfg["fps"]) <= n <= math.floor(cfg["max_seconds"] * cfg["fps"])):
                result.append({"media": m, "range_id": r["id"], "start": start, "end_limit": end,
                               "target_frames": n, "natural": True, "note": r.get("note", "")})
    else:
        for m in allowed.values():
            if m["duration"] + 1e-6 >= (1/cfg['fps'] if cfg.get('match_video_to_voice') else cfg["min_seconds"]):
                result.append({"media": m, "range_id": "fade-from-zero", "start": 0.0,
                               "end_limit": m["duration"], "target_frames": None, "natural": False,
                               "note": "用户选择从0秒使用并允许音乐截尾淡出；未声称自然乐句结束"})
    return result


def validate_bgm_classification(item: dict, cfg: dict) -> None:
    if cfg.get('bgm_rule_version') != BGM_DIRECTORY_RULE:
        return  # Previously saved batches retain their own music plans.
    bgm = item['bgm']
    try:
        relative = Path(bgm['path']).relative_to(Path(cfg['music_root']))
    except ValueError as exc:
        raise AssemblyError('BGM不在当前音乐分类目录内。') from exc
    from editing_profiles import dedicated_ball_music
    if dedicated_ball_music(cfg):
        if not relative.parts or '..' in relative.parts or relative.suffix.lower() not in AUDIO_EXTS:
            raise AssemblyError('牛肉丸BGM不在所选专用音乐目录内。')
        if not Path(bgm['path']).resolve().is_relative_to(Path(cfg['music_root']).resolve()):
            raise AssemblyError('牛肉丸BGM实际路径不在所选专用音乐目录内。')
        actual = 'beef_ball_pool'
    else:
        actual = {'歌词':'lyrics', '纯音乐':'instrumental'}.get(relative.parts[0]) if len(relative.parts) >= 2 else None
    if actual is None or bgm.get('bgm_type') != actual or item.get('bgm_type') != actual or item.get('bgm_path') != bgm['path']:
        raise AssemblyError('BGM路径、目录分类与已保存方案不一致，禁止渲染。')
    if item.get('narration_enabled') and actual not in ('instrumental', 'beef_ball_pool'):
        raise AssemblyError('NO_SUITABLE_INSTRUMENTAL_BGM：当前视频包含配音，但纯音乐库中没有符合条件的BGM。')


def plan_batch(clips: list[dict], music: list[dict], ranges: list[dict], cfg: dict,
               store: Store, log=lambda msg: None, stop: threading.Event | None = None,
               script_packages: list[dict] | None = None) -> dict:
    from visual_layout import finalize as finalize_layout
    from product_catalog import check_package
    for package in script_packages or []:check_package(package,cfg)
    from editing_profiles import FRESH,ball_profile,grouped
    for package in script_packages or []:
        if (package and package.get('script_profile_id') is not None and
                package['script_profile_id'] != cfg.get('editing_profile_id', FRESH)):
            raise AssemblyError('文案品类与剪辑方案不一致，未进入选片或渲染。')
    if cfg.get('editorial_enabled'):
        from editorial import capture,prepare_packages as prepare_editorial_packages
        clips,cfg=capture(clips,cfg,store.root)
        script_packages=prepare_editorial_packages(script_packages,cfg,log)
    if cfg.get('clip_sfx_enabled'):
        from clip_sfx import capture_config
        cfg=capture_config(cfg,store.root)
    if ball_profile(cfg) and grouped(cfg):
        from ball_scene_rotation import capture_config as capture_scene_config
        cfg=capture_scene_config(cfg,store.root)
    from editing_profiles import new_plan_config
    cfg=new_plan_config(cfg)
    from voice_duration_matching import resolve_schedule
    cfg=resolve_schedule(cfg,script_packages)
    from editing_profiles import grouped
    if grouped(cfg):
        from grouped_planner import plan_grouped_batch
        return finalize_layout(plan_grouped_batch(clips,music,ranges,cfg,store,log,stop,script_packages))
    if groups_for_config(cfg) == STAGE_GROUPS:
        from selection_stages import plan_stage_batch
        return finalize_layout(plan_stage_batch(clips, music, ranges, cfg, store, log, stop, script_packages))
    if script_packages:
        raise AssemblyError('文案配音只支持当前五阶段新方案；旧批次继续按原Manifest执行。')
    from timeline_processing import needs_advanced_planner, plan_timeline_batch
    if cfg.get('selection_version', 'v2') == 'v2' or needs_advanced_planner(cfg):
        return finalize_layout(plan_timeline_batch(clips, music, ranges, cfg, store, log, stop))
    cfg = normalize_processing_config(cfg)
    validate_config(cfg)
    processing = processing_settings(cfg)
    rate = processing['speed']
    active = [dict(c, frames=clip_frames(c["duration"] / rate, cfg["fps"])) for c in clips if c.get("enabled")]
    if any(not c.get("reviewed") for c in active):
        raise AssemblyError("请先确认启用的当前版本视频已经人工复核、可整段使用。")
    pools = {g: [c for c in active if c["group"] == g] for g in GROUPS}
    if not pools["A"] or not pools["D"] or len(pools["C"]) < cfg["c_min"]:
        raise AssemblyError("开头/结尾必须有启用素材；中间2设为必选时也不能为空。")
    tracks = music_candidates(music, ranges, cfg)
    if not tracks:
        raise AssemblyError("没有满足时长的音乐区间。自然模式请在「音乐区间」确认起止点；或主动切换为「允许音乐截尾淡出」。程序不会裁视频凑音乐。")
    top_texts = load_top_texts(cfg["top_text_library"]) if cfg.get("top_text_enabled") else []
    if cfg.get("top_text_enabled") and not top_texts:
        raise AssemblyError("顶部文案已启用，但文案库里没有可用文案。")
    fixed_text_index = int(cfg.get("top_text_fixed_index", 0) or 0)
    if fixed_text_index and not 1 <= fixed_text_index <= len(top_texts):
        raise AssemblyError(f"固定文案编号超出范围：当前文案库共{len(top_texts)}条。")
    seed = str(cfg.get("seed") or uuid.uuid4().hex)
    rng = random.Random(seed)
    history = store.usage()
    seen_sequences = {h["sequence_hash"] for h in history}
    seen_sets = {h["set_hash"] for h in history if h.get("set_hash")}
    usage = Counter(c["identity_key"] for h in history for c in h["clips"])
    starts = Counter(h["clips"][0]["identity_key"] for h in history if h["clips"])
    ends = Counter(h["clips"][-1]["identity_key"] for h in history if h["clips"])
    music_usage = Counter(h.get("bgm", {}).get("hash", "") for h in history)
    text_usage = Counter(h.get("top_text", {}).get("hash", "") for h in history if h.get("top_text", {}).get("enabled"))
    batch_text_hashes = set()
    batch_id = "assembly_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    batch = {"schema_version": 1, "app_version": APP_VERSION, "id": batch_id, "created_at": now(),
             "config": cfg, "seed": seed, "source_audio": "discard", "scope": "manual_whole_clip_assembly",
             "approval_note": "用户确认的整段组合输出；本程序未批准或更改原项目商业/权利/正式资格。",
             "items": [], "warnings": processing_warnings(cfg), "requested_count": cfg["count"]}
    minf, maxf = math.ceil(cfg["min_seconds"] * cfg["fps"]), math.floor(cfg["max_seconds"] * cfg["fps"])
    pref = ((cfg["preferred_min"] + cfg["preferred_max"]) / 2) * cfg["fps"]

    def weight(c, role=""):
        score = (1 + usage[c["identity_key"]]) ** -0.65
        if role == "first":
            score /= (1 + starts[c["identity_key"]]) ** 0.8
        if role == "last":
            score /= (1 + ends[c["identity_key"]]) ** 0.8
        return score

    def choose_top_text(index: int) -> dict:
        if not cfg.get("top_text_enabled"):
            return {"enabled": False, "text": "", "index": 0, "hash": "", "source": cfg.get("top_text_library", "")}
        if fixed_text_index:
            chosen_index = fixed_text_index - 1
        elif cfg.get("top_text_mode") == "sequence":
            chosen_index = index % len(top_texts)
        else:
            candidates = list(range(len(top_texts)))
            unused = [i for i in candidates if object_hash(top_texts[i]) not in batch_text_hashes]
            if unused:
                candidates = unused
            weights = [1 / (1 + text_usage[object_hash(top_texts[i])]) for i in candidates]
            chosen_index = choice_weighted(rng, candidates, weights)
        value = top_texts[chosen_index]
        digest = object_hash(value)
        return {"enabled": True, "text": value, "index": chosen_index + 1, "hash": digest,
                "source": str(Path(cfg["top_text_library"]).resolve())}

    for index in range(cfg["count"]):
        if stop and stop.is_set():
            batch["warnings"].append("规划已由用户暂停；仅保留已找到的组合。")
            break
        deadline = time.monotonic() + cfg["plan_seconds_per_video"]
        best = None
        good = 0
        for attempt in range(cfg["attempts_per_video"]):
            if time.monotonic() >= deadline or (stop and stop.is_set()):
                break
            track = choice_weighted(rng, tracks, [1 / (1 + music_usage[t["media"]["hash"]]) for t in tracks])
            end = choice_weighted(rng, pools["D"], [weight(c, "last") for c in pools["D"]])
            chosen_ids, chosen_hashes = {end["identity_key"]}, {end["hash"]}
            c_count = rng.randint(cfg["c_min"], min(cfg["c_max"], len(pools["C"])))
            tail = []
            for _ in range(c_count):
                candidates = [c for c in pools["C"] if c["identity_key"] not in chosen_ids and c["hash"] not in chosen_hashes]
                if not candidates:
                    break
                c = choice_weighted(rng, candidates, [weight(x) for x in candidates])
                tail.append(c)
                chosen_ids.add(c["identity_key"])
                chosen_hashes.add(c["hash"])
            if len(tail) != c_count:
                continue
            possible_a = [c for c in pools["A"] if c["identity_key"] not in chosen_ids and c["hash"] not in chosen_hashes]
            if not possible_a:
                continue
            first = choice_weighted(rng, possible_a, [weight(c, "first") for c in possible_a])
            chosen_ids.add(first["identity_key"])
            chosen_hashes.add(first["hash"])
            base = first["frames"] + end["frames"] + sum(c["frames"] for c in tail)
            if track["natural"]:
                low = high = track["target_frames"] - base
            else:
                high = min(maxf, math.floor((track["end_limit"] - track["start"]) * cfg["fps"] + 1e-7)) - base
                low = max(0, minf - base)
            if high < low or high < 0:
                continue
            candidates = []
            for c in pools["A"] + pools["B"]:
                if c["identity_key"] not in chosen_ids and c["hash"] not in chosen_hashes and c["frames"] <= high:
                    candidates.append(c)
            # Weighted random permutation: selects different subsets between attempts.
            candidates.sort(key=lambda c: rng.random() ** (1 / max(0.001, weight(c) * (cfg["a_weight"] if c["group"] == "A" else 1 - cfg["a_weight"]))), reverse=True)
            unique = []
            seen_i, seen_h = set(), set()
            for c in candidates:
                if c["identity_key"] not in seen_i and c["hash"] not in seen_h:
                    unique.append(c)
                    seen_i.add(c["identity_key"])
                    seen_h.add(c["hash"])
            candidates = unique[:60]
            slots = cfg["max_clips"] - len(tail) - 2
            if slots < 0:
                continue
            # Bounded subset sum in output frames. Never cuts a clip to close a gap.
            dp = {0: ()}
            for j, c in enumerate(candidates):
                if time.monotonic() >= deadline:
                    break
                for val, indices in list(dp.items()):
                    new = val + c["frames"]
                    if new <= high and len(indices) < slots:
                        prev = dp.get(new)
                        if prev is None or len(indices) + 1 < len(prev):
                            dp[new] = indices + (j,)
                if low == high and low in dp:
                    break
            sums = [s for s in dp if low <= s <= high]
            if not sums:
                continue
            target = min(sums, key=lambda s: abs(base + s - pref) + rng.uniform(0, cfg["fps"] * 1.5))
            middle = [candidates[j] for j in dp[target]]
            rng.shuffle(middle)
            # Prefer not to place linked near-source items adjacent when alternatives exist.
            for k in range(len(middle)):
                previous = first if k == 0 else middle[k - 1]
                if previous.get("source_id") and middle[k].get("source_id") == previous["source_id"]:
                    for j in range(k + 1, len(middle)):
                        if middle[j].get("source_id") != previous["source_id"]:
                            middle[k], middle[j] = middle[j], middle[k]
                            break
            seq = [first] + middle + tail + [end]
            try:
                validate_sequence(seq, cfg)
            except AssemblyError:
                continue
            seqhash, sethash = sequence_key(seq), set_key(seq)
            if seqhash in seen_sequences or (cfg["dedup_mode"] == "strict" and sethash in seen_sets):
                continue
            total = sum(c["frames"] for c in seq)
            if not minf <= total <= maxf:
                continue
            overlap = 0.0
            keys = {c["identity_key"] for c in seq}
            for prev in batch["items"]:
                other = {c["identity_key"] for c in prev["clips"]}
                overlap = max(overlap, len(keys & other) / min(len(keys), len(other)))
            if cfg["dedup_mode"] == "strict" and overlap > cfg["pair_overlap_limit"]:
                continue
            duration = total / cfg["fps"]
            dist = max(cfg["preferred_min"] - duration, 0, duration - cfg["preferred_max"])
            score = dist * 2 + overlap * 10 + sum(usage[x["identity_key"]] for x in seq) / len(seq)
            score += starts[first["identity_key"]] * 1.2 + ends[end["identity_key"]] * 1.1
            score += music_usage[track["media"]["hash"]] * 0.5
            if sethash in seen_sets:
                score += 5
            score += rng.random() * 0.6
            offset = 0
            output_clips = []
            clip_effects = build_clip_effects(seq, cfg, rng)
            for idx, c in enumerate(seq):
                output_clips.append(dict(c, timeline_start_frame=offset, duration_frames=c["frames"], source_in_seconds=0,
                                         source_out_seconds=c["duration"], playback_rate=rate, source_audio="discard",
                                         effects=clip_effects[idx]))
                offset += c["frames"]
            item = {"id": f"{batch_id}_{index+1:03d}", "number": index + 1, "status": "planned",
                    "sequence_hash": seqhash, "set_hash": sethash, "clips": output_clips,
                    "total_frames": total, "duration": duration,
                    "source_duration_sum": sum(c["duration"] for c in seq),
                    "frame_rounding_seconds": duration - sum(c["duration"] / rate for c in seq),
                    "playback_rate": rate, "video_processing": processing,
                    "file_export_id": (uuid.uuid4().hex if processing['hash_refresh'] else ''),
                    "bgm": dict(track["media"], start=track["start"], end=track["start"] + duration,
                                approved_end_limit=track["end_limit"], natural=track["natural"], range_id=track["range_id"], note=track["note"]),
                    "source_audio": "discard", "selection_score": round(score, 5), "max_batch_overlap": overlap}
            good += 1
            if best is None or score < best[0]:
                best = (score, item)
            if good >= 12:
                break
        if best is None:
            batch["warnings"].append(f"在有限搜索预算内只找到{len(batch['items'])}/{cfg['count']}条组合。可能受素材时长、音乐自然区间或去重条件限制；不是证明所有组合都不存在。可放宽时长档位/增加片段/增加音乐区间后重试。")
            break
        item = best[1]
        item["top_text"] = choose_top_text(index)
        if item["top_text"].get("enabled"):
            batch_text_hashes.add(item["top_text"]["hash"])
            text_usage[item["top_text"]["hash"]] += 1
        from visual_layout import freeze as freeze_visual_layout
        freeze_visual_layout(item,cfg)
        batch["items"].append(item)
        seen_sequences.add(item["sequence_hash"])
        seen_sets.add(item["set_hash"])
        usage.update(c["identity_key"] for c in item["clips"])
        starts[item["clips"][0]["identity_key"]] += 1
        ends[item["clips"][-1]["identity_key"]] += 1
        music_usage[item["bgm"]["hash"]] += 1
        log(f"方案{index+1:02d}：{item['duration']:.3f}秒 / {len(item['clips'])}段 / {item['bgm']['name']}")
    if not batch["items"]:
        raise AssemblyError(batch["warnings"][0] if batch["warnings"] else "没有找到可用组合；未裁剪任何视频。")
    return batch


def validate_item(item: dict, cfg: dict):
    from visual_layout import validate as validate_visual_layout
    from voice_speed import validate as validate_voice_speed
    try:validate_visual_layout(item,cfg);validate_voice_speed(item,cfg)
    except ValueError as exc:raise AssemblyError(str(exc)) from exc
    from product_catalog import validate_item as validate_product_spec
    from editorial import validate as validate_editorial
    validate_editorial(item,cfg)
    from edit_style import validate as validate_edit_style
    validate_edit_style(item)
    validate_product_spec(item,cfg)
    from clip_sfx import validate as validate_clip_sfx
    validate_clip_sfx(item,cfg)
    from external_voice import validate_frozen
    validate_frozen(item)
    from timeline_processing import validate_timing_item, compatible_settings
    from video_plan_policy import validate_text_layers
    validate_bgm_classification(item, cfg)
    validate_text_layers(item, cfg)
    if item.get('timeline_revision'):
        return validate_timing_item(item, cfg)
    validate_sequence(item["clips"], cfg)
    offset = 0
    rate = processing_settings(cfg)['speed']
    if item.get('video_processing') and not compatible_settings(item['video_processing'], processing_settings(cfg)):
        raise AssemblyError('导出处理设置与已保存方案不同，请重新规划。')
    for c in item["clips"]:
        expected = clip_frames(c["duration"] / rate, cfg["fps"])
        if c["duration_frames"] != expected or c["timeline_start_frame"] != offset:
            raise AssemblyError("时间线已被改动：片段必须从头到尾完整使用且无间隙。")
        if c.get("source_in_seconds") != 0 or c.get("source_out_seconds") != c["duration"] or c.get("playback_rate") != rate or c.get("source_audio") != "discard":
            raise AssemblyError("片段不能裁剪或加入原声；只有明确启用的导出倍速可以改变播放速度。")
        offset += expected
    if item["total_frames"] != offset or abs(item["duration"] - offset / cfg["fps"]) > 1e-8:
        raise AssemblyError("总时长与整段素材不一致。")
    bgm = item["bgm"]
    if not 0 <= bgm["start"] < bgm["end"] <= min(bgm["duration"], bgm["approved_end_limit"]) + 1e-6:
        raise AssemblyError("BGM不足以覆盖画面，或超出确认区间。")
    if abs(bgm["end"] - bgm["start"] - item["duration"]) > 1e-6:
        raise AssemblyError("音乐和画面长度不匹配。")
    if not math.ceil(cfg["min_seconds"]*cfg["fps"]) <= offset <= math.floor(cfg["max_seconds"]*cfg["fps"]):
        raise AssemblyError("时长超出用户范围。")
    if item.get("source_audio") != "discard":
        raise AssemblyError("原声状态非法。")


def save_batch(batch: dict, store: Store) -> Path:
    # Serialize allocation and publication; unfinished plans also own output numbers.
    from file_mutex import file_mutex
    with file_mutex(store.root/'output_numbering.lock',timeout=30):
        return _save_batch_numbered(batch,store)


def _save_batch_numbered(batch: dict, store: Store) -> Path:
    # User-facing output stays clean: output_root/YYYY-MM-DD contains videos only.
    # Manifests, QA receipts, render logs and temporary files live under the app data dir.
    from sk_selection import assert_reservable
    assert_reservable(batch,store)
    batch.setdefault('created_at',now())
    output_root = Path(batch["config"]["output_root"]).expanduser().resolve()
    date_folder = output_root / datetime.now().strftime("%Y-%m-%d")
    date_folder.mkdir(parents=True, exist_ok=True)

    from batch_history import directory_name, REVISION as HISTORY_FOLDER_REVISION
    batch['history_folder_name']=directory_name(batch)
    batch['history_folder_revision']=HISTORY_FOLDER_REVISION
    batch_dir = store.root / "batches" / batch['history_folder_name']
    path = batch_dir / "batch_manifest.json"
    if path.exists():
        raise AssemblyError(f"批次已存在，未覆盖：{path}")

    # Continue numbering within the same date folder across multiple runs.
    import re
    existing_numbers = []
    for f in date_folder.glob("整段组合_*.mp4"):
        m = re.fullmatch(r"整段组合_(\d+)\.mp4", f.name, flags=re.IGNORECASE)
        if m:
            existing_numbers.append(int(m.group(1)))
    reserved_items=store.usage()
    # Voice-failed frozen rows can be retried but are not render reservations yet.
    for manifest in (store.root/'batches').glob('*/batch_manifest.json'):
        reserved_items.extend(read_json(manifest).get('items',[]))
    for item in reserved_items:
        output=Path(item.get('output_path') or '.')
        if output.parent.resolve()!=date_folder.resolve():continue
        match=re.fullmatch(r"整段组合_(\d+)\.mp4",output.name,flags=re.IGNORECASE)
        if match:existing_numbers.append(int(match.group(1)))
    first_number = max(existing_numbers, default=0) + 1

    batch["batch_path"] = str(path)
    batch["output_date_folder"] = str(date_folder)
    for offset, item in enumerate(batch["items"]):
        daily_number = first_number + offset
        work_dir = batch_dir / f"{item['number']:02d}"
        item["daily_number"] = daily_number
        item["work_dir"] = str(work_dir)
        item["output_path"] = str(date_folder / f"整段组合_{daily_number:03d}.mp4")
        item["receipt_path"] = str(work_dir / "qa.json")
    from clip_sfx import freeze_batch as freeze_clip_sfx
    freeze_clip_sfx(batch,store.root,batch_dir/'input_snapshot'/'clip_sfx')
    from top_text_styles import freeze_batch as freeze_top_text
    freeze_top_text(batch,batch_dir/'input_snapshot'/'top_text')
    from edit_style import freeze_assets
    freeze_assets(batch,batch_dir/'input_snapshot'/'editorial')
    atomic_json(path, batch)
    if batch['config'].get('history_retention_revision'):
        from batch_retention import initialize
        initialize(batch,path,store)
        atomic_json(path,batch)
    store.reserve(batch)
    return path



def stream_process(args: list[str], logfile: Path, log, timeout: float = 1800,
                   stage: str = '子进程', progress_seconds: float = 0,
                   cancel: threading.Event | None = None, cwd: Path | None = None):
    """Reader thread avoids pipe deadlocks; actual timeout remains enforceable."""
    import runtime_diagnostics as diag
    if diag.hub():
        try:return diag.process(args,timeout=timeout,log=log,logfile=logfile,stage_name=stage,cancel=cancel,progress_seconds=progress_seconds,cwd=cwd)
        except (diag.ProcessFailure,OSError) as exc:raise AssemblyError(str(exc)) from exc
    import queue
    q = queue.Queue()
    logfile.parent.mkdir(parents=True, exist_ok=True)
    with logfile.open("a", encoding="utf-8") as f:
        f.write("\n" + now() + "\n" + json.dumps(args, ensure_ascii=False) + "\n")
        p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             encoding="utf-8", errors="replace", creationflags=creation_flags(), cwd=cwd)
        def reader():
            for line in p.stdout:
                q.put(line.rstrip())
            q.put(None)
        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        start = time.monotonic()
        last_update = start
        finished = False
        try:
            while not finished or p.poll() is None:
                if cancel and cancel.is_set():
                    raise AssemblyError(f'{stage}已取消；只停止本次启动的子进程：{logfile}')
                if time.monotonic() - start > timeout:
                    raise AssemblyError(f"{stage}运行超过{timeout/60:.0f}分钟。已停止本次子进程；日志：{logfile}")
                try:
                    line = q.get(timeout=0.25)
                except queue.Empty:
                    if time.monotonic()-last_update >= 5:
                        log(f'{stage}进行中：已耗时{time.monotonic()-start:.1f}秒；日志：{logfile}')
                        last_update=time.monotonic()
                    continue
                if line is None:
                    finished = True
                else:
                    f.write(line + "\n")
                    f.flush()
                    if line.startswith("ASSEMBLY:"):
                        log(line[9:].strip())
                    elif progress_seconds and line.startswith('out_time_us='):
                        try:
                            percent=min(100,float(line.split('=',1)[1])/1e6/progress_seconds*100)
                            if time.monotonic()-last_update >= 2 or percent >= 99:
                                log(f'{stage}：{percent:.0f}%；已耗时{time.monotonic()-start:.1f}秒；{logfile}')
                                last_update=time.monotonic()
                        except ValueError:
                            pass
            code = p.wait()
            if code:
                tail = logfile.read_text(encoding="utf-8")[-2400:]
                raise AssemblyError(f"子进程失败（{code}）。日志：{logfile}\n{tail}")
        finally:
            if p.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, creationflags=creation_flags())
                    try:
                        p.wait(5)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.wait(5)
                else:
                    p.terminate()
                    try:
                        p.wait(5)
                    except subprocess.TimeoutExpired:
                        p.kill()
            if p.stdout:
                p.stdout.close()


def remotion_environment(cfg: dict) -> tuple[str, Path]:
    node = resolve_tool("node", cfg["node"])
    configured = cfg.get("remotion_project", "").strip()
    options = [Path(configured)] if configured else [
        Path(_portable_path(r"D:\DY video\项目管理\混剪任务\鲜切牛肉_音乐卡点Pilot_01\remotion")),
        APP_DIR / "renderer"]
    for root in options:
        if not root.is_dir():
            continue
        try:
            check = run_capture([node, str(APP_DIR / "renderer" / "render.cjs"), "--check", str(root.resolve())], 30)
            if "READY" in check:
                return node, root.resolve()
        except AssemblyError:
            pass
    raise AssemblyError("没有找到同时安装 remotion、@remotion/bundler、@remotion/renderer 的Node工程。\n在「环境设置」选择已有Remotion工程，或运行 setup_renderer.py 安装独立依赖。不会改原工程代码。")


def check_source_unchanged(media: dict):
    p = Path(media["path"])
    if not p.is_file():
        raise AssemblyError(f"素材已移动或丢失：{p}。请重新扫描规划。")
    st = p.stat()
    # Verify the actual hash once per batch, not only a filename or cached stat.
    if st.st_size != media["size"] or sha256(p) != media["hash"]:
        raise AssemblyError(f"素材内容已变化：{p}。当前批次使用旧版本，不能继续；请重新扫描确认并规划。")


def prepare_bgm(item: dict, cfg: dict, ffmpeg: str, output: Path):
    duration, bgm = item["duration"], item["bgm"]
    if bgm["end"] > bgm["duration"] + 1e-6:
        raise AssemblyError("音乐长度不足，禁止循环或补静音。")
    filters = ["aresample=48000", f"atrim=start={bgm['start']:.9f}:duration={duration:.9f}",
               "asetpts=PTS-STARTPTS", f"volume={cfg['bgm_gain']:.6f}"]
    # Natural mode preserves the user's selected entrance/exit; no automatic fade.
    if not bgm["natural"]:
        fade_in = min(cfg["fade_in"], duration / 3)
        fade_out = min(cfg["fade_out"], duration / 3)
        if fade_in > 0:
            filters.append(f"afade=t=in:st=0:d={fade_in:.6f}")
        if fade_out > 0:
            filters.append(f"afade=t=out:st={duration-fade_out:.6f}:d={fade_out:.6f}")
    run_capture([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-i", bgm["path"], "-map", "0:a:0", "-vn", "-af", ",".join(filters),
                 "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(output)], 180)


def mux_bgm(silent_video: Path, bgm_wave: Path, output: Path, ffmpeg: str, export_id: str = '',
            *, audio_gain_db: float = 0):
    # Map only silent pictures and prepared mixed audio; never source video audio.
    # No -shortest: a short audio file must fail QA, never cut the last video clip.
    audio_gain_db = float(audio_gain_db)
    if not math.isfinite(audio_gain_db) or not -24 <= audio_gain_db <= 0:
        raise AssemblyError('最终AAC峰值补偿必须为-24～0dB。')
    run_capture([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                 "-i", str(silent_video), "-i", str(bgm_wave),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac",
                 "-b:a", "192k", "-ar", "48000", "-ac", "2", "-map_metadata", "-1",
                 *(["-af", f"volume={10 ** (audio_gain_db / 20):.12f}"] if audio_gain_db else []),
                 *(["-metadata", "comment=WholeClipAssembler export_id=" + export_id] if export_id else []),
                 "-movflags", "+faststart", str(output)], 300)


def finalize_aac_peak(silent_video: Path, mixed_wave: Path, output: Path, ffmpeg: str,
                      export_id: str = '', log=lambda msg: None, cancel=None):
    """Check encoded audio, remux from unchanged PCM with only necessary headroom.

    Never encode an earlier AAC generation, amplify a compliant output, alter the
    frozen mix settings, or relax the final -1 dBTP ceiling. Picture packets copy.
    """
    from voice_pipeline import measure_loudness, VoiceError
    gain = 0.0
    measurements = []
    for attempt in range(4):
        if cancel is not None and cancel.is_set():
            raise VoiceError('已取消最终AAC峰值检查。')
        loudness = measure_loudness(output, ffmpeg)
        peak = loudness['true_peak_dbtp']
        if math.isnan(peak) or peak == math.inf:
            raise VoiceError('最终AAC真峰值测量无效，禁止发布。')
        measurements.append(dict(audio_gain_db=round(gain, 6), **loudness))
        if peak <= -1.0:
            return loudness, dict(revision='aac_peak_headroom_v1', ceiling_dbtp=-1.0,
                                  target_dbtp=-1.5, attenuation_db=round(gain, 6),
                                  remux_count=attempt, measurements=measurements)
        if attempt == 3:
            raise VoiceError(f'最终AAC真峰值{peak:.2f}dBTP超过-1dBTP，三次补偿后仍不合格。')
        gain -= max(.5, peak + 1.5)
        if gain < -24:
            raise VoiceError('最终AAC需要超过24dB的补偿，音频异常，未继续压低或发布。')
        log(f'AAC真峰值{peak:.2f}dBTP超标；仅在封装时整体补偿{gain:.2f}dB，重新编码音频并复测。')
        mux_bgm(silent_video, mixed_wave, output, ffmpeg, export_id, audio_gain_db=gain)
    raise AssertionError('unreachable')


def technical_qa(path: Path, item: dict, cfg: dict, ffprobe: str, ffmpeg: str, require_audio=True,
                 log=None, logfile: Path | None = None, cancel: threading.Event | None = None) -> dict:
    from duration_tail_policy import validate_item as validate_duration
    validate_duration(item,cfg)
    if log: log(f'媒体检查开始：{path}')
    meta = probe(path, ffprobe)
    if log: log(f'媒体检查完成：{path}；开始完整解码。')
    if meta["width"] != cfg["width"] or meta["height"] != cfg["height"] or meta["video_codec"] != "h264":
        raise AssemblyError("成片画幅或视频编码不符合计划。")
    expected = item["duration"]
    if abs(meta["duration"] - expected) > 1.1 / cfg["fps"]:
        raise AssemblyError(f"视频时长不符合整段计划：{meta['duration']:.6f} vs {expected:.6f}")
    if meta["nb_frames"] not in (None, "N/A") and int(meta["nb_frames"]) != item["total_frames"]:
        raise AssemblyError("输出帧数与计划不一致。")
    if require_audio:
        if meta["audio_stream_count"] != 1 or meta["audio_codec"] != "aac":
            raise AssemblyError("成片必须只有一条AAC音轨（BGM）。")
        if abs(meta["audio_duration"] - expected) > max(0.08, 2 / cfg["fps"]):
            raise AssemblyError("成片音乐时长不足/超长，未使用短音频截断视频。")
    elif meta["has_audio"]:
        raise AssemblyError("Remotion画面中间文件不应有任何音轨；拒绝继续混音。")
    decode_args=[ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-progress", "pipe:1",
                 "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-f", "null", "-"]
    if log and logfile:
        stream_process(decode_args, logfile, log, 300, stage=f'完整解码 {path}',
                       progress_seconds=expected, cancel=cancel)
        log(f'完整解码完成：{path}')
    else:
        run_capture(decode_args, 300)
    report = {"technical_pass": True, "metadata": meta, "expected_frames": item["total_frames"],
            "original_audio": "absent_by_silent_render_and_explicit_stream_mapping",
            "content_qa": "not_performed; export timing/effects do not certify action completeness", "checked_at": now()}
    if item.get('sk_snapshot'):
        from copy import deepcopy
        report['sk_selection']=deepcopy(item['sk_snapshot'])
        report['sk_visual_sequence']=item['sk_visual_sequence']
    return report


def render_batch(batch_path: Path, store: Store, log=lambda msg: None,
                 on_item=lambda item: None, pause: threading.Event | None = None,
                 cancel: threading.Event | None = None):
    from batch_retention import run_with_retention
    return run_with_retention(batch_path,store,lambda:_render_batch(batch_path,store,log,on_item,pause,cancel))


def _render_batch(batch_path: Path, store: Store, log=lambda msg: None,
                  on_item=lambda item: None, pause: threading.Event | None = None,
                  cancel: threading.Event | None = None):
    import runtime_diagnostics as diag
    from voice_pipeline import VoiceError, mix_voice_bgm
    from temp_lifecycle import cleanup_item_media
    batch_path = Path(batch_path)
    batch = read_json(batch_path)
    cfg = batch["config"]
    from review_session import blocked, observe
    if blocked(batch):
        if pause is not None: pause.set()
        log('首条已完成，等待审片；请接受当前参数或按新参数继续。')
        return batch
    callback = on_item
    def on_item(item):
        if observe(batch, item, pause): atomic_json(batch_path, batch)
        callback(item)
    validate_config(cfg)
    node, dep_root = remotion_environment(cfg)
    ffmpeg = resolve_tool("ffmpeg", cfg["ffmpeg"])
    ffprobe = resolve_tool("ffprobe", cfg["ffprobe"])
    checked = set()
    from voice_references import paths as referenced_voice_paths
    started = time.monotonic()
    for item in batch["items"]:
        if blocked(batch): break
        if item.get('status') == 'VOICE_FAILED':
            log(f"{item['number']:02d}配音仍失败；请点“重试失败配音”后继续生成。")
            continue
        if pause and pause.is_set():
            log("已在片段成片边界暂停；下次打开此批次可继续。")
            break
        if (cfg.get('bgm_rule_version') == BGM_DIRECTORY_RULE and
                shutil.disk_usage(Path(cfg['output_root'])).free < 2 * 1024**3):
            batch['pause_reason'] = '磁盘剩余空间不足2 GiB，请先清理程序临时文件。'
            log(batch['pause_reason'])
            atomic_json(batch_path, batch)
            break
        folder = Path(item["work_dir"]) if item.get("work_dir") else Path(item["receipt_path"]).parent
        task=diag.Task(batch_id=batch["id"],job_id=item["id"],number=item["number"])
        task.__enter__();task.phase("preparation")
        job_root = (Path(cfg['voice_job_temp_dir']) if item.get('voice_temp_version') and
                    cfg.get('voice_job_temp_dir') else None)
        render_temp = job_root/'render'/item['id'] if job_root else folder
        output = Path(item["output_path"])
        receipt_path = Path(item["receipt_path"])
        try:
            folder.mkdir(parents=True,exist_ok=True)
            output.parent.mkdir(parents=True,exist_ok=True)
            if item.get('voice_temp_version') and (job_root is None or
                    not (job_root/'.assembler_job_temp').is_file()):
                raise AssemblyError('任务临时目录标记缺失，已拒绝写入未知路径。')
            validate_item(item, cfg)
            from top_text_styles import validate as validate_top_text
            validate_top_text(item,cfg)
            plan_fields = {"clips": item["clips"], "bgm": item["bgm"], "top_text": item.get("top_text", {}),
                           "render_settings": {k: cfg[k] for k in ("width", "height", "fps", "crf", "bgm_gain", "fade_in", "fade_out", "source_audio", "top_text_font_size", "top_text_top", "top_text_max_width", "top_text_stroke")}}
            if item.get('script_package'):
                plan_fields.update(script_package=item['script_package'], voice=item.get('voice'),
                                   subtitles=item.get('subtitles', []), subtitle_profile=item.get('subtitle_profile'),
                                   audio_mix_profile=item.get('audio_mix_profile'),
                                   audio_mix_parameters=item.get('audio_mix_parameters'))
                if 'voice_gain_db' in item:
                    plan_fields['voice_gain_db'] = item['voice_gain_db']
            if item.get('duration_policy_version'):
                from video_plan_policy import REVISION as VIDEO_POLICY_REVISION, PREVIOUS_REVISION
                policy_keys = (
                    'video_mode', 'planned_video_duration', 'final_video_duration',
                    'top_text_timing', 'intro_top_text_duration', 'post_voice_mode',
                    'post_voice_top_text_enabled', 'narration_start', 'narration_end',
                    'post_voice_start', 'post_voice_end')
                if item['duration_policy_version'] == PREVIOUS_REVISION:
                    policy_keys = ('video_mode', 'planned_video_duration', 'final_video_duration',
                                   'top_text_enabled', 'top_text_timing', 'intro_top_text_duration',
                                   'narration_start', 'narration_end', 'post_voice_start', 'post_voice_end')
                elif item['duration_policy_version'] == VIDEO_POLICY_REVISION:
                    policy_keys = ('video_mode', 'planned_video_duration', 'final_video_duration',
                                   'top_text_enabled', 'top_text_policy', 'resolved_top_text_timing',
                                   'top_text_start', 'top_text_end', 'narration_start', 'narration_end',
                                   'bottom_subtitle_start', 'bottom_subtitle_end')
                plan_fields['video_policy'] = {key:item.get(key) for key in policy_keys}
            # Keep V1.0.2 receipt hashes readable; do not re-render completed legacy batches merely for new default fields.
            if 'video_processing' in item or processing_settings(cfg)['enabled']:
                plan_fields.update(video_processing=item.get('video_processing', processing_settings(cfg)), file_export_id=item.get('file_export_id', ''))
            if item.get('timeline_revision'):
                plan_fields.update(timeline_revision=item['timeline_revision'], transitions=item.get('transitions', []),
                                   timeline_digest=item.get('timeline_digest'))
            for new_key in ('editing_profile_id','grouped_revision','coverage_snapshot',
                            'package_anchor','voice_match_snapshot','business_digest',
                            'visual_layout_snapshot','voice_speed_snapshot','top_text_visual_snapshot','editorial_snapshot','editorial_digest','edit_style_snapshot'):
                if new_key in item:plan_fields[new_key]=item[new_key]
            if item.get('clip_sfx_snapshot'):plan_fields['clip_sfx_digest']=item['clip_sfx_snapshot']['digest']
            plan_digest = object_hash(plan_fields)
            receipt = read_json(receipt_path, {})
            if output.is_file() and receipt.get("plan_digest") == plan_digest and receipt.get("output_hash") == sha256(output):
                item["status"] = "completed"
                item.pop("error", None)
                atomic_json(folder / "timeline_manifest.json", item)
                store.record_success(item)
                if job_root:
                    cleanup_item_media(item, job_root, log_file=store.root/'cleanup.jsonl',protected_paths=batch.get('protected_voice_paths',[])+referenced_voice_paths(batch['items'],item['id']))
                on_item(item)
                log(f"{item['number']:02d} 已完成且指纹一致，跳过重复渲染。")
                task.finish("skipped",cache_hit=True)
                continue
            task.phase("validation")
            for media in item["clips"] + [item["bgm"]]:
                key = (media["path"], media["hash"])
                if key not in checked:
                    check_source_unchanged(media)
                    checked.add(key)
            if item.get('narration_enabled') and not Path((item.get('voice') or {}).get('voice_processed', '')).is_file():
                raise VoiceError('已冻结的配音临时文件缺失；请点“重试失败配音”，不降级输出。')
            item["status"] = "rendering"
            item.pop("error", None)
            atomic_json(batch_path, batch)
            on_item(item)
            log(f"正在生成 {item['number']:02d}/{len(batch['items'])}：{item['duration']:.3f}秒，原声强制去除。")
            render_temp.mkdir(parents=True, exist_ok=True)
            video = render_temp / "_silent_video.part.mp4"
            wave = render_temp / "_bgm.part.wav"
            final_temp = render_temp / "_final.part.mp4"
            paths, media_keys = {}, []
            processing = processing_settings(cfg)
            task.phase("preprocess")
            cache_started = time.monotonic()
            cache_hits = 0
            for c in item["clips"]:
                if cancel and cancel.is_set(): raise AssemblyError('用户已取消本条生成。')
                media_path, hit = prepare_sampled_media(c, cfg, store.root / 'processing_cache', ffmpeg, ffprobe, log, c.get('effects'), cancel)
                cache_hits += bool(hit)
                key = object_hash(str(media_path)) + media_path.suffix.lower()
                paths[key] = media_path
                media_keys.append(key)
            cache_seconds = time.monotonic() - cache_started
            log(f'缓存处理完成：{len(item["clips"])}段，直接读取或命中{cache_hits}段，耗时{cache_seconds:.1f}秒。')
            task.phase("subtitles")
            from copy import deepcopy
            subtitle_profile = deepcopy(item.get('subtitle_profile', {}))
            if item.get('subtitle_diagnostics'):
                from smart_subtitles import validate as validate_smart_subtitles
                validate_smart_subtitles(item['subtitles'],item['script_package']['subtitle_text'],
                                        subtitle_profile['style'],cfg['fps'],cfg['width'],cfg['height'])
                atomic_json(folder/'subtitle_diagnostics.json',dict(item['subtitle_diagnostics'],cues=item['subtitles']))
                diag.emit('subtitle_layout_metrics','智能字幕断句/对齐/排版与动画检查通过',**item['subtitle_diagnostics'])
                diag.emit('subtitle_cues','冻结字幕逐句详情','DEBUG',cues=item['subtitles'])
                log(f"字幕检查通过：{len(item['subtitles'])}句；字体 {subtitle_profile['style']['font_family']}；对齐缓存 {item['subtitle_diagnostics'].get('alignment_cache_hit')}。")
            font_key = None
            if subtitle_profile.get('style'):
                from subtitle_fonts import media
                font_key = media(subtitle_profile['style'], paths)
            from top_text_styles import media as top_text_media, props as top_text_props
            top_key=top_text_media(item,paths)
            source_map = {media_keys[j]: {'original_path': c['path']} for j,c in enumerate(item['clips'])}
            with local_media_server(paths, diagnostics=folder/'media_server.jsonl',
                                    job_id=item['id'], source_map=source_map) as base:
                if font_key: subtitle_profile['style']['fontSrc'] = base + font_key
                props = {"width": cfg["width"], "height": cfg["height"], "fps": cfg["fps"],
                         "durationInFrames": item["total_frames"], "sourceAudio": "discard",
                         "timelineRevision": item.get("timeline_revision", ""),
                         "transitions": item.get("transitions", []),
                         "topText": item.get("top_text", {}), "videoProcessing": processing,
                         "topTextVisual": top_text_props(item,base+top_key) if top_key else None,
                         "visualLayout": item.get('visual_layout_snapshot'),
                         "editStyle": item.get('edit_style_snapshot'),
                         "subtitles": item.get('subtitles', []),
                         "subtitleProfile": subtitle_profile,
                         "topTextStyle": {"fontSize": cfg.get("top_text_font_size", 60), "top": cfg.get("top_text_top", 90),
                                          "maxWidth": cfg.get("top_text_max_width", 0.78), "stroke": cfg.get("top_text_stroke", 3)},
                         "clips": [{"src": base + media_keys[j],
                                    "from": c["timeline_start_frame"], "frames": c["duration_frames"],
                                    "playbackRate": c.get("playback_rate", 1),
                                    "sourceStartFrame": (0 if paths[media_keys[j]] != Path(c['path']).resolve() else c.get('source_in_seconds',0) * cfg['fps']),
                                    "sourceEndSeconds": (0 if paths[media_keys[j]] != Path(c['path']).resolve() else c.get('source_out_seconds',c['duration'])),
                                    "effects": c.get("effects", {}),
                                    "incomingTransition": c.get("incoming_transition", {"type":"none","frames":0}),
                                    "outgoingTransition": c.get("outgoing_transition", {"type":"none","frames":0}),
                                    "label": c["display"]} for j, c in enumerate(item["clips"])]}
                request = {"props": props, "dependencyRoot": str(dep_root), "output": str(video),
                           "crf": cfg["crf"], "concurrency": cfg["concurrency"], "browser": cfg["browser"]}
                request['diagnostics'] = {'jobId': item['id'], 'attemptId': task.fields['attempt_id'],
                    'media': [{'url': base+media_keys[j], 'originalPath': c['path'],
                               'renderPath': str(paths[media_keys[j]]), 'label': c['display'],
                               'from': c['timeline_start_frame'], 'frames': c['duration_frames'],
                               'sourceWindow': [c.get('source_in_seconds'), c.get('source_out_seconds')]}
                              for j,c in enumerate(item['clips'])]}
                request_file = folder / "render_request.json"
                atomic_json(request_file, request)
                task.phase("remotion")
                render_started = time.monotonic()
                log(f'Remotion渲染开始：{video}；共{item["total_frames"]}帧。')
                try:
                    stream_process([node, str(APP_DIR / "renderer" / "render.cjs"), str(request_file)], folder / "render.log", log,
                                   stage=f'Remotion渲染 {video}', timeout=cfg.get('render_timeout_seconds',1800), cancel=cancel, cwd=APP_DIR)
                except Exception as render_error:
                    try:
                        from render_failure import save_render_failure
                        failure = save_render_failure(folder, request, render_error)
                        diag.emit('render_frame_failure', '取帧/渲染失败诊断已保存', 'ERROR',
                                  report_path=str(folder/'render_failure.json'),
                                  failed_frame=failure.get('failed_frame'),failure_stage=failure.get('failure_stage'),
                                  remotion_concurrency=failure.get('remotion_concurrency'),
                                  requests=failure.get('requests',[])[:8])
                        log(f"渲染失败详情：{folder/'render_failure.json'}")
                    except Exception as diagnostic_error:
                        log(f'渲染诊断写入失败，原异常保留：{diagnostic_error}')
                    raise
                render_seconds = time.monotonic() - render_started
                diag.emit("remotion_metrics","Remotion实际子阶段耗时（与总耗时不相加）",metrics=read_json(folder/"render_performance.json",{}))
                log(f'Remotion渲染完成：{video}；耗时{render_seconds:.1f}秒。')
            task.phase("validation")
            qa_started = time.monotonic()
            log(f'媒体检查与完整解码开始：{video}')
            technical_qa(video, item, cfg, ffprobe, ffmpeg, require_audio=False,
                         log=log, logfile=folder/'qa-silent.log', cancel=cancel)
            task.phase("ffmpeg")
            if item.get('narration_enabled'):
                voice = item.get('voice') or {}
                if not Path(voice.get('voice_processed', '')).is_file():
                    raise VoiceError('已冻结的配音临时文件缺失；请点“重试失败配音”，不降级输出。')
                mix_info = mix_voice_bgm(item, cfg, voice, item.get('audio_mix_profile', '配音优先'),
                                         ffmpeg, wave, item.get('voice_gain_db', item.get('voice_trim_db', 0)),
                                         item.get('bgm_trim_db', 0),defer_peak_validation=True)
                if mix_info.get('pcm_peak_requires_final_check'):
                    log(f"基础混音真峰值{mix_info['true_peak_dbtp']:.2f}dBTP；在音效合并后的最终AAC阶段统一复测并按需整体补偿。")
            else:
                mix_info = None
                prepare_bgm(item, cfg, ffmpeg, wave)
            from clip_sfx import overlay as overlay_clip_sfx
            sfx_info=overlay_clip_sfx(item,cfg,wave,ffmpeg,cancel=cancel)
            from edit_style import overlay as overlay_edit_style
            accents=overlay_edit_style(item,cfg,wave,ffmpeg,cancel=cancel)
            if accents:sfx_info=dict(sfx_info or {},editorial=accents,event_count=(sfx_info or {}).get('event_count',0)+accents['event_count'])
            if sfx_info:
                mix_info=dict(mix_info or {},clip_sfx=sfx_info)
                log(f"片段音效已加入：{sfx_info['event_count']}处；视频长度保持{item['duration']:.3f}秒。")
            wave_meta = probe(wave, ffprobe)
            if abs(wave_meta["duration"] - item["duration"]) > 0.004:
                raise AssemblyError("解码后的BGM长度不足，未自动补静音或截断画面。请重新选择音乐区间。")
            mux_bgm(video, wave, final_temp, ffmpeg, item.get("file_export_id", ""))
            task.phase("validation")
            final_loudness = None
            if item.get('narration_enabled') or sfx_info:
                final_loudness, peak_control = finalize_aac_peak(
                    video, wave, final_temp, ffmpeg, item.get('file_export_id', ''), log, cancel)
                mix_info=dict(mix_info or {},aac_peak_control=peak_control)
            qa = technical_qa(final_temp, item, cfg, ffprobe, ffmpeg,
                              log=log, logfile=folder/'qa-final.log', cancel=cancel)
            if item.get('narration_enabled') or sfx_info:
                if qa['metadata']['audio_sample_rate'] != 48000:
                    raise VoiceError('最终视频配音音轨不是48000Hz。')
                if final_loudness['true_peak_dbtp'] > -1.0:
                    raise VoiceError(f'最终AAC真峰值{final_loudness["true_peak_dbtp"]:.2f}dBTP超过-1dBTP。')
                mix_info['final_audio'] = final_loudness
            qa_seconds = time.monotonic() - qa_started
            diag.emit("artifact_validated","成片完整性检查通过",path=str(final_temp),valid=True)
            log(f'媒体检查与完整解码完成：{final_temp}；耗时{qa_seconds:.1f}秒。')
            task.phase("output")
            if output.exists():
                backup = output.with_name(output.stem + "_unverified_" + uuid.uuid4().hex[:6] + output.suffix)
                output.rename(backup)
                log(f"保留已有未验证输出：{backup.name}")
            os.replace(final_temp, output)
            qa.update(plan_digest=plan_digest, output_hash=sha256(output), output_path=str(output),
                      only_bgm=not item.get('narration_enabled', False) and not sfx_info,
                      all_source_audio_discarded=True, audio_mix=mix_info,
                      output_md5=file_md5(output), video_processing=processing_settings(cfg),
                      file_export_id=item.get('file_export_id', ''), platform_check='not_performed',
                      stage_seconds={'cache':round(cache_seconds,3),'render':round(render_seconds,3),'qa_and_bgm':round(qa_seconds,3)})
            atomic_json(receipt_path, qa)
            item["status"] = "completed"
            item["finished_at"] = now()
            atomic_json(folder / "timeline_manifest.json", item)
            store.record_success(item)
            for temp in (video, wave):
                temp.unlink(missing_ok=True)
            if job_root:
                cleanup_item_media(item, job_root, log_file=store.root/'cleanup.jsonl',protected_paths=batch.get('protected_voice_paths',[])+referenced_voice_paths(batch['items'],item['id']))
            prior=(item.get("voice") or {}).get("diagnostic",{})
            prior_stages=dict(prior.get('stages',{}))
            prior_stages.update(item.get('planning_diagnostic',{}).get('stages',{}))
            span=(time.monotonic()-prior['started_monotonic'] if diag.hub() and prior.get('run_id')==diag.hub().run_id else None)
            task.finish("success",output=str(output),output_exists=output.is_file(),voice_prior_performance=(item.get("voice") or {}).get("performance",{}),prior_stages=prior_stages,end_to_end_seconds=span)
            if prior_stages:log('规划时阶段（不计入本次渲染用时）：'+str(prior_stages))
            log(task.summary())
            item["diagnostics"]={**task.fields,"stages":task.times}
            log(f"{item['number']:02d} 已完成：{output}")
        except Exception as exc:
            cancelled=bool(cancel and cancel.is_set())
            task.finish('cancelled' if cancelled else 'failed',exc,
                        cleanup='保留本条已生成的配音和中间文件；未清理正式配音缓存',
                        resume='沿用冻结Manifest重试；有效配音复用，完成成片按原收据跳过')
            # Keep valid frozen voice on rendering failures: retry rendering, not TTS.
            audio_missing=item.get('narration_enabled') and not Path((item.get('voice') or {}).get('voice_processed','')).is_file()
            item['status']='planned' if cancelled else ('VOICE_FAILED' if audio_missing else 'failed')
            item['error']=str(exc)
            item['diagnostics']={**task.fields,'stages':task.times}
            log(f"{item['number']:02d} {'已取消' if cancelled else '失败，其他条继续'}：{exc}")
        finally:
            task.__exit__(None,None,None)
            atomic_json(batch_path, batch)
            on_item(item)
        if cancel and cancel.is_set():
            log('本条取消后已停止当前批次；原方案保留，可再次继续。')
            break
    batch["last_run_seconds"] = round(time.monotonic() - started, 2)
    batch["last_run_at"] = now()
    atomic_json(batch_path, batch)
    write_summary(batch_path, batch)
    diag.emit("batch_summary","批次执行结束",batch_id=batch["id"],elapsed=batch["last_run_seconds"],counts={status:sum(i.get("status")==status for i in batch["items"]) for status in ("completed","failed","VOICE_FAILED","planned")})
    return batch


def write_summary(batch_path: Path, batch: dict):
    items = batch["items"]
    successful = [i for i in items if i["status"] == "completed"]
    ids = [c["identity_key"] for i in successful for c in i["clips"]]
    repeated = 1 - len(set(ids)) / len(ids) if ids else 0
    lines = ["# 整段组合批次报告", "", f"批次：{batch['id']}",
             f"请求 {batch['requested_count']} 条；已规划 {len(items)} 条；完成 {len(successful)} 条。",
             "", "原视频音轨：全部排除。成片音轨：仅指定BGM。", ("原素材文件保持不变；启用修边或倍速时成片仅按保存的处理窗口播放。导出处理设置：" + str(processing_settings(batch['config']))),
             "关闭导出处理时整段原速输出。修边、倍速、特效按已冻结的片段方案执行；转场重叠只扣除一次。输出帧率统一，BGM自身不变速。",
             "MD5变化、调色或镜像不代表平台认为内容原创。本程序没有平台查重或过审测试。",
             "不同帧率统一输出时存在帧网格量化，每段增加少于一个输出帧的时间格；详见各条frame_rounding_seconds。",
             "技术校验不是审美/权利批准；本工具不修改原项目素材索引或正式资格。", "",
             f"已完成成片按素材身份计算的重复使用比例：{repeated:.1%}。", "",
             "| 编号 | 状态 | 秒数 | 片段数 | BGM | 顶部文案 | 输出 |", "|---|---|---:|---:|---|---|---|"]
    for i in items:
        top_preview = i.get("top_text", {}).get("text", "").replace("|", " ").replace("\n", " / ")[:40]
        lines.append(f"|{i['number']}|{i['status']}|{i['duration']:.3f}|{len(i['clips'])}|{i['bgm']['name'].replace('|',' ')}|{top_preview}|{i['output_path']}|")
        if i.get("error"):
            lines += ["", f"第{i['number']}条错误：{i['error']}", ""]
    if batch.get('sk_summary'):
        from sk_ui import summary_text
        lines += ['', '## SK类别时长汇总', summary_text(batch)]
    lines += ["", "## 提示", *batch.get("warnings", [])]
    (batch_path.parent / "批次报告.md").write_text("\n".join(lines), encoding="utf-8")


@contextmanager
def instance_lock(data_dir: Path):
    path = Path(data_dir) / "application.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+b")
    try:
        f.seek(0)
        f.write(b"0")
        f.flush()
        f.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AssemblyError("同一数据目录的组合器已经运行。请关闭旧窗口后再启动，避免重复组合/并发写入。") from exc
        yield
    finally:
        f.close()
