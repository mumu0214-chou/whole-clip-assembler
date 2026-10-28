"""Explicit pending-only revisions with a replayable activation journal."""
from copy import deepcopy
from pathlib import Path
import json
import uuid

DONE = ('completed','DONE')
AUDIO_KEYS = ('voice_source','voice_selected_ids','voice_strategy','external_voice_strategy',
              'external_voice_selected_names','external_voice_version_overrides','voice_speed_enabled',
              'voice_speed_factor','voice_speed_selected','voice_pitch_semitones')
LOCKED = ('editing_profile_id','product_spec_id','material_root','music_root','group_folders','output_root')


def reusable_voice(old,new):
    defaults=dict(voice_pitch_semitones=0.,voice_source='indextts25')
    return all(old.get(k,defaults.get(k))==new.get(k,defaults.get(k)) for k in AUDIO_KEYS)


def same_local_profile(current,package):
    if not current or not current.get('available'):return False
    from voice_pipeline import _file_hash
    frozen=package.get('voice_request_profile') or {}
    ignored={'available','reference_path','emotion_reference_path','reference_path_sha256','emotion_reference_path_sha256'}
    if {k:v for k,v in current.items() if k not in ignored}!={k:v for k,v in frozen.items() if k not in ignored}:return False
    for path_key,hash_key in [('reference_path','reference_sha256'),('emotion_reference_path','emotion_reference_sha256')]:
        if path_key=='emotion_reference_path' and current.get('emotion_mode')!='audio':continue
        source=Path(current.get(path_key,'') or '.')
        if not source.is_file() or _file_hash(source)!=package.get(hash_key):return False
    return True


def style_only(old,new):
    ignore={'count','seed','task_snapshot','voice_job_temp_dir','review_first_enabled','editorial_snapshot','video_mode_schedule',
            'grouped_coverage_snapshot','clip_sfx_bindings_snapshot','ball_scene_bindings_snapshot',
            'voice_gain_db','voice_trim_db','bgm_trim_db','bgm_gain','audio_mix_profile',
            'visual_layout','visual_layout_revision','ffmpeg','ffprobe','node','browser','concurrency'}
    appearance={'top_text_style','top_text_font_size','top_text_top','top_text_max_width','top_text_stroke'}
    return all(old.get(k)==new.get(k) for k in set(old)|set(new)
               if k not in ignore|appearance and not k.startswith('subtitle_'))


class HistoryView:
    def __init__(self,store,bid):self.store=store;self.root=store.root;self.bid=bid
    def usage(self,*a,**kw):return self.store.usage(exclude_batch=self.bid)
    def __getattr__(self,k):return getattr(self.store,k)


