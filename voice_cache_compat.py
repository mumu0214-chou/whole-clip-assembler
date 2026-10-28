"""Keep voice identity across audited subtitle and downstream mix changes.

The baseline and audited downstream mix patches share identity.
Any other AST change invalidates it, including inference or voice postprocessing.
"""
import ast
from hashlib import sha256
from pathlib import Path

ORIGINAL_SHA='b4024748235b0a83225e2d819b130816410f4787008d5b920fad6ced10b35ba0'
INFERENCE_AST_SHA='674887f6e5a43ef30633b9537b269f162e79f36413569fc1d5baeeba3a1571b2'
STRICT_MIX_AST_SHA='ea049c3e5f27301bd9258458ba0abbd8cf45102d6eff2199e7f3c4b58f5b27a0'
# Audited additive external-source dispatch. Local inference/postprocessing AST
# is unchanged; any further edit still invalidates cache identities.
EXTERNAL_ROUTING_AST_SHA='22c1078d7e38ad8abdab4546ffec195fd9da169e9122cadc9fd8685bd9731aac'
# Audited final-AAC peak handoff changes only mix_voice_bgm. Inference and
# processed-voice generation are byte-for-byte AST compatible with the baseline.
PRE_AAC_MIX_AST_SHA='441428f1ff26daec371ea527f283ddf668971d7cc4b6f8f7111c28811ee2480e'
# Per-speaker tempo is downstream of the unchanged inference/post cache. Only
# prepare_script_packages adds its frozen decision; speed has a separate PCM key.
VOICE_RATE_ROUTING_AST_SHA='0b829aab2b507acc0229115f4a767a2e6b6b97108526cb126eede305dc98bb21'


def pipeline_revision(path):
    path=Path(path);tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    excluded={'freeze_script_on_item','WeightedSentenceTiming','SubtitleTimingProvider'}
    tree.body=[node for node in tree.body if getattr(node,'name',None) not in excluded]
    fingerprint=sha256(ast.dump(tree,include_attributes=False).encode()).hexdigest()
    return ORIGINAL_SHA if fingerprint in (INFERENCE_AST_SHA,STRICT_MIX_AST_SHA,EXTERNAL_ROUTING_AST_SHA,PRE_AAC_MIX_AST_SHA,VOICE_RATE_ROUTING_AST_SHA) else sha256(path.read_bytes()).hexdigest()
