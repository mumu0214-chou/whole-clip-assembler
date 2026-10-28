"""Initialize explicitly shared text on a fresh installation without replacing data."""
from pathlib import Path
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
STARTER = ROOT / 'starter_content'

def publish_if_missing(target, write_temp):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        print('已有文件，保留：' + str(target))
        return False
    fd, temporary = tempfile.mkstemp(prefix='.starter-', suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        write_temp(Path(temporary))
        try:
            os.link(temporary, target)
        except FileExistsError:
            print('文件已由其他进程创建，保留：' + str(target))
            return False
        print('已初始化：' + str(target))
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)

def create_database(target):
    from script_library import ScriptLibrary, normalize_text
    from editing_profiles import NAMES
    data = json.loads((STARTER / 'scripts.json').read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('scripts'), list):
        raise ValueError('共享文案格式不正确')
    lib = ScriptLibrary(target)
    stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
    with lib._connect() as db:
        for item in data['scripts']:
            profile, spec = item['profile_id'], item['spec_id']
            if profile not in NAMES or not db.execute('SELECT 1 FROM product_specs WHERE profile_id=? AND spec_id=?', (profile,spec)).fetchone():
                raise ValueError('未知文案品类或规格')
            raw = normalize_text(item['raw_text'])
            if not raw or item['enabled'] not in (0,1): raise ValueError('共享文案数据不正确')
            db.execute('''INSERT INTO scripts(script_id,raw_text,text_hash,created_at,updated_at,profile_id,spec_id,enabled)
                          VALUES(?,?,?,?,?,?,?,?)''',
                       (item['script_id'],raw,hashlib.sha256(raw.encode('utf-8')).hexdigest(),stamp,stamp,profile,spec,item['enabled']))

def main():
    publish_if_missing(ROOT/'顶部文案库.txt', lambda p: p.write_bytes((STARTER/'top_texts.txt').read_bytes()))
    publish_if_missing(ROOT/'data'/'script_library.sqlite3', create_database)

if __name__ == '__main__':
    main()
