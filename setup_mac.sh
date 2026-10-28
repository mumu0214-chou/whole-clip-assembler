#!/bin/bash
# Apple Silicon bootstrap. Installs to an isolated workspace; never deletes user data.
set -euo pipefail
if [[ "$(uname -s)" != Darwin || "$(uname -m)" != arm64 ]]; then
  echo '此安装器需要 Apple Silicon macOS。' >&2; exit 1
fi
ROOT="$(cd "$(dirname "$0")" && pwd)"
export ASSEMBLER_HOME="${ASSEMBLER_HOME:-$HOME/Movies/WholeClipAssembler}"
export ASSEMBLER_RUNTIME="${ASSEMBLER_RUNTIME:-$ASSEMBLER_HOME/runtime}"
export ASSEMBLER_TEMP="${ASSEMBLER_TEMP:-$ASSEMBLER_HOME/temp}"
export UV_CACHE_DIR="$ASSEMBLER_RUNTIME/cache/uv" PIP_CACHE_DIR="$ASSEMBLER_RUNTIME/cache/pip"
export npm_config_cache="$ASSEMBLER_RUNTIME/cache/npm"
export HF_HOME="$ASSEMBLER_RUNTIME/cache/huggingface" TMPDIR="$ASSEMBLER_TEMP"
export PYTORCH_ENABLE_MPS_FALLBACK=1
mkdir -p "$ASSEMBLER_HOME" "$ASSEMBLER_RUNTIME" "$ASSEMBLER_TEMP" "$ASSEMBLER_RUNTIME/models"
if ! command -v brew >/dev/null 2>&1; then
  echo '缺少 Homebrew。请先按照 https://brew.sh/ 官方说明安装，再重新运行。' >&2; exit 1
fi
brew install python@3.11 python-tk@3.11 ffmpeg node uv
PYTHON="$(brew --prefix python@3.11)/bin/python3.11"
"$PYTHON" -c 'import tkinter' || { echo 'Python Tk 不可用，请检查 python-tk@3.11。' >&2; exit 1; }
if [[ ! -x "$ASSEMBLER_RUNTIME/app/bin/python" ]]; then "$PYTHON" -m venv "$ASSEMBLER_RUNTIME/app"; fi
"$ASSEMBLER_RUNTIME/app/bin/python" -m pip install -r "$ROOT/requirements-ui.txt"
(cd "$ROOT/renderer" && npm ci --no-audit --no-fund)

INDEX_COMMIT=d9e41aac89fd00b3d71497fddb287b7f24613712
if [[ ! -e "$ASSEMBLER_RUNTIME/index-tts" ]]; then
  git clone https://github.com/index-tts/index-tts.git "$ASSEMBLER_RUNTIME/index-tts"
  git -C "$ASSEMBLER_RUNTIME/index-tts" checkout --detach "$INDEX_COMMIT"
elif [[ ! -d "$ASSEMBLER_RUNTIME/index-tts/.git" ]] || [[ "$(git -C "$ASSEMBLER_RUNTIME/index-tts" rev-parse HEAD)" != "$INDEX_COMMIT" ]]; then
  echo '已有 IndexTTS 目录版本不匹配，未覆盖。请检查 ASSEMBLER_RUNTIME。' >&2; exit 1
fi
(cd "$ASSEMBLER_RUNTIME/index-tts" && uv sync --frozen --no-dev)
if [[ ! -x "$ASSEMBLER_RUNTIME/asr/bin/python" ]]; then "$PYTHON" -m venv "$ASSEMBLER_RUNTIME/asr"; fi
"$ASSEMBLER_RUNTIME/asr/bin/python" -m pip install 'faster-whisper>=1.1,<2' 'sherpa-onnx>=1.10,<2' 'huggingface-hub>=0.27,<1'
"$ASSEMBLER_RUNTIME/asr/bin/python" - "$ASSEMBLER_RUNTIME" <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download
import sys
root=Path(sys.argv[1])
snapshot_download('IndexTeam/IndexTTS-2.5', local_dir=root/'index-tts/checkpoints')
snapshot_download('Systran/faster-whisper-large-v3-turbo', local_dir=root/'models/faster-whisper-large-v3-turbo')
PY
(cd "$ASSEMBLER_RUNTIME/index-tts" && "$ASSEMBLER_RUNTIME/index-tts/.venv/bin/python" -c 'from indextts.utils.model_download import ensure_models_available; ensure_models_available("checkpoints")')
"$ASSEMBLER_RUNTIME/asr/bin/python" "$ROOT/setup_sensevoice.py"
echo '依赖和模型安装步骤结束。请按 MAC首次使用.md 进行 Mac 实际配音和渲染验证。'
