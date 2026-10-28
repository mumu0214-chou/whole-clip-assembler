"""Keep the final complete packaging action through a short voice release tail."""
import hashlib
import math

REVISION = 'package_final_clause_release_v1'
MAX_RELEASE_SECONDS = .5


def ending_policy(package, anchor, cfg, target):
    """Only a proven last quantity clause in a new strict voice-fit plan qualifies.

    The normal entry/exit margin remains in force for every other clause. This
    closes the frame-quantized audio release tail without introducing a tiny
    ordinary shot, cutting an unapproved action, or adding a video hold.
    """
    from voice_edge_fitting import enabled
    from package_count_matching import trigger, clause_range
    from voice_duration_matching import target_frames
    if not (enabled(cfg) and cfg.get('match_video_to_voice') and package and
            package.get('narration_enabled') and anchor and
            anchor.get('timestamp_source') == 'whisper_known_text_dtw_v1'):
        return None
    raw=package.get('raw_text','');voice=package.get('voice') or {}
    match=trigger(raw)
    if not match or list(clause_range(raw,match)) != anchor.get('source_range'):
        return None
    left,right=anchor['source_range']
    if raw[left:right] != anchor.get('phrase') or any(ch.isalnum() for ch in raw[right:]):
        return None
    if not voice or target != target_frames(voice,cfg['fps']):
        return None
    fps=cfg['fps'];a=anchor['start_frame'];b=anchor['end_frame']
    if (a != max(0,math.floor(anchor['start_seconds']*fps+1e-7)) or
            b != math.ceil(anchor['end_seconds']*fps-1e-7)):
        return None
    margin=math.floor(float(cfg.get('package_anchor_margin_seconds',.2))*fps+1e-7)
    seconds=float(voice['effective_voice_duration'])-anchor['end_seconds']
    if not (target>b+margin and 0<seconds<=MAX_RELEASE_SECONDS+1e-8 and
            0<target-b<=math.ceil(MAX_RELEASE_SECONDS*fps)):
        return None
    return dict(revision=REVISION,reason='final_quantity_clause_voice_release',
        raw_text_sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest(),
        source_range=[left,right],anchor_end_frame=b,normal_end_limit_frame=b+margin,
        allowed_end_frame=target,release_frames=target-b,
        effective_voice_duration=float(voice['effective_voice_duration']),
        release_seconds=seconds,max_release_seconds=MAX_RELEASE_SECONDS)


def validate_ending_policy(item,cfg):
    from core import AssemblyError
    anchor=item.get('package_anchor') or {}
    proof=anchor.get('ending_policy')
    if proof is None:
        return anchor.get('end_frame',0)+math.floor(float(cfg.get('package_anchor_margin_seconds',.2))*cfg['fps']+1e-7)
    package=dict(item.get('script_package') or {},voice=item.get('voice'),
                 narration_enabled=item.get('narration_enabled'))
    expected=ending_policy(package,anchor,cfg,item['total_frames'])
    if proof != expected or not item.get('voice_fit_revision'):
        raise AssemblyError('PACKAGE_END_POLICY_CHANGED：末句包装收尾依据不一致')
    locked=[c for c in item['clips'] if c.get('planning_block')=='package']
    if not locked or max(c['timeline_start_frame']+c['duration_frames'] for c in locked)!=item['total_frames']:
        raise AssemblyError('PACKAGE_END_POLICY_CHANGED：末句包装没有自然结束于配音帧边界')
    return proof['allowed_end_frame']
