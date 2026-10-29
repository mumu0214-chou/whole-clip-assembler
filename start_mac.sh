#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export ASSEMBLER_HOME="${ASSEMBLER_HOME:-$HOME/Movies/WholeClipAssembler}"
export ASSEMBLER_RUNTIME="${ASSEMBLER_RUNTIME:-$ASSEMBLER_HOME/runtime}"
export ASSEMBLER_TEMP="${ASSEMBLER_TEMP:-$ASSEMBLER_HOME/temp}"
export PYTORCH_ENABLE_MPS_FALLBACK=1
mkdir -p "$ASSEMBLER_TEMP"
PY="$ASSEMBLER_RUNTIME/app/bin/python"
if [[ ! -x "$PY" ]]; then echo '请先运行 setup_mac.sh'; exit 1; fi
cd "$ROOT"
"$PY" "$ROOT/create_workspace_layout.py"
"$PY" "$ROOT/initialize_starter_content.py"
exec "$PY" app.py "$@"
