"""Exact G-family identities and human-reviewed coverage in the existing Store DB."""
from pathlib import PurePosixPath, Path
from itertools import combinations
import json
import re

REVISION = 'grouped_coverage_v1'
_FILE = re.compile(r'(?P<source>G[0-9]{3,})(?P<combo>[A-Z])(?P<member>[1-9][0-9]*)?')


class GroupedError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}：{message}')


def classify(relative):
    path = PurePosixPath(str(relative).replace('\\', '/'))
    if path.is_absolute() or len(path.parts) != 2 or '..' in path.parts:
        raise GroupedError('INVALID_G_NAME', f'需要 G编号/同编号组合文件：{relative}')
    match = _FILE.fullmatch(path.stem)
    if not match or match['source'] != path.parts[0] or path.suffix.lower() != '.mp4':
        raise GroupedError('INVALID_G_NAME', f'目录与完整文件名必须精确对应：{relative}')
    source, combo, member = match['source'], match['combo'], match['member']
    return dict(group='G', category='G', source_group=source, combo_id=source+combo,
                asset_kind='independent' if member else 'combo',
                member_number=int(member) if member else None,
                can_open=True, mutex_group='file:'+path.as_posix(),
                relative_path=path.as_posix(), timeline_role='随机镜头')


def suggestion(a, b):
    return ('overlap' if a['combo_id'] == b['combo_id']
            and (a['asset_kind'] == 'combo' or b['asset_kind'] == 'combo') else 'clear')


def pair_key(a, b):
    from core import object_hash
    return object_hash(sorted((a['relative_path']+'|'+a['hash'], b['relative_path']+'|'+b['hash'])))


def root_key(root):
    return str(Path(root).resolve()).casefold()