def prepare(path,current,store,scripts,profiles,provider,log=lambda _:None,stop=None):
    from core import read_json,atomic_json,resolve_tool,plan_batch,scan_library,validate_item
    from voice_pipeline import prepare_script_packages,freeze_script_on_item
    from video_plan_policy import apply_item_timing
    from visual_layout import finalize
    from review_session import resume
    old=read_json(path);pending=[i for i in old['items'] if i.get('status') not in DONE]
    journal=read_json(Path(path).parent/'revision_pending.json',{})
    if journal and journal.get('state')!='committed':raise ValueError('上次修订尚待恢复，请先继续该批次以完成恢复')
    if not pending:raise ValueError('本批已无待生成条目')
    if any(i.get('status')=='rendering' for i in pending):raise ValueError('请先等本条完成并暂停')
    cfg=deepcopy(current)
    for k in LOCKED:
        if old['config'].get(k)!=cfg.get(k):raise ValueError('审片调整请保持本批商品、规格、素材及输出目录：'+k)
    cfg['count']=len(pending)
    cfg['script_library_path']=str(scripts.path)
    revision=uuid.uuid4().hex[:12]
    folder=Path(path).parent/'revisions'/revision;folder.mkdir(parents=True,exist_ok=False)
    atomic_json(folder/'before.json',old)
    cfg['voice_job_temp_dir']=str(store.root/'job_temp'/('revision_'+revision))
    job=Path(cfg['voice_job_temp_dir']);job.mkdir(parents=True);(job/'.assembler_job_temp').touch()
    for k in ('task_snapshot','video_mode_schedule','grouped_coverage_snapshot','editorial_snapshot','clip_sfx_bindings_snapshot','ball_scene_bindings_snapshot'):
        cfg.pop(k,None)
    try:
        reuse=reusable_voice(old['config'],cfg)
        if reuse and cfg.get('voice_source')!='external_files':
            byid={p['voice_id']:p for p in profiles}
            reuse=all(not i.get('narration_enabled') or same_local_profile(byid.get(i.get('script_package',{}).get('voice_profile')),i.get('script_package',{})) for i in pending)
        from video_plan_policy import build_schedule
        from voice_pipeline import parse_scripts
        schedule=build_schedule(cfg);cfg['video_mode_schedule']=schedule
        packages=[]
        for slot,i in zip(schedule,pending):
            p=deepcopy(i.get('script_package'))
            mode=slot['video_mode']
            if mode=='纯画面+BGM':packages.append(None);continue
            if not p:
                row=scripts.select_for_profile(1,cfg)[0]
                p=parse_scripts(row['raw_text'],False,mode)[0]
                p.update(script_id=row['script_id'],script_library_id=row['script_id'],script_profile_id=row['profile_id'],script_spec_id=row.get('spec_id','default'))
            else:
                flags=parse_scripts(p['raw_text'],False,mode)[0]
                p.update({k:flags[k] for k in ('mode','narration_enabled','subtitle_enabled','bgm_enabled')})
            if p.get('narration_enabled') and not i.get('voice'):reuse=False
            if reuse:
                p['voice']=deepcopy(i.get('voice'));p['diagnostic_number']=i['number']
                if p.get('voice'):
                    from voice_speed import restore_missing,decision,speaker_key
                    if restore_missing(p['voice'],resolve_tool('ffmpeg',cfg['ffmpeg'])):log('已恢复原冻结Hash的配音派生文件。')
                    previous=p.get('voice_speed_snapshot')
                    expected=decision(cfg,speaker_key(p))
                    if previous and previous!=expected:
                        if ({k:v for k,v in previous.items() if k!='pitch_semitones'}!={k:v for k,v in expected.items() if k!='pitch_semitones'} or previous.get('pitch_semitones',0)!=expected.get('pitch_semitones',0)):
                            raise ValueError('原配音处理快照与本次设置不一致，请重新准备声音')
                        p['voice_speed_snapshot']=expected;p['voice']['speed_snapshot']=deepcopy(expected)
            else:
                for k in list(p):
                    if k.startswith(('external_','voice_','frozen_','reference_','emotion_reference_')):p.pop(k,None)
                p['diagnostic_number']=i['number']
            packages.append(p)
        if not reuse:
            indices=[j for j,p in enumerate(packages) if p]
            for j in indices:
                for k in list(packages[j]):
                    if k.startswith(('external_','voice_','frozen_','reference_','emotion_reference_')):packages[j].pop(k,None)
                if cfg.get('voice_source')=='external_files' and packages[j].get('narration_enabled'):
                    if scripts.get(packages[j]['script_library_id'])['raw_text']!=packages[j]['raw_text']:
                        raise ValueError('文案正文已在库中修改；本批保留旧正文，不能自动换用新正文配音。请为新正文另建批次。')
            if cfg.get('voice_source')!='external_files':
                from background_tasks import snapshot_profiles
                profiles=snapshot_profiles(profiles,job)
            ready=prepare_script_packages([packages[j] for j in indices],cfg,resolve_tool('ffmpeg',cfg['ffmpeg']),profiles,provider,log)
            for j,p in zip(indices,ready):packages[j]=p
            if any(p and p.get('voice_error') for p in packages):raise ValueError('新配音准备失败；原批次仍保留，未切换参数')
        if stop and stop.is_set():raise ValueError('已停止更新；原批次保留')
        from editorial import Tags
        old_annotations=old['config'].get('editorial_snapshot',{}).get('assets',{})
        current_annotations=Tags(store.root,cfg['material_root']).rows()
        annotations_changed=any(current_annotations.get(k)!=v for k,v in old_annotations.items()) or any(k not in old_annotations for k in current_annotations)
        if reuse and not annotations_changed and style_only(old['config'],cfg):
            cfg.update({k:deepcopy(v) for k,v in old['config'].items() if k in ('editorial_snapshot','grouped_coverage_snapshot','ball_scene_bindings_snapshot','clip_sfx_bindings_snapshot')})
            updated=dict(old,config=cfg,items=deepcopy(pending))
            for item,p in zip(updated['items'],packages):
                if p:freeze_script_on_item(item,p,cfg)
                item.pop('visual_layout_snapshot',None);item.pop('top_text_visual_snapshot',None)
                apply_item_timing(item,cfg)
            finalize(updated)
            for item in updated['items']:
                if item.get('business_digest'):
                    from grouped_planner import business_digest
                    item['business_digest']=business_digest(item)
            log('仅更新外观/混音：复用已有配音及镜头顺序。')
        else:
            clips,music,_=scan_library(cfg,store,log)
            ranges=read_json(store.root/'music_ranges.json',[])
            updated=plan_batch(clips,music,ranges,cfg,HistoryView(store,old['id']),log,stop,packages)
            if len(updated['items'])!=len(pending):raise ValueError('新参数无法完整准备全部待生成条目；原批次保留')
            log('已按新参数重算配音时长、字幕和选片。')
        updated['id']=old['id'];updated['created_at']=old['created_at']
        for item,prior in zip(updated['items'],pending):
            for k in ('id','video_id','number','daily_number','output_path'):
                if k in prior:item[k]=prior[k]
            item['work_dir']=str(folder/f"{item['number']:02d}")
            Path(item['work_dir']).mkdir(parents=True,exist_ok=True)
            item['receipt_path']=str(Path(item['work_dir'])/'qa.json')
            item.update(status='planned',config_revision=revision);item.pop('error',None)
        from clip_sfx import freeze_batch
        from top_text_styles import freeze_batch as freeze_top
        freeze_batch(updated,store.root,folder);freeze_top(updated,folder)
        from edit_style import freeze_assets
        freeze_assets(updated,folder/'editorial')
        for item in updated['items']:validate_item(item,updated['config'])
        new=deepcopy(old);new.update(config=updated['config'],items=updated['items'],config_revision=revision)
        from voice_references import job_roots
        new['config']['voice_referenced_job_dirs']=job_roots(new['items'],store.root/'job_temp')
        new=resume(new)
        new['revision_history']=old.get('revision_history',[])+[dict(revision=revision,before=str(folder/'before.json'),voice_reused=reuse)]
        # Completed files remain in their original history/config revision.
        # Never put them in a renderer using the newly activated config.
        new['review_completed_items']=old.get('review_completed_items',[])+[deepcopy(i) for i in old['items'] if i.get('status') in DONE]
        atomic_json(folder/'after.json',new)
        atomic_json(Path(path).parent/'revision_pending.json',dict(revision=revision,after=str(folder/'after.json'),before=str(folder/'before.json'),state='prepared'))
        return new
    except Exception:
        scripts.external.abandon_preparation(cfg['voice_job_temp_dir'])
        raise


