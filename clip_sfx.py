"""Manual beef-ball clip/effect bindings and frozen, sample-exact overlays.

Effects follow the final video timeline. Manual bindings can also label scenes
for batch-level picture diversity; audio duration never controls video timing.
"""
from pathlib import Path
import hashlib,json,math,os,shutil,sqlite3,subprocess,uuid,wave
import time
from contextlib import contextmanager
from editing_profiles import ball_profile

REVISION='beef_ball_clip_sfx_v1'
RATE=48000

class SFXError(ValueError):pass

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda:source.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def run(args,timeout=120,cancel=None):
    with subprocess.Popen([str(a) for a in args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as process:
        deadline=time.monotonic()+timeout
        while True:
            if cancel is not None and cancel.is_set():
                process.kill();process.communicate();raise SFXError('用户已取消本条音效混音。')
            try:stdout,stderr=process.communicate(timeout=min(.25,max(.001,deadline-time.monotonic())));break
            except subprocess.TimeoutExpired:
                if time.monotonic()>=deadline:
                    process.kill();process.communicate();raise SFXError('音效处理超时。')
        if process.returncode:raise SFXError(stderr.decode('utf-8',errors='replace')[-2500:])
        return stdout

def pcm_info(path):
    with wave.open(str(path),'rb') as audio:
        if audio.getframerate()!=RATE or audio.getnchannels()!=2 or audio.getsampwidth()!=2:raise SFXError('音效托管文件格式不是48kHz立体声PCM。')
        frames=audio.getnframes()
        if frames<=0:raise SFXError('音效为空。')
        return dict(samples=frames,duration=frames/RATE)

class SFXLibrary:
    def __init__(self,data_dir):
        self.root=Path(data_dir)/'clip_sfx';self.root.mkdir(parents=True,exist_ok=True)
        self.managed=self.root/'managed';self.managed.mkdir(exist_ok=True);self.db=self.root/'library.sqlite3'
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS assets(id TEXT PRIMARY KEY,name TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL UNIQUE,duration REAL NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,source_path TEXT NOT NULL,source_sha256 TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bindings(id TEXT PRIMARY KEY,asset_id TEXT NOT NULL REFERENCES assets(id),clip_hash TEXT NOT NULL,clip_path TEXT NOT NULL,clip_label TEXT NOT NULL,gain_db REAL NOT NULL DEFAULT -12,offset_seconds REAL NOT NULL DEFAULT 0,mode TEXT NOT NULL DEFAULT 'once',UNIQUE(asset_id,clip_hash));''')
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.db,timeout=20);db.row_factory=sqlite3.Row;db.execute('PRAGMA foreign_keys=ON')
        try:
            yield db;db.commit()
        except BaseException:
            db.rollback();raise
        finally:db.close()
    def assets(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT a.*,count(b.id) binding_count FROM assets a LEFT JOIN bindings b ON a.id=b.asset_id GROUP BY a.id ORDER BY a.name,a.id')]
    def bindings(self,asset_id=None):
        with self.connect() as db:
            sql='SELECT b.*,a.name,a.path audio_path,a.sha256,a.duration audio_duration FROM bindings b JOIN assets a ON a.id=b.asset_id'
            return [dict(r) for r in db.execute(sql+(' WHERE b.asset_id=?' if asset_id else '')+' ORDER BY b.clip_label,b.id',(asset_id,) if asset_id else ())]
    def import_audio(self,source,name,ffmpeg):
        source=Path(source).resolve()
        if not source.is_file():raise SFXError('音效文件不存在。')
        name=str(name).strip()
        if not name:raise SFXError('请填写音效名称。')
        original=file_hash(source);temp=self.managed/(uuid.uuid4().hex+'.part.wav')
        try:
            run([ffmpeg,'-v','error','-nostdin','-y','-i',source,'-map','0:a:0','-vn','-ar',RATE,'-ac',2,'-c:a','pcm_s16le',temp])
            info=pcm_info(temp)
            if info['duration']>3600:raise SFXError('单份音效请控制在1小时以内。')
            if original!=file_hash(source):raise SFXError('导入期间原音效被修改，请重新导入。')
            digest=file_hash(temp);target=self.managed/(digest+'.wav')
            from file_mutex import file_mutex
            with file_mutex(self.root/'import.lock',timeout=30):
                if target.exists():
                    if file_hash(target)!=digest:raise SFXError('同名托管文件内容异常，未覆盖。')
                else:os.replace(temp,target)
                with self.connect() as db:
                    existing=db.execute('SELECT * FROM assets WHERE sha256=?',(digest,)).fetchone()
                    if existing:return dict(existing)
                    aid=uuid.uuid4().hex
                    db.execute('INSERT INTO assets VALUES(?,?,?,?,?,1,?,?)',(aid,name,str(target),digest,info['duration'],str(source),original))
                    return dict(db.execute('SELECT * FROM assets WHERE id=?',(aid,)).fetchone())
        finally:temp.unlink(missing_ok=True)
    def rename(self,asset_id,name):
        if not str(name).strip():raise SFXError('音效名称不能为空。')
        with self.connect() as db:db.execute('UPDATE assets SET name=? WHERE id=?',(str(name).strip(),asset_id))
    def set_enabled(self,asset_id,enabled):
        with self.connect() as db:db.execute('UPDATE assets SET enabled=? WHERE id=?',(int(bool(enabled)),asset_id))
    def bind(self,asset_id,clips,*,gain_db=-12,offset_seconds=0,mode='once'):
        gain_db=float(gain_db);offset_seconds=float(offset_seconds)
        if not math.isfinite(gain_db) or not -60<=gain_db<=12:raise SFXError('音效音量范围为-60～+12dB。')
        if not math.isfinite(offset_seconds) or not 0<=offset_seconds<=3600:raise SFXError('音效起点偏移范围为0～3600秒。')
        if mode not in ('once','loop'):raise SFXError('未知音效播放方式。')
        prepared=[]
        for clip in clips:
            path=Path(clip['path']).resolve()
            if not path.is_file():raise SFXError('绑定的视频片段不存在：'+str(path))
            digest=file_hash(path)
            if clip.get('hash') and clip['hash']!=digest:raise SFXError('绑定的片段内容已经变化。')
            prepared.append((uuid.uuid4().hex,asset_id,digest,str(path),clip.get('display') or path.name,gain_db,offset_seconds,mode))
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM assets WHERE id=?',(asset_id,)).fetchone():raise SFXError('音效不存在。')
            db.executemany('''INSERT INTO bindings VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(asset_id,clip_hash) DO UPDATE SET clip_path=excluded.clip_path,clip_label=excluded.clip_label,gain_db=excluded.gain_db,offset_seconds=excluded.offset_seconds,mode=excluded.mode''',prepared)
    def unbind(self,binding_ids):
        with self.connect() as db:db.executemany('DELETE FROM bindings WHERE id=?',[(i,) for i in binding_ids])
    def update_options(self,binding_ids,*,gain_db,offset_seconds,mode):
        gain_db=float(gain_db);offset_seconds=float(offset_seconds)
        if not math.isfinite(gain_db) or not -60<=gain_db<=12 or not math.isfinite(offset_seconds) or not 0<=offset_seconds<=3600 or mode not in ('once','loop'):raise SFXError('音效音量、偏移或播放方式无效。')
        with self.connect() as db:db.executemany('UPDATE bindings SET gain_db=?,offset_seconds=?,mode=? WHERE id=?',[(gain_db,offset_seconds,mode,i) for i in binding_ids])
    def active_bindings(self):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT b.*,a.name,a.path audio_path,a.sha256,a.duration audio_duration FROM bindings b JOIN assets a ON a.id=b.asset_id WHERE a.enabled=1 ORDER BY b.id')]

    def update_gain(self,binding_ids,gain_db):
        """Atomically edit only gain on explicitly captured stable binding IDs."""
        gain_db=float(gain_db)
        if not math.isfinite(gain_db) or not -60<=gain_db<=12:raise SFXError('音效音量范围为-60～+12dB。')
        ids=list(dict.fromkeys(binding_ids))
        if not ids:raise SFXError('没有选择待调整的音效绑定。')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            found=set()
            for start in range(0,len(ids),900):
                chunk=ids[start:start+900]
                found.update(row['id'] for row in db.execute('SELECT id FROM bindings WHERE id IN ('+','.join('?' for _ in chunk)+')',chunk))
            if found!=set(ids):raise SFXError('部分音效绑定已被移除，请刷新后重试；未修改其他绑定。')
            db.executemany('UPDATE bindings SET gain_db=? WHERE id=?',[(gain_db,bid) for bid in ids])
        return len(ids)

def snapshot_digest(snapshot):
    content={k:v for k,v in snapshot.items() if k!='digest'}
    content['events']=[{k:v for k,v in e.items() if k!='audio_path'} for e in snapshot['events']]
    return hashlib.sha256(json.dumps(content,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def capture_config(cfg,data_dir):
    if not cfg.get('clip_sfx_enabled') or not ball_profile(cfg):return cfg
    if 'clip_sfx_bindings_snapshot' in cfg:return cfg
    return dict(cfg,clip_sfx_bindings_snapshot=SFXLibrary(data_dir).active_bindings())

def freeze_batch(batch,data_dir,snapshot_root):
    cfg=batch['config']
    if not cfg.get('clip_sfx_enabled') or not ball_profile(cfg):return
    bindings=cfg.get('clip_sfx_bindings_snapshot')
    if bindings is None:bindings=SFXLibrary(data_dir).active_bindings()
    by_hash={}
    for binding in bindings:by_hash.setdefault(binding['clip_hash'],[]).append(binding)
    copies={};snapshot_root=Path(snapshot_root)
    for item in batch['items']:
        fps=float(cfg['fps']);total=int(item.get('total_frames') or round(item['duration']*fps));events=[];skipped=[]
        for index,clip in enumerate(item['clips']):
            start=int(clip['timeline_start_frame']);end=min(total,start+int(clip['duration_frames']))
            for binding in by_hash.get(clip.get('hash'),[]):
                offset=round(binding['offset_seconds']*fps);onset=start+offset
                if onset>=end:
                    skipped.append(dict(binding_id=binding['id'],clip_label=binding['clip_label'],reason='音效起点已超出本次片段'))
                    continue
                digest=binding['sha256']
                if digest not in copies:
                    source=Path(binding['audio_path'])
                    if not source.is_file() or file_hash(source)!=digest:raise SFXError('已启用音效缺失或内容变化：'+binding['name'])
                    pcm_info(source);snapshot_root.mkdir(parents=True,exist_ok=True);target=snapshot_root/(digest+'.wav')
                    if not target.exists():shutil.copy2(source,target)
                    if file_hash(target)!=digest:raise SFXError('冻结音效校验失败。')
                    copies[digest]=str(target)
                events.append(dict(binding_id=binding['id'],asset_id=binding['asset_id'],name=binding['name'],clip_index=index,clip_hash=clip['hash'],clip_label=clip.get('display',binding['clip_label']),
                    start_frame=onset,end_frame=end,gain_db=binding['gain_db'],mode=binding['mode'],sha256=digest,audio_path=copies[digest],audio_duration=binding['audio_duration']))
        snapshot=dict(revision=REVISION,fps=fps,total_frames=total,events=events,skipped=skipped)
        snapshot['digest']=snapshot_digest(snapshot);item['clip_sfx_snapshot']=snapshot
    batch['clip_sfx_summary']=dict(revision=REVISION,event_count=sum(len(i['clip_sfx_snapshot']['events']) for i in batch['items']))
    if not batch['clip_sfx_summary']['event_count']:batch.setdefault('warnings',[]).append('片段音效已启用，但本批未选到有效的已绑定片段；视频长度及原配音/BGM保持。')

def validate(item,cfg,verify_files=True):
    snapshot=item.get('clip_sfx_snapshot')
    if not snapshot:return
    if not ball_profile(cfg):raise SFXError('牛肉丸片段音效不能用于其他品类。')
    if snapshot.get('revision')!=REVISION or snapshot.get('digest')!=snapshot_digest(snapshot):raise SFXError('冻结音效配置指纹不一致。')
    fps=float(cfg['fps']);total=int(item.get('total_frames') or round(item['duration']*fps))
    if snapshot['fps']!=fps or snapshot['total_frames']!=total:raise SFXError('音效时间线与冻结视频长度不一致。')
    checked=set()
    for event in snapshot['events']:
        index=event['clip_index']
        if not isinstance(index,int) or not 0<=index<len(item['clips']):raise SFXError('音效片段索引无效。')
        clip=item['clips'][index];start=int(clip['timeline_start_frame']);end=min(total,start+int(clip['duration_frames']))
        if clip['hash']!=event['clip_hash'] or not start<=event['start_frame']<event['end_frame']<=end:raise SFXError('音效已脱离对应视频片段。')
        if event['mode'] not in ('once','loop') or not math.isfinite(event['gain_db']) or not -60<=event['gain_db']<=12:raise SFXError('音效参数非法。')
        if verify_files and event['sha256'] not in checked:
            audio=Path(event['audio_path'])
            if not audio.is_file() or file_hash(audio)!=event['sha256']:raise SFXError('冻结的片段音效缺失或内容变化，未替换为当前音效库。')
            pcm_info(audio);checked.add(event['sha256'])

def overlay(item,cfg,base,ffmpeg,cancel=None):
    snapshot=item.get('clip_sfx_snapshot')
    if not snapshot or not snapshot['events']:return None
    validate(item,cfg);base=Path(base);fps=float(cfg['fps']);target=round(item['duration']*RATE)
    info=pcm_info(base)
    if info['samples']!=target:raise SFXError('原混音长度与视频帧边界不一致，未用音效补齐。')
    args=[ffmpeg,'-v','error','-nostdin','-y','-i',base];filters=[f'[0:a]asetpts=PTS-STARTPTS[base]'];labels=['[base]'];sources={}
    for n,event in enumerate(snapshot['events'],1):sources.setdefault((event['sha256'],event['mode']),[]).append((n,event))
    for input_index,entries in enumerate(sources.values(),1):
        event=entries[0][1]
        if event['mode']=='loop':args+=['-stream_loop','-1']
        args+=['-i',event['audio_path']]
        filters.append(f'[{input_index}:a]asplit={len(entries)}'+''.join(f'[src{n}]' for n,_ in entries))
    for n,event in enumerate(snapshot['events'],1):
        duration=(event['end_frame']-event['start_frame'])/fps;audible=duration if event['mode']=='loop' else min(duration,event['audio_duration'])
        samples=round(duration*RATE);delay=round(event['start_frame']/fps*RATE);fade=min(.015,audible/3)
        filters.append(f'[src{n}]atrim=end_sample={samples},asetpts=PTS-STARTPTS,volume={10**(event["gain_db"]/20):.9f},afade=t=in:st=0:d={fade:.9f},afade=t=out:st={audible-fade:.9f}:d={fade:.9f},adelay={delay}S:all=1,apad=whole_len={target},atrim=end_sample={target}[sfx{n}]')
        labels.append(f'[sfx{n}]')
    filters.append(''.join(labels)+f'amix=inputs={len(labels)}:duration=first:normalize=0:dropout_transition=0,alimiter=limit=0.85:level=false:latency=true,atrim=end_sample={target}[out]')
    temp=base.with_name(base.stem+'.sfx.part.wav');script=base.with_name(base.stem+'.sfx.filters.txt')
    try:
        script.write_text(';'.join(filters),encoding='utf-8')
        output_args=['-map','[out]','-ar',RATE,'-ac',2,'-c:a','pcm_s16le',temp]
        try:run(args+['-/filter_complex',script]+output_args,timeout=max(120,item['duration']*5),cancel=cancel)
        except SFXError as exc:
            if 'Unrecognized option' not in str(exc):raise
            run(args+['-filter_complex_script',script]+output_args,timeout=max(120,item['duration']*5),cancel=cancel)
        if pcm_info(temp)['samples']!=target:raise SFXError('音效混音采样长度异常。')
        os.replace(temp,base)
    finally:temp.unlink(missing_ok=True);script.unlink(missing_ok=True)
    return dict(revision=REVISION,event_count=len(snapshot['events']),snapshot_digest=snapshot['digest'],samples=target,video_duration_unchanged=True)
