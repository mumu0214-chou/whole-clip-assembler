"""Common new-plan duration: narration ends the clip unless top copy follows."""
import math

REVISION = 'voice_then_optional_top_v1'


def active(cfg):
    return cfg.get('duration_tail_revision') == REVISION


def bounds(cfg):
    return (math.ceil(float(cfg['min_seconds']) * cfg['fps'] - 1e-7),
            math.floor(float(cfg['max_seconds']) * cfg['fps'] + 1e-7))


def check_bounds(frames, cfg):
    from core import AssemblyError
    lo, hi = bounds(cfg)
    if frames < lo:
        raise AssemblyError('VIDEO_DURATION_BELOW_MINIMUM：'
                            f'当前{frames/cfg["fps"]:.3f}秒，短于最短{lo/cfg["fps"]:g}秒；'
                            '无顶部文案的短配音请重选，有顶文的任务须按计划补足真实画面。')
    if frames > hi:
        raise AssemblyError('VIDEO_DURATION_ABOVE_MAXIMUM：完整配音及顶部文案所需画面超过最长时长；'
                            '请重选配音或调整范围，不截断口播。')


def narrated_target(cfg, entry, package, *, validate=True):
    from voice_duration_matching import target_frames
    voice = target_frames(package['voice'], cfg['fps'])
    if entry.get('top_text_enabled'):
        planned = int(entry.get('planned_video_frames', voice))
        total = max(planned, bounds(cfg)[0], voice + math.ceil(2 * cfg['fps'] - 1e-7))
    else:
        total = voice
    if validate:
        check_bounds(total, cfg)
    return total


def validate_item(item, cfg):
    from core import AssemblyError
    if not active(cfg):
        if item.get('duration_tail_revision') == REVISION:
            raise AssemblyError('VIDEO_DURATION_POLICY_CHANGED：新任务的通用时长规则标记缺失')
        return
    frames = item['total_frames']
    check_bounds(frames, cfg)
    if abs(item['duration'] - frames/cfg['fps']) > 1e-8:
        raise AssemblyError('VIDEO_DURATION_SNAPSHOT_CHANGED：成片秒数与帧数不一致')
    if not item.get('narration_enabled'):
        return
    if item.get('duration_tail_revision') != REVISION:
        raise AssemblyError('VIDEO_DURATION_POLICY_CHANGED：任务通用时长快照标记缺失')
    expected = narrated_target(cfg, item, item)
    if frames != expected:
        raise AssemblyError('VIDEO_DURATION_TARGET_MISMATCH：无顶文须随配音结束，有顶文须播放到冻结目标')
    from voice_duration_matching import target_frames
    n = target_frames(item['voice'], cfg['fps'])
    if item.get('top_text_enabled'):
        if (item.get('top_text') or {}).get('windows') != [dict(start_frame=n, end_frame=frames)]:
            raise AssemblyError('VIDEO_TOP_TAIL_CHANGED：顶部文案须在完整配音结束后显示到视频结束')
    elif frames != n or (item.get('top_text') or {}).get('windows'):
        raise AssemblyError('VIDEO_UNREQUESTED_TAIL：无顶部文案不得追加纯画面/BGM尾段')
