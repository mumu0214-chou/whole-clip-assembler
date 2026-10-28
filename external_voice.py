"""External narration identities, additive migration and recoverable file operations.

The existing script database is the sole binding truth. Media operations are
journaled separately from SQLite commits; frozen references outlive UI bindings.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
import hashlib, json, os, random, re, shutil, subprocess, time, uuid, wave
from file_mutex import file_mutex

SOURCE = 'external_files'
REVISION = 'external_pcm_full_v1'

def stamp(): return datetime.now().astimezone().isoformat(timespec='seconds')
def ident(): return uuid.uuid4().hex
def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def error(text):
    from voice_pipeline import VoiceError
    return VoiceError(text)
def emit(event, **data):
    # Diagnostics must never roll back a completed file/database operation.
    try:
        import runtime_diagnostics as diag
        diag.emit(event, '外部配音管理', **data)
    except Exception: pass
def migrate(db):
    """Transactional additive changes; triggers also protect edits by older UI processes."""
    if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
    columns = {r[1] for r in db.execute('PRAGMA table_info(scripts)')}
    for name, sql in [('script_uid','TEXT'), ('display_id','TEXT'), ('body_version','INTEGER NOT NULL DEFAULT 1'), ('designated_voice','TEXT')]:
        if name not in columns: db.execute(f'ALTER TABLE scripts ADD COLUMN {name} {sql}')
    db.execute('UPDATE scripts SET script_uid=lower(hex(randomblob(16))),display_id=script_id WHERE script_uid IS NULL')
    statements = [
        'CREATE UNIQUE INDEX IF NOT EXISTS scripts_uid ON scripts(script_uid)',
        '''CREATE TABLE IF NOT EXISTS script_versions(uid TEXT,version INTEGER,body_hash TEXT,raw_text TEXT,created TEXT,PRIMARY KEY(uid,version))''',
        '''CREATE TABLE IF NOT EXISTS external_resources(id TEXT PRIMARY KEY,sha TEXT NOT NULL,mode TEXT NOT NULL,path TEXT NOT NULL,source_path TEXT,metadata TEXT NOT NULL,state TEXT NOT NULL,bytes INTEGER,created TEXT)''',
        '''CREATE TABLE IF NOT EXISTS external_versions(id TEXT PRIMARY KEY,uid TEXT NOT NULL,resource_id TEXT NOT NULL,name TEXT,note TEXT,bound_version INTEGER,bound_hash TEXT,enabled INTEGER,review TEXT,deleted_op TEXT,usage_count INTEGER DEFAULT 0,reserved_count INTEGER DEFAULT 0,last_used TEXT,created TEXT)''',
        '''CREATE INDEX IF NOT EXISTS external_versions_script ON external_versions(uid,deleted_op)''',
        '''CREATE TABLE IF NOT EXISTS external_history(id TEXT PRIMARY KEY,version_id TEXT,resource_id TEXT,details TEXT,created TEXT)''',
        '''CREATE TABLE IF NOT EXISTS external_human_reviews(version_id TEXT PRIMARY KEY,resource_id TEXT NOT NULL,body_hash TEXT NOT NULL,audio_sha TEXT NOT NULL,approved_at TEXT NOT NULL)''',
        '''CREATE TRIGGER IF NOT EXISTS preserve_external_human_review AFTER UPDATE OF review ON external_versions WHEN NEW.review!='confirmed' AND EXISTS(SELECT 1 FROM external_human_reviews h JOIN external_resources r ON r.id=NEW.resource_id JOIN scripts s ON s.script_uid=NEW.uid WHERE h.version_id=NEW.id AND h.resource_id=NEW.resource_id AND h.body_hash=s.text_hash AND h.body_hash=NEW.bound_hash AND h.audio_sha=r.sha) BEGIN UPDATE external_versions SET review='confirmed' WHERE id=NEW.id; END''',
        '''CREATE TABLE IF NOT EXISTS external_refs(id TEXT PRIMARY KEY,preparation TEXT,video_id TEXT UNIQUE,version_id TEXT,resource_id TEXT,uid TEXT,body_version INTEGER,body_hash TEXT,frozen_path TEXT,sha TEXT,duration REAL,state TEXT,created TEXT)''',
        '''CREATE TABLE IF NOT EXISTS external_uses(video_id TEXT PRIMARY KEY,batch_id TEXT,version_id TEXT,state TEXT,updated TEXT)''',
        '''CREATE TABLE IF NOT EXISTS external_operations(id TEXT PRIMARY KEY,kind TEXT,payload TEXT,state TEXT,error TEXT,created TEXT,updated TEXT)''',
        '''CREATE TABLE IF NOT EXISTS external_schema(revision INTEGER PRIMARY KEY,created TEXT)''',
        '''CREATE TABLE IF NOT EXISTS external_preparations(id TEXT PRIMARY KEY,pid INTEGER,state TEXT,created TEXT)''',
        '''CREATE TRIGGER IF NOT EXISTS script_identity_insert AFTER INSERT ON scripts BEGIN
        UPDATE scripts SET script_uid=COALESCE(NEW.script_uid,lower(hex(randomblob(16)))),display_id=COALESCE(NEW.display_id,NEW.script_id) WHERE script_id=NEW.script_id;
        INSERT OR IGNORE INTO script_versions SELECT script_uid,body_version,text_hash,raw_text,created_at FROM scripts WHERE script_id=NEW.script_id; END''',
        '''CREATE TRIGGER IF NOT EXISTS script_body_edit AFTER UPDATE OF text_hash ON scripts WHEN OLD.text_hash!=NEW.text_hash BEGIN
        UPDATE scripts SET body_version=OLD.body_version+1 WHERE script_id=NEW.script_id;
        INSERT INTO script_versions SELECT script_uid,body_version,text_hash,raw_text,updated_at FROM scripts WHERE script_id=NEW.script_id;
        UPDATE external_versions SET review='review' WHERE uid=NEW.script_uid AND deleted_op IS NULL; END''',
    ]
    for sql in statements: db.execute(sql)
    db.execute('INSERT OR IGNORE INTO script_versions SELECT script_uid,body_version,text_hash,raw_text,created_at FROM scripts')
    db.execute('INSERT OR IGNORE INTO external_schema VALUES(1,?)',(stamp(),))
    db.execute('INSERT OR IGNORE INTO external_schema VALUES(2,?)',(stamp(),))

class ExternalVoices:
    def __init__(self, library):
        self.library = library
        self.root = library.path.parent/'external_voice_resources'

    def start_preparation(self, preparation):
        with self.library._connect() as db:db.execute('INSERT OR REPLACE INTO external_preparations VALUES(?,?,?,?)',(preparation,os.getpid(),'active',stamp()))

    def reconcile_preparations(self):
        with self.library._connect() as db:rows=[dict(r) for r in db.execute("SELECT * FROM external_preparations WHERE state='active'")]
        for r in rows:
            if process_alive(r['pid']):continue
            found=False
            for path in (self.library.path.parent/'batches').glob('*/batch_manifest.json'):
                try:batch=json.loads(path.read_text(encoding='utf-8'))
                except (OSError,ValueError):continue
                if batch.get('config',{}).get('voice_job_temp_dir')==r['id']:
                    self.finalize(batch,r['id']);self.library.reconcile_batch(batch);found=True;break
            if not found:self.abandon_preparation(r['id'])

    @contextmanager
    def locked(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with file_mutex(self.root/'.operations.lock', timeout=120): yield

    def _script(self, db, script_id, *, active=True):
        row = db.execute('SELECT * FROM scripts WHERE script_id=? OR script_uid=?',(script_id,script_id)).fetchone()
        if not row or (active and row['deleted_at']): raise error(f'文案不存在或已回收：{script_id}')
        return dict(row)

    def operation(self, kind, payload):
        op=ident()
        with self.library._connect() as db:
            db.execute('INSERT INTO external_operations VALUES(?,?,?,?,?,?,?)', (op,kind,json.dumps(payload,ensure_ascii=False),'pending','',stamp(),stamp()))
        return op

    def update_op(self, op, payload, state, failure=''):
        with self.library._connect() as db:
            db.execute('UPDATE external_operations SET payload=?,state=?,error=?,updated=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),state,failure,stamp(),op))
        emit('external_operation', operation_id=op, state=state, error=failure)

    def probe(self, path, ffmpeg, ffprobe, stream=None):
        p=Path(path).resolve()
        if not p.is_file(): raise error(f'文件不存在：{p}')
        start=time.monotonic()
        result=subprocess.run([ffprobe,'-v','error','-show_streams','-show_format','-of','json',str(p)],capture_output=True,text=True,encoding='utf-8',timeout=45)
        if result.returncode: raise error('音频探测失败：'+result.stderr[-500:])
        info=json.loads(result.stdout); tracks=[s for s in info.get('streams',[]) if s.get('codec_type')=='audio']
        if not tracks: raise error('文件没有有效音轨：'+str(p))
        if len(tracks)>1 and stream is None:
            return dict(path=str(p),tracks=tracks,status='track_choice',sha=digest(p))
        track=next((s for s in tracks if s['index']==stream),None) if stream is not None else tracks[0]
        if track is None: raise error('所选音轨不存在。')
        decoded=subprocess.run([ffmpeg,'-v','error','-xerror','-i',str(p),'-map',f'0:{track["index"]}','-vn','-f','null','-'],capture_output=True,text=True,timeout=180)
        if decoded.returncode: raise error('音轨无法完整解码：'+decoded.stderr[-500:])
        duration=float(track.get('duration') or info.get('format',{}).get('duration') or 0)
        if duration<=0: raise error('无法确定音频时长。')
        return dict(path=str(p),sha=digest(p),stream=track['index'],duration=duration,sample_rate=int(track.get('sample_rate',0)),channels=int(track.get('channels',0)),codec=track.get('codec_name'),bytes=p.stat().st_size,status='valid',probe_seconds=time.monotonic()-start)

    def suggest(self, filename, profile_id=None,spec_id=None):
        # Complete digit token: L0000440 never falls back to L000044.
        matches=re.findall(r'(?<![A-Za-z0-9])(L\d+)(?!\d)',Path(filename).stem,re.I)
        if len(matches)!=1: return None
        with self.library._connect() as db:
            rows=[dict(r) for r in db.execute('SELECT * FROM scripts WHERE display_id=? AND deleted_at IS NULL',(matches[0].upper(),))]
        if profile_id: rows=[r for r in rows if r['profile_id']==profile_id]
        if spec_id is not None:rows=[r for r in rows if r['spec_id']==spec_id]
        return rows[0]['script_id'] if len(rows)==1 else None

    def preview(self, paths, ffmpeg, ffprobe, *, script_id=None, profile_id=None,spec_id=None, progress=lambda _:None, cancel=None):
        result=[]
        for i,p in enumerate(paths):
            if cancel and cancel.is_set(): break
            try:
                row=self.probe(p,ffmpeg,ffprobe)
                row['script_id']=script_id or self.suggest(p,profile_id,spec_id)
                row['classification']='新绑定' if row.get('script_id') else '待分配'
                if row.get('script_id') and row['status']=='valid':
                    with self.library._connect() as db:
                        s=self._script(db,row['script_id'])
                        if profile_id is not None:self.library._check_profile(dict(s),profile_id,spec_id)
                        dup=db.execute('SELECT v.* FROM external_versions v JOIN external_resources r ON r.id=v.resource_id WHERE v.uid=? AND r.sha=? AND r.state!=\'purged\' AND json_extract(r.metadata,\'$.stream\')=?',(s['script_uid'],row['sha'],row['stream'])).fetchone()
                    if dup: row.update(classification='重复',duplicate_id=dup['id'],duplicate_state=('回收' if dup['deleted_op'] else '停用' if not dup['enabled'] else dup['review']))
            except Exception as exc: row=dict(path=str(p),status='invalid',classification='失效',error=str(exc),script_id=script_id)
            result.append(row);progress(f'已检查 {i+1}/{len(paths)}：{Path(p).name}')
        return result

    def bind(self, row, *, managed=True, confirmed=False, name=None, note='', exported=None, staged=False, expected_script=None):
        if row.get('status')!='valid' or not row.get('script_id'): raise error('请先校验文件、选择音轨并分配文案。')
        source=Path(row['path']); planned_id=ident()
        planned_dest=self.root/'managed'/(planned_id+source.suffix.lower()) if managed else source
        payload=dict(row=row,managed=managed,owned_target=str(planned_dest) if managed else None,confirmed=confirmed)
        op=self.operation('import',payload)
        try:
            with self.locked(), self.library._connect() as db:
                db.execute('BEGIN IMMEDIATE');s=self._script(db,row['script_id'])
                if expected_script and any(s[key]!=expected_script[key] for key in ('script_uid','profile_id','display_id')+ (('spec_id',) if 'spec_id' in expected_script else ())):
                    raise error('配对期间文案ID或品类发生变化，未绑定旧映射，请刷新重试。')
                if digest(source)!=row['sha']: raise error('源文件在校验后发生变化，请重新检查。')
                dup=db.execute('SELECT v.* FROM external_versions v JOIN external_resources r ON r.id=v.resource_id WHERE v.uid=? AND r.sha=? AND r.state!=\'purged\' AND json_extract(r.metadata,\'$.stream\')=?',(s['script_uid'],row['sha'],row['stream'])).fetchone()
                if dup:
                    result=dict(status='duplicate',version_id=dup['id'],state='回收' if dup['deleted_op'] else '停用' if not dup['enabled'] else dup['review'])
                else:
                    resource=db.execute("SELECT * FROM external_resources WHERE sha=? AND mode=? AND state='ready' AND json_extract(metadata,'$.stream')=?",(row['sha'],'managed' if managed else 'linked',row['stream'])).fetchone()
                    if resource is not None and (not Path(resource['path']).is_file() or digest(resource['path'])!=row['sha']): resource=None
                    if resource is None:
                        rid=planned_id;dest=planned_dest
                        if managed:
                            dest.parent.mkdir(parents=True,exist_ok=True);part=dest.with_suffix(dest.suffix+'.part')
                            shutil.copy2(source,part)
                            if digest(part)!=row['sha']: raise error('复制校验失败。')
                            os.replace(part,dest)
                        db.execute('INSERT INTO external_resources VALUES(?,?,?,?,?,?,?,?,?)',(rid,row['sha'],'managed' if managed else 'linked',str(dest),str(source),json.dumps(row,ensure_ascii=False),'ready',row['bytes'],stamp()))
                    else: rid=resource['id']
                    vid=ident();review='confirmed' if confirmed else 'review'
                    if exported and (exported.get('body_hash')!=s['text_hash'] or exported.get('internal_id')!=s['script_uid']): review='review'
                    db.execute('INSERT INTO external_versions(id,uid,resource_id,name,note,bound_version,bound_hash,enabled,review,created) VALUES(?,?,?,?,?,?,?,?,?,?)',(vid,s['script_uid'],rid,name or source.stem,note,s['body_version'],s['text_hash'],int(not staged),review,stamp()))
                    result=dict(status='bound',version_id=vid,resource_id=rid,review=review)
            self.update_op(op,dict(payload,result=result), 'complete');return result
        except Exception as exc:
            self.update_op(op,payload,'failed',str(exc));raise

    def retry_import(self, op, ffmpeg, ffprobe):
        with self.locked(),self.library._connect() as db:
            record=db.execute("SELECT * FROM external_operations WHERE id=? AND kind='import'",(op,)).fetchone()
            if not record:raise error('导入操作不存在。')
            payload=json.loads(record['payload'])
            if record['state']=='complete':return payload.get('result')
            target=payload.get('owned_target')
            if target:
                p=Path(target).resolve()
                if not p.is_relative_to((self.root/'managed').resolve()):raise error('操作目标超出托管目录。')
                registered=db.execute('SELECT 1 FROM external_resources WHERE path=?',(str(p),)).fetchone()
                if not registered:
                    # Only exact files owned by this incomplete operation; never its source.
                    for candidate in (p,p.with_suffix(p.suffix+'.part')):
                        if candidate.is_file():candidate.unlink()
        row=payload['row'];checked=self.probe(row['path'],ffmpeg,ffprobe,stream=row.get('stream'));checked['script_id']=row['script_id']
        result=self.bind(checked,managed=payload.get('managed',True),confirmed=False)
        self.update_op(op,dict(payload,result=result),'complete');return result

    def versions(self, script_id=None, *, include_deleted=False):
        with self.library._connect() as db:
            uid=self._script(db,script_id,active=False)['script_uid'] if script_id else None
            where=' WHERE 1=1';args=[]
            if uid:where+=' AND v.uid=?';args.append(uid)
            if not include_deleted:where+=' AND v.deleted_op IS NULL'
            rows=[dict(r) for r in db.execute('SELECT v.*,r.path,r.mode,r.state AS file_state,r.sha,r.metadata,r.source_path,s.script_id,s.display_id,s.body_version AS current_version,s.text_hash AS current_hash,s.deleted_at AS script_deleted FROM external_versions v JOIN external_resources r ON r.id=v.resource_id JOIN scripts s ON s.script_uid=v.uid'+where+' ORDER BY v.created,v.id',args)]
            approvals={r['version_id']:dict(r) for r in db.execute('SELECT * FROM external_human_reviews')}
        for r in rows:
            r['metadata']=json.loads(r['metadata']);r['duration']=r['metadata']['duration']
            r['missing']=not Path(r['path']).is_file() or r['file_state']!='ready'
            human=approvals.get(r['id'])
            r['human_review']=human if human and human['resource_id']==r['resource_id'] and human['body_hash']==r['current_hash'] and human['audio_sha']==r['sha'] else None
            r['valid']=not r['missing'] and not r['deleted_op'] and not r['script_deleted'] and bool(r['enabled']) and r['review']=='confirmed' and r['bound_hash']==r['current_hash']
        return rows

    def candidates(self, script_id, cfg=None):
        from external_voice_catalog import filter_rows
        if cfg and cfg.get('product_spec_revision'):
            from product_catalog import check_script
            check_script(self.library.get(script_id),cfg)
        return filter_rows([r for r in self.versions(script_id) if r['valid']], cfg or {})

    def voice_catalog(self, profile_id,spec_id=None):
        from external_voice_catalog import voice_name
        with self.library._connect() as db:
            ids={r['script_id'] for r in self.library.list(profile_id=profile_id,spec_id=spec_id)}
        result={}
        for row in self.versions():
            name=voice_name(row)
            if row['script_id'] in ids and name:
                counts=result.setdefault(name,dict(bound=0,valid=0))
                counts['bound']+=1;counts['valid']+=int(row['valid'])
        return result

    def organize_library(self, directory, **kwargs):
        from external_voice_catalog import organize_library
        return organize_library(self, directory, **kwargs)

    def pair_by_id(self,directory,profile_id,ffmpeg,ffprobe,**kwargs):
        from external_voice_catalog import pair_by_id
        return pair_by_id(self,directory,profile_id,ffmpeg,ffprobe,**kwargs)

    def summary(self, script_id):
        rows=self.versions(script_id)
        return dict(bound=len(rows),valid=sum(r['valid'] for r in rows),review=sum(r['review']!='confirmed' or r['bound_hash']!=r['current_hash'] for r in rows),missing=sum(r['missing'] for r in rows))

    def change(self, ids, action, value=None):
        ids=list(dict.fromkeys(ids))
        if not ids:raise error('请选择配音版本。')
        with self.locked(),self.library._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for vid in ids:
                row=db.execute('SELECT * FROM external_versions WHERE id=? AND deleted_op IS NULL',(vid,)).fetchone()
                if not row:raise error('配音已回收或不存在，请刷新。')
                s=self._script(db,row['uid'])
                if action=='confirm':
                    resource=db.execute('SELECT * FROM external_resources WHERE id=?',(row['resource_id'],)).fetchone()
                    if not resource or not Path(resource['path']).is_file() or digest(resource['path'])!=resource['sha']:
                        raise error('音频缺失或文件内容已改变，请先重新定位；没有可确认的当前音频。')
                    approval=dict(version_id=vid,resource_id=row['resource_id'],body_hash=s['text_hash'],audio_sha=resource['sha'],approved_at=stamp())
                    db.execute('INSERT OR REPLACE INTO external_human_reviews VALUES(?,?,?,?,?)',tuple(approval.values()))
                    db.execute('INSERT INTO external_history VALUES(?,?,?,?,?)',(ident(),vid,row['resource_id'],json.dumps(dict(action='human_confirmed',previous_machine_review=row['review'],approval=approval),ensure_ascii=False),stamp()))
                    db.execute("UPDATE external_versions SET review='confirmed',bound_version=?,bound_hash=? WHERE id=?",(s['body_version'],s['text_hash'],vid))
                elif action in ('enable','disable'):db.execute('UPDATE external_versions SET enabled=? WHERE id=?',(int(action=='enable'),vid))
                elif action=='rename':db.execute('UPDATE external_versions SET name=? WHERE id=?',(str(value).strip(),vid))
                elif action=='note':db.execute('UPDATE external_versions SET note=? WHERE id=?',(str(value).strip(),vid))
                elif action=='designate':db.execute('UPDATE scripts SET designated_voice=? WHERE script_uid=?',(vid,s['script_uid']))
                else:raise error('未知配音操作。')

    def replace(self, vid, row, *, relink=False, confirmed=False):
        with self.library._connect() as db:
            old=db.execute('SELECT * FROM external_versions WHERE id=? AND deleted_op IS NULL',(vid,)).fetchone()
            if not old:raise error('版本不存在。')
            s=self._script(db,old['uid']);old=dict(old)
        row=dict(row,script_id=s['script_id']);new=self.bind(row,managed=not relink,confirmed=confirmed,staged=True)
        if new['status']=='duplicate' and new['version_id']==vid:
            # Only relocate identical linked content; no implicit enabling/review approval.
            with self.locked(),self.library._connect() as db:
                r=db.execute('SELECT * FROM external_resources WHERE id=?',(old['resource_id'],)).fetchone()
                if r['mode']=='linked':db.execute("UPDATE external_resources SET path=?,state='ready' WHERE id=?",(row['path'],r['id']))
                elif not Path(r['path']).is_file() or digest(r['path'])!=r['sha']:
                    dest=Path(r['path']).resolve()
                    if not dest.is_relative_to((self.root/'managed').resolve()):raise error('托管资源路径不合法，保留原件。')
                    part=dest.with_suffix(dest.suffix+'.part');dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(row['path'],part)
                    if digest(part)!=r['sha']:raise error('重新定位复制校验失败。')
                    os.replace(part,dest);db.execute("UPDATE external_resources SET state='ready' WHERE id=?",(r['id'],))
            return vid
        if new['status']=='duplicate':raise error('该内容已经绑定到另一个版本，请使用已有版本；未覆盖。')
        op=self.operation('replace',dict(previous=old,new=new))
        try:
            with self.locked(),self.library._connect() as db:
                db.execute('BEGIN IMMEDIATE');fresh=db.execute('SELECT * FROM external_versions WHERE id=?',(new['version_id'],)).fetchone()
                current=db.execute('SELECT deleted_op,resource_id FROM external_versions WHERE id=?',(vid,)).fetchone()
                if not current or current[0] or current[1]!=old['resource_id']:raise error('替换期间原版本已回收或变化，请刷新后重试。')
                db.execute('INSERT INTO external_history VALUES(?,?,?,?,?)',(ident(),vid,old['resource_id'],json.dumps(old),stamp()))
                db.execute('UPDATE external_versions SET resource_id=?,bound_version=?,bound_hash=?,review=? WHERE id=?',(fresh['resource_id'],fresh['bound_version'],fresh['bound_hash'],fresh['review'],vid))
                db.execute('DELETE FROM external_versions WHERE id=?',(new['version_id'],))
        except Exception as exc:
            with self.locked(),self.library._connect() as db:db.execute('DELETE FROM external_versions WHERE id=? AND enabled=0',(new['version_id'],))
            self.update_op(op,dict(previous=old,new=new),'failed',str(exc));raise
        self.update_op(op,dict(previous=old,new=new),'complete');return vid

    def _protected(self, db, rid):
        if db.execute('SELECT 1 FROM external_versions WHERE resource_id=? AND deleted_op IS NULL',(rid,)).fetchone():return '其他有效绑定'
        if db.execute('SELECT 1 FROM external_refs WHERE resource_id=?',(rid,)).fetchone():return '冻结任务或历史复现引用'
        if db.execute('SELECT 1 FROM external_history WHERE resource_id=?',(rid,)).fetchone():return '替换历史'
        return ''

    def _path_protected(self, db, path, rid):
        target=os.path.normcase(str(Path(path).resolve()))
        for other in db.execute('SELECT id,path,source_path FROM external_resources WHERE id!=?',(rid,)):
            if any(p and os.path.normcase(str(Path(p).resolve()))==target for p in (other['path'],other['source_path'])):
                why=self._protected(db,other['id'])
                if why:return '同一路径仍被其他资源引用：'+why
        return ''

    def delete_preview(self, script_ids=(), version_ids=()):
        with self.library._connect() as db:
            scripts=[self._script(db,x) for x in dict.fromkeys(script_ids)]
            versions=[]
            for s in scripts:versions += [dict(r) for r in db.execute('SELECT * FROM external_versions WHERE uid=? AND deleted_op IS NULL',(s['script_uid'],))]
            for v in dict.fromkeys(version_ids):
                r=db.execute('SELECT * FROM external_versions WHERE id=? AND deleted_op IS NULL',(v,)).fetchone()
                if r:versions.append(dict(r))
            versions=list({v['id']:v for v in versions}.values());vset={v['id'] for v in versions};resources=[]
            for rid in dict.fromkeys(v['resource_id'] for v in versions):
                r=dict(db.execute('SELECT * FROM external_resources WHERE id=?',(rid,)).fetchone())
                others=[x[0] for x in db.execute('SELECT id FROM external_versions WHERE resource_id=? AND deleted_op IS NULL',(rid,)) if x[0] not in vset]
                protected='其他文案/配音绑定' if others else '冻结任务/替换历史' if db.execute('SELECT 1 FROM external_refs WHERE resource_id=?',(rid,)).fetchone() or db.execute('SELECT 1 FROM external_history WHERE resource_id=?',(rid,)).fetchone() else ''
                resources.append(dict(r,protected=protected))
        return dict(scripts=scripts,versions=versions,resources=resources)

    def recycle(self, script_ids=(), version_ids=(), *, delete_originals=False):
        with self.locked():
            payload=self.delete_preview(script_ids,version_ids);payload['files']=[]
            op=self.operation('delete',payload)
            with self.library._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for s in payload['scripts']:
                    self._script(db,s['script_id'])
                    db.execute('UPDATE scripts SET enabled=0,deleted_at=?,updated_at=? WHERE script_id=?',(op,stamp(),s['script_id']))
                for v in payload['versions']:db.execute('UPDATE external_versions SET deleted_op=? WHERE id=? AND deleted_op IS NULL',(op,v['id']))
                # A binding committed before deletion is included even if preview was old.
                for r in payload['resources']:
                    protection=self._protected(db,r['id']);r['protected']=protection
                    if protection:continue
                    paths=[]
                    if r['mode']=='managed':paths.append(('managed',r['path']))
                    if delete_originals: paths.append(('original',r['source_path'] or r['path']))
                    for kind,p in paths:
                        if any(os.path.normcase(str(Path(f['source']).resolve()))==os.path.normcase(str(Path(p).resolve())) for f in payload['files']):continue
                        dest=self.root/'trash'/op/(r['id']+'_'+kind+Path(p).suffix)
                        payload['files'].append(dict(resource_id=r['id'],kind=kind,source=p,trash=str(dest),state='pending' if Path(p).is_file() else 'missing',sha=r['sha']))
                db.execute('UPDATE external_operations SET payload=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),op))
            return self._move_files(op,payload)

    def _move_files(self, op, payload, restore=False):
        failures=[]
        for f in payload.get('files',[]):
            # A file already missing at deletion is an original state, not a failed move.
            # Restore its binding with the missing flag; do not invent an audio file.
            if f['state']=='missing':continue
            if restore and f['state'] not in ('moved','purged') and Path(f['source']).is_file() and not Path(f['trash']).exists() and digest(f['source'])==f['sha']:
                f['state']='restored'
                continue
            expected='restored' if restore else 'moved'
            if f['state']==expected:continue
            src,dst=(Path(f['trash']),Path(f['source'])) if restore else (Path(f['source']),Path(f['trash']))
            try:
                with self.library._connect() as db:
                    protected=(self._protected(db,f['resource_id']) or self._path_protected(db,f['source'],f['resource_id'])) if not restore else ''
                if protected:
                    f.update(state='protected',error=protected);continue
                if not src.is_file():
                    if dst.is_file() and digest(dst)==f['sha']:f['state']=expected
                    else:raise FileNotFoundError(str(src))
                else:
                    if digest(src)!=f['sha']:raise error('文件内容发生变化，保留现场。')
                    if dst.exists():raise error('恢复/回收目标已存在，不覆盖。')
                    dst.parent.mkdir(parents=True,exist_ok=True)
                    shutil.move(str(src),str(dst));f['state']=expected
                f.pop('error',None)
                if f['kind']=='managed':
                    with self.library._connect() as db:db.execute('UPDATE external_resources SET path=?,state=? WHERE id=?',(str(dst),'ready' if restore else 'trashed',f['resource_id']))
            except Exception as exc:f.update(state='failed',error=str(exc));failures.append(str(exc))
            self.update_op(op,payload,'restoring' if restore else 'pending')
        if restore and not failures:
            with self.library._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for s in payload.get('scripts',[]):db.execute('UPDATE scripts SET enabled=?,deleted_at=NULL,updated_at=? WHERE script_id=? AND deleted_at=?',(s['enabled'],stamp(),s['script_id'],op))
                for v in payload.get('versions',[]):db.execute('UPDATE external_versions SET deleted_op=NULL WHERE id=? AND deleted_op=?',(v['id'],op))
        self.update_op(op,payload,'partial' if failures else 'restored' if restore else 'complete','; '.join(failures))
        return dict(operation_id=op,state='partial' if failures else 'restored' if restore else 'complete',payload=payload,errors=failures)

    def trash(self):
        with self.library._connect() as db:
            rows=[dict(r) for r in db.execute("SELECT * FROM external_operations WHERE kind='delete' AND state NOT IN ('restored','purged') ORDER BY created DESC")]
        for r in rows:r['payload']=json.loads(r['payload'])
        return rows

    def recover(self, op, *, restore=True):
        with self.locked(),self.library._connect() as db:
            row=db.execute("SELECT * FROM external_operations WHERE id=? AND kind='delete'",(op,)).fetchone()
            if not row:raise error('回收记录不存在。')
            if row['state'] in ('purged','purge_partial'):raise error('该项已发生永久清理，不能作为完整组恢复。')
            if row['state']=='restored':return dict(operation_id=op,state='restored')
            payload=json.loads(row['payload'])
            # Exit DB connection before IO progress writes.
        with self.locked():return self._move_files(op,payload,restore)

    def purge(self, op):
        with self.locked(),self.library._connect() as db:
            row=db.execute("SELECT * FROM external_operations WHERE id=? AND kind='delete'",(op,)).fetchone()
            if not row or row['state']=='restored':raise error('不是可清理的回收项。')
            payload=json.loads(row['payload']);skipped=[]
            for r in payload['resources']:
                why=self._protected(db,r['id'])
                if why:skipped.append(why)
            for f in payload.get('files',[]):
                why=self._path_protected(db,f['source'],f['resource_id'])
                if why:skipped.append(why)
            if skipped:return dict(state='protected',reasons=skipped)
            if row['state'] not in ('complete','purge_partial'):raise error('文件回收尚未完整完成，请先重试。')
            failures=[]
            for f in payload['files']:
                try:
                    p=Path(f['trash']).resolve()
                    if not p.is_relative_to((self.root/'trash'/op).resolve()):raise error('路径超出登记回收区。')
                    if p.is_file():
                        if digest(p)!=f['sha']:raise error('回收文件发生变化，拒绝清理。')
                        p.unlink()
                    f['state']='purged';f.pop('error',None)
                except Exception as exc:f.update(state='purge_failed',error=str(exc));failures.append(str(exc))
            if failures:
                db.execute("UPDATE external_operations SET state='purge_partial',payload=?,error=?,updated=? WHERE id=?",(json.dumps(payload,ensure_ascii=False),'; '.join(failures),stamp(),op))
                return dict(state='partial',errors=failures)
            # Keep script tombstones and IDs; old IDs are never allocated again.
            db.execute("UPDATE external_resources SET state='purged' WHERE id IN (SELECT resource_id FROM external_versions WHERE deleted_op=?)",(op,))
            db.execute("UPDATE external_operations SET state='purged',payload=?,updated=? WHERE id=?",(json.dumps(payload,ensure_ascii=False),stamp(),op))
        return dict(state='purged')

    def choose_and_freeze(self, script_id, cfg, ffmpeg, *, preparation, rng=None, verify_version=None):
        rng=rng or random.Random()
        selected=None
        try:
            with self.locked():
                excluded=set(cfg.get('external_voice_excluded_versions',[]))
                from external_voice_catalog import filter_rows, voice_name
                rows=[r for r in self.versions(script_id) if r['id'] not in excluded and (r['valid'] or (r['id']==verify_version and not r['missing'] and not r['script_deleted']))]
                if not verify_version:rows=filter_rows(rows,cfg)
                if not rows:raise error(f'{script_id}：所选音色范围内没有可用外部配音（无音频、缺失、停用或需复核）。')
                strategy=cfg.get('external_voice_strategy','均衡轮换')
                with self.library._connect() as db:
                    db.execute('BEGIN IMMEDIATE');s=self._script(db,script_id)
                    if cfg.get('product_spec_revision') and not verify_version:
                        from product_catalog import check_script
                        check_script(dict(s),cfg)
                    if not s['enabled'] and not verify_version:raise error('文案已停用。')
                    if verify_version:selected=next(r for r in rows if r['id']==verify_version)
                    elif strategy=='指定':
                        selected=next((r for r in rows if r['id']==s['designated_voice']),None)
                        if not selected:raise error(f'{script_id}：指定配音不存在或不可用。')
                    elif strategy=='随机':selected=rng.choice(rows)
                    elif strategy=='均衡轮换':
                        selected=min(rows,key=lambda r:(r['usage_count']+r['reserved_count'],r['last_used'] or '',r['created'],r['id']))
                    else:raise error('未知外部配音策略。')
                    # BEGIN IMMEDIATE is the freeze/delete ordering boundary.
                    live=db.execute('SELECT * FROM external_versions WHERE id=?',(selected['id'],)).fetchone()
                    if live['deleted_op'] or (not verify_version and (not live['enabled'] or live['bound_hash']!=s['text_hash'] or live['review']!='confirmed')):raise error('冻结前配音状态改变，请重新规划。')
                    approval=db.execute('SELECT * FROM external_human_reviews WHERE version_id=? AND resource_id=? AND body_hash=? AND audio_sha=?',(selected['id'],selected['resource_id'],s['text_hash'],selected['sha'])).fetchone()
                    selected['human_review']=dict(approval) if approval else None
                    source=Path(selected['path'])
                    if not source.is_file() or digest(source)!=selected['sha']:raise error(f'{script_id}：音频缺失或内容变化，请重新定位并复核。')
                    key=hashlib.sha256(f'{selected["sha"]}:{selected["metadata"]["stream"]}:{REVISION}'.encode()).hexdigest()
                    dest=self.root/'frozen'/key/'audio.wav';dest.parent.mkdir(parents=True,exist_ok=True)
                    integrity=dest.parent/'integrity.json'
                    if dest.is_file():
                        expected=json.loads(integrity.read_text(encoding='utf-8')) if integrity.is_file() else {}
                        if digest(dest)!=expected.get('sha256'):raise error('已有冻结兼容副本损坏，保留现场，请检查资源。')
                    else:
                        original=dest.parent/('source'+source.suffix.lower());shutil.copy2(source,original)
                        if digest(original)!=selected['sha']:raise error('冻结复制不完整。')
                        part=dest.with_name('audio.part.wav')
                        result=subprocess.run([ffmpeg,'-v','error','-xerror','-y','-i',str(original),'-map',f'0:{selected["metadata"]["stream"]}','-vn','-c:a','pcm_s16le',str(part)],capture_output=True,text=True,timeout=180)
                        if result.returncode:raise error('兼容转换失败：'+result.stderr[-500:])
                        with wave.open(str(part)) as audio:
                            if audio.getnframes()<=0:raise error('音频为空。')
                        os.replace(part,dest)
                        integrity.write_text(json.dumps(dict(sha256=digest(dest),revision=REVISION)),encoding='utf-8')
                    with wave.open(str(dest)) as audio:duration=audio.getnframes()/audio.getframerate();sample_rate=audio.getframerate();channels=audio.getnchannels()
                    actual=digest(dest);ref=ident()
                    db.execute('INSERT INTO external_refs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',(ref,preparation,None,selected['id'],selected['resource_id'],s['script_uid'],s['body_version'],s['text_hash'],str(dest),actual,duration,'prepared',stamp()))
                    db.execute('UPDATE external_versions SET reserved_count=reserved_count+1 WHERE id=?',(selected['id'],))
                emit('external_voice_frozen',script_id=script_id,version_id=selected['id'],resource_id=selected['resource_id'],reference_id=ref,source=SOURCE,strategy=strategy,duration=duration,sha256=actual)
                return dict(source=SOURCE,script_uid=s['script_uid'],body_version=s['body_version'],body_hash=s['text_hash'],external_voice_name=voice_name(selected),external_version_id=selected['id'],external_resource_id=selected['resource_id'],external_ref_id=ref,human_review=selected.get('human_review'),voice_profile='external:'+selected['id'],voice_profile_version=1,
                    voice=dict(voice_cache_key=key,voice_raw=str(dest),voice_processed=str(dest),temporary=False,cache_state='VALID',voice_duration=duration,effective_voice_duration=duration,effective_voice_start=0.,effective_voice_end=duration,sample_rate=sample_rate,channels=channels,sha256=actual,source=SOURCE,processing_revision=REVISION,model_versions={},performance={},tts_parameters={}))
        except Exception as exc:
            if selected:
                exc.external_version_id=selected['id'];exc.external_resource_id=selected['resource_id'];exc.external_body_hash=s['text_hash']
            raise

    def record_acoustic_failure(self, vid, resource_id, body_hash, message):
        """Persist failed content review only for the unchanged bound resource."""
        status='mismatch' if any(token in str(message) for token in ('内容不一致','价格/数量','否定/价格')) else 'review'
        with self.locked(),self.library._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM external_versions WHERE id=? AND resource_id=? AND deleted_op IS NULL',(vid,resource_id)).fetchone()
            if not row:raise error('声学复核期间配音版本已改变，未写入旧结果。')
            current=self._script(db,row['uid'])
            if current['text_hash']!=body_hash:raise error('声学复核期间正文已改变，未写入旧结果。')
            resource=db.execute('SELECT sha FROM external_resources WHERE id=?',(resource_id,)).fetchone()
            if resource and db.execute('SELECT 1 FROM external_human_reviews WHERE version_id=? AND resource_id=? AND body_hash=? AND audio_sha=?',(vid,resource_id,body_hash,resource['sha'])).fetchone():
                db.execute('INSERT INTO external_history VALUES(?,?,?,?,?)',(ident(),vid,resource_id,json.dumps(dict(action='machine_warning_after_human_confirmation',error=str(message)),ensure_ascii=False),stamp()))
                emit('external_voice_machine_warning',version_id=vid,error=str(message),human_priority=True)
                return 'confirmed'
            if row['review']!=status:
                db.execute('UPDATE external_versions SET review=? WHERE id=?',(status,vid))
                db.execute('INSERT INTO external_history VALUES(?,?,?,?,?)',(ident(),vid,resource_id,json.dumps(dict(action='acoustic_review_failed',status=status,error=str(message)),ensure_ascii=False),stamp()))
        emit('external_voice_review_failed',version_id=vid,status=status,error=str(message))
        return status

    def recheck(self, vid, ffmpeg):
        from voice_pipeline import normalize_speech
        from subtitle_alignment import alignment
        row=next((r for r in self.versions() if r['id']==vid),None)
        if not row:raise error('版本不存在。')
        preparation='review:'+ident();self.start_preparation(preparation)
        try:
            frozen=self.choose_and_freeze(row['script_id'],{},ffmpeg,preparation=preparation,verify_version=vid)
            s=self.library.get(row['script_id']);v=frozen['voice']
            try:result=alignment(v['voice_processed'],normalize_speech(s['raw_text']),0,v['voice_duration'],verify_content=not bool(frozen.get('human_review')),raw_text=s['raw_text'])
            except ValueError as exc:
                self.record_acoustic_failure(vid,row['resource_id'],frozen['body_hash'],exc)
                raise
            with self.library._connect() as db:
                db.execute('BEGIN IMMEDIATE');current=self._script(db,row['script_id'])
                if current['text_hash']!=frozen['body_hash']:raise error('复核期间正文变化，请重新复核。')
                changed=db.execute("UPDATE external_versions SET review='confirmed',bound_version=?,bound_hash=? WHERE id=? AND resource_id=? AND deleted_op IS NULL",(current['body_version'],current['text_hash'],vid,row['resource_id']))
                if changed.rowcount!=1:raise error('复核期间配音版本变化或被回收，请刷新。')
            return dict(version_id=vid,status='confirmed',content_check=(dict(decision='human_authoritative',approval=frozen['human_review']) if frozen.get('human_review') else result.get('content_check')))
        finally:self.abandon_preparation(preparation)

    def finalize(self, batch, preparation):
        refs=set()
        with self.locked(),self.library._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in batch['items']:
                rid=item.get('external_ref_id')
                if not rid or item.get('status') not in ('planned','rendering','completed'):continue
                r=db.execute('SELECT * FROM external_refs WHERE id=?',(rid,)).fetchone()
                if not r:raise error('冻结配音引用丢失。')
                refs.add(rid);vid=item.get('video_id') or item['id']
                db.execute("UPDATE external_refs SET video_id=?,state='frozen' WHERE id=?",(vid,rid))
                db.execute('INSERT OR IGNORE INTO external_uses VALUES(?,?,?,?,?)',(vid,batch['id'],r['version_id'],'reserved',stamp()))
            for r in db.execute("SELECT * FROM external_refs WHERE preparation=? AND state='prepared'",(preparation,)).fetchall():
                if r['id'] not in refs:
                    db.execute('UPDATE external_versions SET reserved_count=MAX(0,reserved_count-1) WHERE id=?',(r['version_id'],))
                    db.execute('DELETE FROM external_refs WHERE id=?',(r['id'],))
            db.execute("UPDATE external_preparations SET state='finalized' WHERE id=?",(preparation,))

    def abandon_preparation(self, preparation):self.finalize(dict(id='',items=[]),preparation)

    def release_prepared_reference(self, reference_id):
        with self.locked(),self.library._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM external_refs WHERE id=? AND state='prepared'",(reference_id,)).fetchone()
            if row:
                db.execute('UPDATE external_versions SET reserved_count=MAX(0,reserved_count-1) WHERE id=?',(row['version_id'],))
                db.execute('DELETE FROM external_refs WHERE id=?',(reference_id,))

    def preflight(self, script_ids, strategy='均衡轮换', cfg=None):
        issues=[]
        for sid in dict.fromkeys(script_ids):
            s=self.library.get(sid);rows=self.versions(sid);valid=self.candidates(sid,cfg)
            if not valid:issues.append(f'{sid}：所选音色范围内无有效音频；绑定{len(rows)}，需复核{sum(r["review"]!="confirmed" for r in rows)}，缺失{sum(r["missing"] for r in rows)}')
            elif strategy=='指定' and s['designated_voice'] not in [r['id'] for r in valid]:issues.append(f'{sid}：指定版本缺失或不可用')
        return issues

    def reserve(self, batch):
        with self.library._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in batch.get('items',[]):
                if not item.get('external_ref_id') or item.get('status') not in ('planned','rendering','VOICE_RETRYING'):continue
                vid=item.get('video_id') or item['id']
                if db.execute('SELECT 1 FROM external_uses WHERE video_id=?',(vid,)).fetchone():continue
                r=db.execute('SELECT * FROM external_refs WHERE id=? AND video_id=?',(item['external_ref_id'],vid)).fetchone()
                if not r:raise error('任务缺少冻结的外部配音引用。')
                db.execute('INSERT INTO external_uses VALUES(?,?,?,?,?)',(vid,batch['id'],r['version_id'],'reserved',stamp()))
                db.execute('UPDATE external_versions SET reserved_count=reserved_count+1 WHERE id=?',(r['version_id'],))

    def finish(self, item, success):
        if not item.get('external_ref_id'):return
        vid=item.get('video_id') or item['id']
        with self.library._connect() as db:
            db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM external_uses WHERE video_id=?',(vid,)).fetchone()
            if row and row['state']=='success':return
            ref=db.execute('SELECT * FROM external_refs WHERE id=? AND video_id=?',(item['external_ref_id'],vid)).fetchone()
            if not ref:raise error('完成任务的外部配音身份不一致。')
            if row:
                db.execute('UPDATE external_versions SET reserved_count=MAX(0,reserved_count-1) WHERE id=?',(row['version_id'],))
                db.execute('DELETE FROM external_uses WHERE video_id=?',(vid,))
            if success:
                db.execute('INSERT INTO external_uses VALUES(?,?,?,?,?)',(vid,item['id'].rsplit('_',1)[0],ref['version_id'],'success',stamp()))
                db.execute('UPDATE external_versions SET usage_count=usage_count+1,last_used=? WHERE id=?',(stamp(),ref['version_id']))
                db.execute("UPDATE external_refs SET state='history' WHERE id=?",(ref['id'],))
        emit('external_voice_count',job_id=vid,version_id=ref['version_id'],success=success)

def validate_frozen(item):
    if item.get('voice_source')!=SOURCE:return
    v=item.get('voice') or {};p=Path(v.get('voice_processed',''))
    if not p.is_file() or digest(p)!=v.get('sha256'):raise error('外部配音冻结文件缺失或内容不符；不能替换声音重试。')

def process_alive(pid):
    if pid==os.getpid():return True
    if os.name=='nt':
        import ctypes
        k=ctypes.windll.kernel32;k.OpenProcess.restype=ctypes.c_void_p
        handle=k.OpenProcess(0x1000,False,int(pid))
        if not handle:return False
        code=ctypes.c_ulong()
        try:return bool(k.GetExitCodeProcess(ctypes.c_void_p(handle),ctypes.byref(code))) and code.value==259
        finally:k.CloseHandle(ctypes.c_void_p(handle))
    try:os.kill(int(pid),0);return True
    except ProcessLookupError:return False
    except PermissionError:return True

def prepare_packages(scripts,cfg,ffmpeg,log):
    from script_library import ScriptLibrary
    from subtitle_alignment import alignment
    library=ScriptLibrary(Path(cfg.get('script_library_path') or Path(__file__).parent/'data'/'script_library.sqlite3'))
    preparation=cfg.get('voice_job_temp_dir') or ident();rng=random.Random(str(cfg.get('seed',''))+':external')
    tolerant=cfg.get('batch_failure_policy')=='skip_recompose_v1'
    limit=min(8,max(1,int(cfg.get('external_voice_retry_limit',3)))) if tolerant else 1
    library.external.start_preparation(preparation);results=[]
    try:
        for script in scripts:
            if not script.get('narration_enabled'):
                results.append(dict(script));continue
            excluded=[];errors=[];success=False
            for attempt in range(limit):
                p=dict(script);started=time.monotonic()
                try:
                    if not p.get('script_library_id'):raise error('外部配音需要使用已绑定的文案库正文。')
                    p.update(library.external.choose_and_freeze(p['script_library_id'],dict(cfg,external_voice_excluded_versions=excluded),ffmpeg,preparation=preparation,rng=rng));p['voice_source']=SOURCE
                    if p.get('body_hash')!=hashlib.sha256(p['raw_text'].encode('utf-8')).hexdigest():raise error('准备期间正文改变，请重新规划。')
                    human=p.get('human_review')
                    if cfg.get('voice_speed_revision'):
                        from voice_speed import decision,speaker_key,apply as apply_voice_speed
                        p['voice_speed_snapshot']=decision(cfg,speaker_key(p))
                        p['_tempo_preparing']=True
                        tempo_root=Path(cfg['voice_job_temp_dir'])/'voice_rate' if cfg.get('voice_job_temp_dir') else library.external.root/'tempo'
                        p['voice']=apply_voice_speed(p['voice'],p['voice_speed_snapshot'],ffmpeg,tempo_root)
                        p.pop('_tempo_preparing',None)
                    aligned=alignment(p['voice']['voice_processed'],p['speech_text'],0,p['voice']['voice_duration'],verify_content=not bool(human),raw_text=p['raw_text'])
                    if human:
                        aligned=dict(aligned,content_check=dict(decision='human_authoritative',approval=human))
                    p['voice']['external_alignment']=aligned;p['voice']['alignment_identity']=aligned['cache_key']
                    p['voice']['content_review']=aligned.get('content_check')
                    p['preparation_attempt_errors']=errors
                    results.append(p);success=True
                    log(f'{p["script_id"]} 外部配音复用：{p["external_version_id"]}，{p["voice"]["voice_duration"]:.3f}秒，准备{time.monotonic()-started:.2f}秒')
                    break
                except Exception as exc:
                    vid=p.get('external_version_id') or getattr(exc,'external_version_id',None)
                    if p.get('external_ref_id'):library.external.release_prepared_reference(p['external_ref_id'])
                    resource_id=p.get('external_resource_id') or getattr(exc,'external_resource_id',None)
                    body_hash=p.get('body_hash') or getattr(exc,'external_body_hash',None)
                    if vid and resource_id and body_hash and not p.get('_tempo_preparing'):
                        try:library.external.record_acoustic_failure(vid,resource_id,body_hash,exc)
                        except Exception as stale:log('复核结果未写入已变化的版本：'+str(stale))
                    errors.append(dict(attempt=attempt+1,version_id=vid,error=str(exc)))
                    log(f'{script["script_id"]} 第{attempt+1}次配音准备失败，记录后继续：{exc}')
                    if not tolerant:raise
                    if vid:excluded.append(vid)
                    if not vid or cfg.get('external_voice_strategy')=='指定':break
            if not success:
                p=dict(script,voice_error=errors[-1]['error'],voice_failure_stage='外部配音准备',preparation_attempt_errors=errors)
                results.append(p)
        return results
    except Exception:
        library.external.abandon_preparation(preparation);raise
