"""Create the recorded editing folder structure; never copy or modify media."""
from pathlib import Path, PurePosixPath
import json
from mac_paths import HOME, PROJECT

def create_layout(root=HOME):
    root = Path(root).expanduser()
    data = json.loads((PROJECT/'starter_content'/'workspace_directories.json').read_text(encoding='utf-8'))
    if data.get('version') != 1 or not isinstance(data.get('directories'), list):
        raise ValueError('目录清单格式不正确')
    paths = []
    for name in data['directories']:
        if not isinstance(name, str) or '\\' in name:
            raise ValueError('目录清单包含无效路径')
        p = PurePosixPath(name)
        if p.is_absolute() or not p.parts or any(part in ('.', '..') for part in p.parts):
            raise ValueError('目录清单包含越界路径：'+name)
        paths.append(p)
    root.mkdir(parents=True, exist_ok=True)
    for p in sorted(paths, key=lambda x:(len(x.parts),x.as_posix())):
        current=root
        for part in p.parts:
            current=current/part
            if current.is_symlink(): raise ValueError('目录中存在符号链接，请手动确认：'+str(current))
            current.mkdir(exist_ok=True)
    print(f'剪辑目录已就绪：{root}（{len(paths)} 个目录）')

if __name__ == '__main__':
    create_layout()