class Coverage:
    def __init__(self, store, root):
        self.store, self.root = store, root_key(root)
        with store.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS grouped_asset_review(
                root TEXT, path TEXT, hash TEXT, safe_cut INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(root,path,hash));
              CREATE TABLE IF NOT EXISTS grouped_coverage(
                root TEXT, pair_key TEXT, payload TEXT NOT NULL, PRIMARY KEY(root,pair_key));
              CREATE TABLE IF NOT EXISTS grouped_opening_review(
                root TEXT,path TEXT,hash TEXT,excluded INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(root,path,hash));
            ''')

    def annotate(self, clips):
        with self.store.connect() as db:
            cuts = {(r[0], r[1]): bool(r[2]) for r in db.execute(
                'SELECT path,hash,safe_cut FROM grouped_asset_review WHERE root=?', (self.root,))}
            opening = {(r[0],r[1]): bool(r[2]) for r in db.execute(
                'SELECT path,hash,excluded FROM grouped_opening_review WHERE root=?',(self.root,))}
        for clip in clips:
            clip['safe_cut_confirmed'] = cuts.get((clip['relative_path'], clip['hash']), False)
            clip['opening_excluded'] = opening.get((clip['relative_path'],clip['hash']),False)
        return clips

    def rows(self, clips):
        with self.store.connect() as db:
            saved = {r[0]: json.loads(r[1]) for r in db.execute(
                'SELECT pair_key,payload FROM grouped_coverage WHERE root=?', (self.root,))}
        return [dict(pair_key=pair_key(a,b), left=a['relative_path'], right=b['relative_path'],
                     left_hash=a['hash'], right_hash=b['hash'],
                     suggestion=suggestion(a,b), state=saved.get(pair_key(a,b),{}).get('state','pending'))
                for a,b in combinations(clips,2)]

    def confirm(self, rows, state=None):
        from core import now
        with self.store.connect() as db:
            for row in rows:
                value = dict(row, state=state or row['suggestion'], confirmed_at=now())
                if value['state'] not in ('overlap','clear','pending'):
                    raise GroupedError('COVERAGE_STATE_INVALID', '关系必须为重复/不重复/待确认')
                db.execute('INSERT OR REPLACE INTO grouped_coverage VALUES(?,?,?)',
                           (self.root,value['pair_key'],json.dumps(value,ensure_ascii=False)))

    def safe_cut(self, clips, enabled):
        with self.store.connect() as db:
            for c in clips:
                db.execute('INSERT OR REPLACE INTO grouped_asset_review VALUES(?,?,?,?)',
                           (self.root,c['relative_path'],c['hash'],int(enabled)))
                c['safe_cut_confirmed'] = bool(enabled)

    def exclude_opening(self,clips,excluded):
        with self.store.connect() as db:
            for clip in clips:
                db.execute('INSERT OR REPLACE INTO grouped_opening_review VALUES(?,?,?,?)',
                           (self.root,clip['relative_path'],clip['hash'],int(excluded)))
                clip['opening_excluded']=bool(excluded)

    def snapshot(self, clips):
        return dict(revision=REVISION, root=self.root, pairs=self.rows(clips),opening_revision='ball_opening_v1',
                    assets=[dict(path=c['relative_path'],hash=c['hash'],
                                 safe_cut=bool(c.get('safe_cut_confirmed')),
                                 opening_excluded=bool(c.get('opening_excluded'))) for c in clips])


def conflict(clips, snapshot, *, require_confirmed=True):
    if snapshot.get('sk_revision'):
        from sk_materials import hard_conflict
        return hard_conflict(clips)
    states = {r['pair_key']: r['state'] for r in snapshot.get('pairs',[])}
    ids, hashes = set(), set()
    for c in clips:
        if c['hash'] in hashes or c['identity_key'] in ids:
            return 'DUPLICATE_FILE', '同一文件/内容重复'
        hashes.add(c['hash']); ids.add(c['identity_key'])
    for a,b in combinations(clips,2):
        state = states.get(pair_key(a,b),'pending')
        if state == 'overlap':
            return 'COVERAGE_CONFLICT', a['relative_path']+' + '+b['relative_path']
        if require_confirmed and state != 'clear':
            return 'COVERAGE_UNCONFIRMED', a['relative_path']+' + '+b['relative_path']
    return None


def validate_sequence(clips, cfg, snapshot=None):
    if cfg.get('material_format') == 'sk_diverse':
        from sk_materials import validate_sequence as validate_sk
        return validate_sk(clips, cfg, snapshot)
    if not clips or len(clips) > int(cfg['max_clips']):
        raise GroupedError('CANDIDATES_INSUFFICIENT','镜头数量为空或超出上限')
    for c in clips:
        expected = classify(c['relative_path'])
        if any(c.get(k) != v for k,v in expected.items() if k != 'timeline_role'):
            raise GroupedError('G_IDENTITY_CHANGED',c['relative_path'])
    # The immutable item snapshot is checked by the grouped validator.
    if snapshot is not None and (err:=conflict(clips,snapshot,
            require_confirmed=bool(cfg.get('coverage_dedup_enabled',True)))):
        raise GroupedError(*err)


def package_paths(cfg, clips):
    listed = cfg.get('package_count_clips') or []
    if isinstance(listed,str):
        listed = [v.strip() for v in listed.splitlines() if v.strip()]
    by_path={c['relative_path'].casefold():c for c in clips}
    result=[]
    for value in listed:
        path=PurePosixPath(str(value).replace('\\','/'))
        classify(path.as_posix())
        c=by_path.get(path.as_posix().casefold())
        if c is None:
            if (Path(cfg['material_root'])/path.as_posix()).exists():
                raise GroupedError('PACKAGE_FILE_INVALID','文件存在但未通过本次有效扫描，请检查解码异常或重新扫描：'+path.as_posix())
            raise GroupedError('PACKAGE_FILE_MISSING',path.as_posix())
        if not c.get('enabled') or not c.get('reviewed'):
            raise GroupedError('PACKAGE_FILE_UNAVAILABLE',path.as_posix())
        if c not in result:result.append(c)
    return result


def scan_materials(cfg,store,tool,log):
    from core import VIDEO_EXTS,AssemblyError,run_capture
    root=Path(cfg['material_root'])
    review=Coverage(store,root)
    with store.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS grouped_decode_cache(hash TEXT PRIMARY KEY)')
        decoded={r[0] for r in db.execute('SELECT hash FROM grouped_decode_cache')}
    clips,warnings=[],[]
    for p in sorted(root.rglob('*')):
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:continue
        relative=p.relative_to(root).as_posix()
        try:
            identity=classify(relative)
            meta,hit=store.inspect(p,tool)
            if not meta.get('has_video'):
                raise GroupedError('MEDIA_DECODE_FAILED','没有视频流')
            if meta['hash'] not in decoded:
                from core import resolve_tool
                run_capture([resolve_tool('ffmpeg',cfg['ffmpeg']),'-v','error','-xerror',
                    '-nostdin','-threads','2','-i',str(p),'-map','0:v:0','-an','-f','null','-'],
                    max(120,float(meta['duration'])*5))
                with store.connect() as db:
                    db.execute('INSERT OR IGNORE INTO grouped_decode_cache VALUES(?)',(meta['hash'],))
                decoded.add(meta['hash'])
            clips.append(dict(meta,**identity,display=relative,**store.flags(meta['hash']),
                              identity_key='sha256:'+meta['hash'],source_id='',project_link={}))
            log(f"G | {relative} | {meta['duration']:.3f}秒 | {'缓存' if hit else '已读取'}")
        except Exception as exc:
            warnings.append(f'待确认 {relative}：{exc}')
    review.annotate(clips)
    pending=sum(r['state']=='pending' for r in review.rows(clips))
    log(f'G组扫描：{len(clips)}条有效文件，{pending}对关系待确认；异常{len(warnings)}条。')
    return clips,warnings
