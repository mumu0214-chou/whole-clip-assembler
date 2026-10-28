# -*- coding: utf-8 -*-
"""Retry only frozen failed voice tasks; never call the visual planner."""
from __future__ import annotations

from pathlib import Path
import runtime_diagnostics as diag

from voice_pipeline import (VOICE_TEMP_ROOT, VoiceError, freeze_script_on_item,
                            prepare_voice, voice_failure_stage)
from video_plan_policy import apply_item_timing
from temp_lifecycle import cleanup_item_media


def retry_batch_voice(batch: dict, provider, ffmpeg: str, *, cache_root: Path | None = None,
                      log=lambda _: None) -> dict:
    cfg = batch['config']
    job_root = Path(cfg.get('voice_job_temp_dir') or
                    (Path(cache_root).parent if cache_root is not None else
                     VOICE_TEMP_ROOT / batch.get('id', 'unsaved_retry')))
    voice_root = Path(cache_root) if cache_root is not None else job_root/'voice'
    job_root.mkdir(parents=True, exist_ok=True)
    (job_root/'.assembler_job_temp').write_text('', encoding='utf-8')
    for failed in batch.get('voice_failed_tasks', []):
        failed['error'] = '旧批次该项未保存Clip/BGM方案，无法在不重新选片的前提下重试。'
        failed['voice_failure_stage'] = '旧Manifest缺少冻结画面方案'
    for item in batch['items']:
        if item.get('status') not in ('VOICE_FAILED', 'VOICE_RETRYING'):
            continue
        item['status'] = 'VOICE_RETRYING'
        item.pop('error', None)
        generated = None
        try:
            cleanup_item_media(item, job_root)
            package = dict(item['script_package'])
            profile = package.get('voice_request_profile')
            if not profile:
                raise VoiceError('旧Manifest缺少冻结音色参数，不能保证原方案不变。')
            if profile.get('voice_id') != package.get('voice_profile'):
                raise VoiceError('冻结音色身份不一致。')
            voice = prepare_voice(package, profile, provider, ffmpeg,
                                  cache_root=voice_root, log=log, cache_enabled=cfg.get('voice_cache_enabled', True))
            generated = voice
            if package.get('frozen_model_versions') and voice['model_versions'] != package['frozen_model_versions']:
                raise VoiceError('配音模型内容已变化，不能替换已冻结方案的配音。')
            if item.get('voice_cache_key') and voice['voice_cache_key'] != item['voice_cache_key']:
                raise VoiceError('音色或模型版本已变化，不能替换已冻结方案的配音。')
            if item.get('effective_voice_duration') and abs(
                    voice['effective_voice_duration']-item['effective_voice_duration']) > 1/cfg['fps']:
                raise VoiceError('重试配音时长变化超过一帧，不能直接用于已冻结画面。')
            if voice['effective_voice_duration'] > item['duration'] + 1/cfg['fps']:
                raise VoiceError('原画面方案时长不足以容纳本次配音；未重新选片。')
            package['voice'] = voice
            package.pop('voice_error', None)
            cfg['voice_job_temp_dir'] = str(job_root)
            item['voice_temp_version'] = 'job_temp_v1'
            freeze_script_on_item(item, package, cfg)
            apply_item_timing(item, cfg)
            item['status'] = 'planned'
            item.pop('error', None)
            item.pop('voice_failure_stage', None)
            log(f"{item['script_id']}配音恢复成功；Clip、BGM和画面方案保持不变。")
        except Exception as exc:
            diag.error(exc,component="voice-retry",batch_id=batch.get("id",""),job_id=item["id"],number=item["number"])
            if generated is not None:
                cleanup_item_media({'id':item['id'], 'voice_temp_version':'job_temp_v1',
                                    'voice':generated}, job_root)
            item['status'] = 'VOICE_FAILED'
            item['voice_failure_stage'] = voice_failure_stage(exc)
            item['error'] = str(exc)
            log(f"{item.get('script_id',item['id'])}配音重试失败：{exc}")
    return batch
