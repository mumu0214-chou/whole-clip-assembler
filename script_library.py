# -*- coding: utf-8 -*-
"""Persistent manual script library and idempotent plan-to-success accounting."""
from __future__ import annotations

from datetime import datetime
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
import random
import sqlite3

from voice_pipeline import VoiceError, split_script_text


def normalize_text(value: str) -> str:
    return '\n'.join(line.rstrip() for line in str(value).replace('\r\n', '\n').replace('\r', '\n').strip().split('\n')).strip()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


class ScriptLibrary:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS scripts (
                    script_id TEXT PRIMARY KEY, raw_text TEXT NOT NULL, text_hash TEXT NOT NULL UNIQUE,
                    enabled INTEGER NOT NULL DEFAULT 1, usage_count INTEGER NOT NULL DEFAULT 0,
                    reserved_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, last_used_at TEXT, optional_note TEXT NOT NULL DEFAULT '',
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS script_uses (
                    video_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, script_id TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('reserved','success')),
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS script_uses_batch ON script_uses(batch_id,state);
                CREATE TABLE IF NOT EXISTS script_rotation (
                    profile_id TEXT PRIMARY KEY, last_script_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
            ''')
            columns={row[1] for row in db.execute('PRAGMA table_info(scripts)')}
            if 'profile_id' not in columns:
                db.execute("ALTER TABLE scripts ADD COLUMN profile_id TEXT NOT NULL DEFAULT 'fresh_beef_standard'")
            if 'spec_id' not in columns:
                db.execute("ALTER TABLE scripts ADD COLUMN spec_id TEXT NOT NULL DEFAULT 'default'")
            db.execute('CREATE INDEX IF NOT EXISTS scripts_scope ON scripts(profile_id,spec_id,enabled)')
        from product_catalog import Catalog
        self.catalog=Catalog(self.path)
        from external_voice import migrate, ExternalVoices
        with self._connect() as db:
            migrate(db)
        self.external = ExternalVoices(self)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA busy_timeout=10000')
        try:
            with db:
                yield db
        finally:
            db.close()

    def add(self, raw_text: str, note: str = '', *, profile_id='fresh_beef_standard',spec_id='default') -> str:
        from editing_profiles import NAMES
        if profile_id not in NAMES:
            raise VoiceError('未知文案归属方案')
        self.catalog.get(profile_id,spec_id)
        value = normalize_text(raw_text)
        if not value:
            raise VoiceError('文案不能为空。')
        digest = sha256(value.encode('utf-8')).hexdigest()
        with self._connect() as db:
            existing = db.execute('SELECT script_id,deleted_at,profile_id,spec_id FROM scripts WHERE text_hash=?', (digest,)).fetchone()
            if existing:
                if existing['spec_id']!=spec_id and existing['profile_id']==profile_id:
                    raise VoiceError('相同文案已归属其他规格，请通过“移到规格”调整归属，配音和历史会保留。')
                if existing['profile_id'] != profile_id:
                    raise VoiceError(f'相同文案已归属{NAMES.get(existing["profile_id"], "其他品类")}，请先核对归属，不能直接加入当前品类。')
                if existing['deleted_at'] is None:
                    raise VoiceError('该文案已存在。')
                raise VoiceError('该文案已在回收站，请通过回收站恢复原记录。')
            next_id = db.execute("SELECT COALESCE(MAX(CAST(SUBSTR(script_id,2) AS INTEGER)),0)+1 FROM scripts").fetchone()[0]
            script_id = f'L{next_id:06d}'
            stamp = _now()
            db.execute('INSERT INTO scripts(script_id,raw_text,text_hash,created_at,updated_at,optional_note,profile_id,spec_id) VALUES(?,?,?,?,?,?,?,?)',
                       (script_id, value, digest, stamp, stamp, note.strip(),profile_id,spec_id))
        return script_id

    def import_text(self, text: str, *, profile_id='fresh_beef_standard',spec_id='default') -> tuple[list[str], list[str]]:
        added, duplicates = [], []
        for block in split_script_text(text):
            try:
                added.append(self.add(block,profile_id=profile_id,spec_id=spec_id))
            except VoiceError as exc:
                if str(exc) != '该文案已存在。':
                    raise
                duplicates.append(block[:28])
        return added, duplicates

    def list(self, status='全部', sort='使用次数', *, profile_id=None, query='',spec_id=None) -> list[dict]:
        where = "WHERE deleted_at IS NULL"
        if status == '启用': where += ' AND enabled=1'
        if status == '停用': where += ' AND enabled=0'
        params=[]
        if spec_id is not None:
            where+=' AND spec_id=?';params.append(spec_id)
        if profile_id is not None:
            where+=' AND profile_id=?';params.append(profile_id)
        if str(query).strip():
            where += ' AND (instr(lower(raw_text),lower(?))>0 OR instr(lower(script_id),lower(?))>0)'
            params.extend([str(query).strip()] * 2)
        numeric_id = 'CAST(SUBSTR(script_id,2) AS INTEGER)'
        order = {'ID升序': numeric_id + ' ASC, script_id ASC',
                 'ID降序': numeric_id + ' DESC, script_id DESC',
                 '使用次数': 'usage_count ASC, script_id ASC',
                 '最近使用': 'COALESCE(last_used_at,\'\') DESC, script_id ASC',
                 '状态': 'enabled DESC, script_id ASC'}.get(sort, 'script_id ASC')
        with self._connect() as db:
            return [dict(row) for row in db.execute(f'SELECT * FROM scripts {where} ORDER BY {order}',params)]

    def get(self, script_id: str) -> dict:
        with self._connect() as db:
            row = db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL', (script_id,)).fetchone()
        if row is None:
            raise VoiceError(f'文案不存在：{script_id}')
        return dict(row)

    @staticmethod
    def _check_profile(row, profile_id,spec_id=None):
        if profile_id is not None and row['profile_id'] != profile_id:
            raise VoiceError('这篇文案的品类已改变，请刷新当前品类列表后再操作。')
        if spec_id is not None and row.get('spec_id','default')!=spec_id:
            raise VoiceError('这篇文案的规格已改变，请刷新当前规格列表后再操作。')

    def edit(self, script_id: str, raw_text: str, reset_usage=False, note: str | None = None, *, profile_id=None):
        value = normalize_text(raw_text)
        if not value:
            raise VoiceError('文案不能为空。')
        digest = sha256(value.encode('utf-8')).hexdigest()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL', (script_id,)).fetchone()
            if row is None: raise VoiceError(f'文案不存在：{script_id}')
            self._check_profile(row, profile_id)
            if db.execute('SELECT 1 FROM scripts WHERE text_hash=? AND script_id!=?', (digest, script_id)).fetchone():
                raise VoiceError('该文案已存在。')
            db.execute('''UPDATE scripts SET raw_text=?,text_hash=?,updated_at=?,optional_note=?,
                          usage_count=CASE WHEN ? THEN 0 ELSE usage_count END,
                          last_used_at=CASE WHEN ? THEN NULL ELSE last_used_at END WHERE script_id=?''',
                       (value, digest, _now(), row['optional_note'] if note is None else note.strip(),
                        int(reset_usage), int(reset_usage), script_id))

    def set_enabled(self, script_id: str, enabled: bool, *, profile_id=None):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL', (script_id,)).fetchone()
            if row is None:
                raise VoiceError(f'文案不存在：{script_id}')
            self._check_profile(row, profile_id)
            db.execute('UPDATE scripts SET enabled=?,updated_at=? WHERE script_id=?',
                       (int(enabled), _now(), script_id))

    def delete(self, script_id: str, *, profile_id=None):
        self._check_profile(self.get(script_id), profile_id)
        return self.external.recycle([script_id])

    def reset_usage(self, script_id: str, *, profile_id=None):
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL', (script_id,)).fetchone()
            if row is None: raise VoiceError(f'文案不存在：{script_id}')
            self._check_profile(row, profile_id)
            db.execute('UPDATE scripts SET usage_count=0,last_used_at=NULL,updated_at=? WHERE script_id=? AND deleted_at IS NULL',
                       (_now(), script_id))

    def set_profile(self, script_id, profile_id, *, expected_profile=None):
        from editing_profiles import NAMES
        if profile_id not in NAMES:raise VoiceError('未知文案归属方案')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL',(script_id,)).fetchone()
            if row is None:
                raise VoiceError('文案不存在')
            self._check_profile(row, expected_profile)
            db.execute("UPDATE scripts SET profile_id=?,spec_id='default',updated_at=? WHERE script_id=?",(profile_id,_now(),script_id))

    def move_spec(self,script_ids,profile_id,target_spec,*,expected_spec=None):
        self.catalog.get(profile_id,target_spec);ids=list(dict.fromkeys(script_ids))
        if not ids:raise VoiceError('请先选择文案')
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for sid in ids:
                row=db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL',(sid,)).fetchone()
                if row is None:raise VoiceError('文案不存在：'+sid)
                self._check_profile(dict(row),profile_id,expected_spec)
                if row['reserved_count']:raise VoiceError('文案正在被待剪辑任务预留，请完成或取消任务后再移动规格')
            for sid in ids:db.execute('UPDATE scripts SET spec_id=?,updated_at=? WHERE script_id=?',(target_spec,_now(),sid))
        return len(ids)

    def select_for_profile(self, count: int, cfg: dict) -> list[dict]:
        """New plans must draw scripts from the same category as their editing rules."""
        from editing_profiles import NAMES, FRESH, ball_profile, GROUPED_FORMAT
        from sk_materials import active as sk_active, FORMAT as SK_FORMAT
        key = cfg.get('editing_profile_id', FRESH)
        if key not in NAMES or cfg.get('script_library_profile', key) != key:
            raise VoiceError('文案品类与剪辑方案不一致，请重新选择剪辑方案后规划。')
        expected_format = (SK_FORMAT if sk_active(cfg) else
                           GROUPED_FORMAT if ball_profile(key) else 'five_stage')
        if cfg.get('material_format', expected_format) != expected_format:
            raise VoiceError('素材规则与文案品类不一致，请重新选择剪辑方案后规划。')
        try:
            return self.select(count, strategy=cfg.get('script_library_strategy', '低使用次数优先'),
                               allow_repeat=cfg.get('allow_script_repeat', False),
                               seed=cfg.get('seed', ''), profile_id=key,
                               spec_id=cfg.get('product_spec_id','default'),
                               external_only=cfg.get('voice_source')=='external_files' and cfg.get('external_voice_only_scripts',False) and (not cfg.get('video_mode_schedule') or any(e['video_mode'] in ('配音+BGM','配音+字幕+BGM') for e in cfg['video_mode_schedule'])), external_cfg=cfg)
        except VoiceError as exc:
            label=self.catalog.categories()[key]+' / '+self.catalog.get(key,cfg.get('product_spec_id','default'))['name']
            raise VoiceError(f'{label}：{exc}') from exc

    def bulk_action(self, script_ids, action, *, profile_id, target_profile=None):
        """Validate the complete cross-page selection before one atomic mutation."""
        from editing_profiles import NAMES
        ids = list(dict.fromkeys(script_ids))
        if not ids:
            raise VoiceError('请先选择文案。')
        if profile_id not in NAMES or action not in ('enable','disable','delete','reset','move'):
            raise VoiceError('未知批量操作或品类。')
        if action=='move' and target_profile not in NAMES:
            raise VoiceError('未知目标品类。')
        if action=='delete':
            for sid in ids:self._check_profile(self.get(sid),profile_id)
            self.external.recycle(ids)
            return len(ids)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for sid in ids:
                row = db.execute('SELECT * FROM scripts WHERE script_id=? AND deleted_at IS NULL',(sid,)).fetchone()
                if row is None:
                    raise VoiceError(f'文案不存在：{sid}')
                self._check_profile(row, profile_id)
            stamp = _now()
            for sid in ids:
                if action in ('enable','disable'):
                    db.execute('UPDATE scripts SET enabled=?,updated_at=? WHERE script_id=?',(int(action=='enable'),stamp,sid))
                elif action=='reset':
                    db.execute('UPDATE scripts SET usage_count=0,last_used_at=NULL,updated_at=? WHERE script_id=?',(stamp,sid))
                else:
                    db.execute("UPDATE scripts SET profile_id=?,spec_id='default',updated_at=? WHERE script_id=?",(target_profile,stamp,sid))
        return len(ids)

    def select(self, count: int, *, strategy='低使用次数优先', allow_repeat=False, seed='', profile_id='fresh_beef_standard', external_only=False, external_cfg=None,spec_id='default') -> list[dict]:
        self.catalog.get(profile_id,spec_id)
        candidates = self.list('启用',profile_id=profile_id,spec_id=spec_id)
        rotation_key=profile_id if spec_id=='default' else profile_id+':spec:'+spec_id
        if external_only:candidates=[r for r in candidates if self.external.candidates(r['script_id'],external_cfg)]
        if not candidates and count:
            raise VoiceError('文案库没有已启用的可用文案。')
        if len(candidates) < count and not allow_repeat:
            raise VoiceError(f'本批需要{count}篇，可用不重复文案{len(candidates)}篇，还缺{count-len(candidates)}篇。')
        rng = random.Random(str(seed) + ':script-library')
        result = []
        remaining = list(candidates)
        cursor = ''
        if strategy == '顺序':
            with self._connect() as db:
                row = db.execute('SELECT last_script_id FROM script_rotation WHERE profile_id=?', (rotation_key,)).fetchone()
                if row:
                    cursor = row['last_script_id']
                else:
                    # Seed existing libraries from their last real reservation;
                    # opening/selecting a library never advances the rotation.
                    row = db.execute('''SELECT u.script_id FROM script_uses u JOIN scripts s ON s.script_id=u.script_id
                                        WHERE s.profile_id=? AND s.spec_id=? ORDER BY u.created_at DESC,u.rowid DESC LIMIT 1''', (profile_id,spec_id)).fetchone()
                    if row: cursor = row['script_id']
        for _ in range(count):
            # Repetition is allowed only when requested. A balanced batch can
            # then let new/underused scripts catch up instead of forcing a
            # high-use script merely to finish a round of the entire library.
            pool = candidates if allow_repeat and strategy == '低使用次数优先' else remaining or candidates
            if strategy == '低使用次数优先':
                low = min(row['usage_count'] + row['reserved_count'] +
                          sum(x['script_id'] == row['script_id'] for x in result) for row in pool)
                choices = [row for row in pool if row['usage_count'] + row['reserved_count'] +
                           sum(x['script_id'] == row['script_id'] for x in result) == low]
                chosen = rng.choice(choices)
            elif strategy == '顺序':
                following = [row for row in pool if row['script_id'] > cursor]
                chosen = min(following or pool, key=lambda row: row['script_id'])
                cursor = chosen['script_id']
            elif strategy == '随机':
                chosen = rng.choice(pool)
            else:
                raise VoiceError(f'未知文案库选择策略：{strategy}')
            result.append(chosen)
            if chosen in remaining: remaining.remove(chosen)
        return result

    def reserve_batch(self, batch: dict):
        if batch.get('config',{}).get('product_spec_revision'):
            from product_catalog import check_script,check_package
            for item in batch.get('items',[]):
                if item.get('script_library_id') and item.get('status') in ('planned','rendering','VOICE_RETRYING'):
                    check_script(self.get(item['script_library_id']),batch['config'])
                    check_package(item.get('script_package'),batch['config'])
        self.external.reserve(batch)
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for item in batch.get('items', []):
                script_id = item.get('script_library_id')
                if not script_id or item.get('status') not in ('planned', 'rendering', 'VOICE_RETRYING'): continue
                video_id = item.get('video_id') or item['id']
                existing = db.execute('SELECT script_id FROM script_uses WHERE video_id=?', (video_id,)).fetchone()
                if existing:
                    if existing['script_id'] != script_id:
                        raise VoiceError(f'成片{video_id}的文案预留身份冲突。')
                    continue
                if not db.execute('SELECT 1 FROM scripts WHERE script_id=?', (script_id,)).fetchone():
                    raise VoiceError(f'预留文案已不存在：{script_id}')
                stamp = _now()
                db.execute('INSERT INTO script_uses VALUES(?,?,?,?,?,?)',
                           (video_id, batch['id'], script_id, 'reserved', stamp, stamp))
                db.execute('UPDATE scripts SET reserved_count=reserved_count+1 WHERE script_id=?', (script_id,))
                profile = item.get('script_profile_id') or batch.get('config', {}).get('editing_profile_id')
                if not profile:
                    profile = db.execute('SELECT profile_id FROM scripts WHERE script_id=?', (script_id,)).fetchone()['profile_id']
                spec=item.get('script_spec_id') or batch.get('config',{}).get('product_spec_id','default')
                rotation_key=profile if spec=='default' else profile+':spec:'+spec
                db.execute('''INSERT INTO script_rotation VALUES(?,?,?)
                              ON CONFLICT(profile_id) DO UPDATE SET last_script_id=excluded.last_script_id,updated_at=excluded.updated_at''',
                           (rotation_key, script_id, stamp))

    def finish_item(self, item: dict, success: bool):
        self.external.finish(item,success)
        script_id = item.get('script_library_id')
        if not script_id: return
        video_id = item.get('video_id') or item['id']
        with self._connect() as db:
            row = db.execute('SELECT * FROM script_uses WHERE video_id=?', (video_id,)).fetchone()
            if row and row['script_id'] != script_id:
                raise VoiceError(f'成片{video_id}的文案提交身份冲突。')
            if row and row['state'] == 'success': return
            if success:
                if row:
                    db.execute('UPDATE scripts SET reserved_count=MAX(0,reserved_count-1) WHERE script_id=?', (script_id,))
                    db.execute("UPDATE script_uses SET state='success',updated_at=? WHERE video_id=?", (_now(), video_id))
                else:
                    stamp = _now()
                    db.execute('INSERT INTO script_uses VALUES(?,?,?,?,?,?)',
                               (video_id, item.get('id', '').rsplit('_', 1)[0], script_id, 'success', stamp, stamp))
                db.execute('UPDATE scripts SET usage_count=usage_count+1,last_used_at=?,updated_at=? WHERE script_id=?',
                           (_now(), _now(), script_id))
            elif row and row['state'] == 'reserved':
                db.execute('DELETE FROM script_uses WHERE video_id=?', (video_id,))
                db.execute('UPDATE scripts SET reserved_count=MAX(0,reserved_count-1) WHERE script_id=?', (script_id,))

    def abandon_batch(self, batch: dict):
        for item in batch.get('items', []):
            if item.get('status') != 'completed': self.finish_item(item, False)

    def reconcile_batch(self, batch: dict):
        if batch.get('abandoned_at') or batch.get('reservation_state')=='released':
            self.abandon_batch(batch)
            return
        self.reserve_batch(batch)
        for item in batch.get('items', []):
            if item.get('status') == 'completed': self.finish_item(item, True)
            elif item.get('status') in ('failed', 'VOICE_FAILED'):
                self.finish_item(item, False)
