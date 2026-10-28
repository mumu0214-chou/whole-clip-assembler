"""Stable category/specification identities over the existing script database."""
import sqlite3
import uuid
from contextlib import contextmanager
from copy import deepcopy
from editing_profiles import FRESH,BALL,SHANTOU_BALL

REVISION='product_specs_v1'
DEFAULT_SPEC='default'
CATEGORIES={FRESH:'鲜切牛肉',BALL:'牛肉丸',SHANTOU_BALL:'汕头牛肉丸'}
DEFAULT_NAMES={FRESH:'通用规格',BALL:'49.9十包牛肉丸',SHANTOU_BALL:'通用规格'}


class Catalog:
    def __init__(self,path):
        self.path=path
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS product_categories(profile_id TEXT PRIMARY KEY,name TEXT NOT NULL UNIQUE);
                CREATE TABLE IF NOT EXISTS product_specs(profile_id TEXT NOT NULL,spec_id TEXT NOT NULL,name TEXT NOT NULL,
                PRIMARY KEY(profile_id,spec_id),UNIQUE(profile_id,name));''')
            for key,name in CATEGORIES.items():
                db.execute('INSERT OR IGNORE INTO product_categories VALUES(?,?)',(key,name))
                db.execute('INSERT OR IGNORE INTO product_specs VALUES(?,?,?)',(key,DEFAULT_SPEC,DEFAULT_NAMES[key]))

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=30);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def name(value):
        value=str(value).strip()
        if not value or len(value)>64 or any(ord(c)<32 for c in value):raise ValueError('名称须为1～64个字，不能包含换行')
        return value

    def categories(self):
        with self.connect() as db:return {r['profile_id']:r['name'] for r in db.execute('SELECT * FROM product_categories ORDER BY rowid')}

    def specs(self,profile):
        with self.connect() as db:return [dict(r) for r in db.execute('SELECT * FROM product_specs WHERE profile_id=? ORDER BY rowid',(profile,))]

    def get(self,profile,spec_id=DEFAULT_SPEC):
        with self.connect() as db:row=db.execute('SELECT * FROM product_specs WHERE profile_id=? AND spec_id=?',(profile,spec_id)).fetchone()
        if row is None:raise ValueError('所选规格不存在或不属于当前品类，请刷新规格列表')
        return dict(row)

    def rename_category(self,profile,name):
        value=self.name(name)
        if profile not in CATEGORIES:raise ValueError('未知品类')
        try:
            with self.connect() as db:db.execute('UPDATE product_categories SET name=? WHERE profile_id=?',(value,profile))
        except sqlite3.IntegrityError as exc:raise ValueError('品类名称已经存在') from exc

    def add_spec(self,profile,name):
        if profile not in CATEGORIES:raise ValueError('未知品类')
        key=uuid.uuid4().hex
        try:
            with self.connect() as db:db.execute('INSERT INTO product_specs VALUES(?,?,?)',(profile,key,self.name(name)))
        except sqlite3.IntegrityError as exc:raise ValueError('当前品类已有同名规格') from exc
        return key

    def rename_spec(self,profile,spec_id,name):
        self.get(profile,spec_id)
        try:
            with self.connect() as db:db.execute('UPDATE product_specs SET name=? WHERE profile_id=? AND spec_id=?',(self.name(name),profile,spec_id))
        except sqlite3.IntegrityError as exc:raise ValueError('当前品类已有同名规格') from exc

    def freeze(self,cfg,spec_id=None):
        cfg=deepcopy(cfg);profile=cfg['editing_profile_id'];spec=self.get(profile,spec_id or cfg.get('product_spec_id',DEFAULT_SPEC))
        cfg.update(product_spec_revision=REVISION,product_spec_id=spec['spec_id'],product_spec_name=spec['name'],editing_profile_name=self.categories()[profile])
        return cfg


def check_script(row,cfg):
    profile=cfg.get('editing_profile_id',FRESH);spec=cfg.get('product_spec_id',DEFAULT_SPEC)
    if row['profile_id']!=profile or row.get('spec_id',DEFAULT_SPEC)!=spec:
        raise ValueError('文案品类/规格与当前任务不一致，禁止混用规格文案和配音')


def check_package(package,cfg):
    if cfg.get('product_spec_revision')!=REVISION or not package:return
    if (package.get('script_profile_id')!=cfg['editing_profile_id'] or
            package.get('script_spec_id')!=cfg['product_spec_id']):
        raise ValueError('冻结文案品类/规格与任务不一致，未进入配音或选片')


def validate_item(item,cfg):
    if cfg.get('product_spec_revision')!=REVISION:return
    if item.get('product_spec_snapshot')!={k:cfg[k] for k in ('product_spec_revision','product_spec_id','product_spec_name','editing_profile_id','editing_profile_name')}:
        raise ValueError('商品规格冻结信息发生变化')
    if item.get('script_package'):check_package(item['script_package'],cfg)