def activate(path,store,scripts,*,leased=False):
    """Idempotent after a crash between SQLite and the atomic Manifest rename."""
    from core import read_json,atomic_json,now,validate_item
    path=Path(path);journal=path.parent/'revision_pending.json'
    record=read_json(journal,{})
    if not record or record.get('state')=='committed':return read_json(path)
    if not leased:
        from batch_retention import run_lease
        with run_lease(path,store):return activate(path,store,scripts,leased=True)
    next_path=Path(record['after']).resolve()
    if not next_path.is_relative_to(path.parent.resolve()/'revisions'):
        raise ValueError('批次修订日志路径无效')
    new=read_json(next_path)
    if new['id']!=read_json(path)['id']:raise ValueError('修订批次身份不一致')
    for item in new['items']:
        if item.get('status') not in DONE:validate_item(item,new['config'])
    with scripts.external.locked(),scripts._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for item in new['items']:
            if item.get('status') in DONE:continue
            vid=item.get('video_id') or item['id'];rid=item.get('external_ref_id')
            previous_script=db.execute('SELECT * FROM script_uses WHERE video_id=?',(vid,)).fetchone()
            if previous_script and previous_script['script_id']!=item.get('script_library_id'):
                if previous_script['state']=='success':raise ValueError('禁止替换已成功视频的文案')
                db.execute('UPDATE scripts SET reserved_count=MAX(0,reserved_count-1) WHERE script_id=?',(previous_script['script_id'],))
                db.execute('DELETE FROM script_uses WHERE video_id=?',(vid,))
            existing=db.execute('SELECT * FROM external_uses WHERE video_id=?',(vid,)).fetchone()
            ref=db.execute('SELECT * FROM external_refs WHERE id=?',(rid,)).fetchone() if rid else None
            if rid and not ref:raise ValueError('新配音冻结引用丢失，保留旧批次')
            same=bool(ref and existing and existing['version_id']==ref['version_id'] and ref['video_id']==vid and ref['state']=='frozen')
            if not same:
                if existing:
                    if existing['state']=='success':raise ValueError('禁止替换已成功视频的配音')
                    db.execute('UPDATE external_versions SET reserved_count=MAX(0,reserved_count-1) WHERE id=?',(existing['version_id'],))
                    db.execute('DELETE FROM external_uses WHERE video_id=?',(vid,))
                if not ref:db.execute("UPDATE external_refs SET video_id=NULL,state='history' WHERE video_id=?",(vid,))
                if ref:
                    if ref['state']!='prepared':db.execute('UPDATE external_versions SET reserved_count=reserved_count+1 WHERE id=?',(ref['version_id'],))
                    db.execute("UPDATE external_refs SET video_id=NULL,state='history' WHERE video_id=? AND id<>?",(vid,rid))
                    db.execute("UPDATE external_refs SET video_id=?,state='frozen' WHERE id=?",(vid,rid))
                    db.execute('INSERT INTO external_uses VALUES(?,?,?,?,?)',(vid,new['id'],ref['version_id'],'reserved',now()))
        db.execute("UPDATE external_preparations SET state='finalized' WHERE id=?",(new['config']['voice_job_temp_dir'],))
    # Each stage is replayable. The journal is committed only after all stages.
    store.reserve(new)
    atomic_json(path,new)
    scripts.reserve_batch(new)
    record['state']='committed';atomic_json(journal,record)
    return new
