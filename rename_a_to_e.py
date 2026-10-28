# -*- coding: utf-8 -*-
"""Number video files from 1 separately in each A-E directory and subdirectory."""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import uuid

from core import APP_DIR, VIDEO_EXTS, atomic_json, natural_key


PLAN_PATH = APP_DIR / 'data' / 'name_plans' / 'A到E顺序命名方案.json'
REPORT_PATH = APP_DIR / 'data' / 'name_plans' / 'A到E顺序命名预览.txt'
JOURNAL_DIR = APP_DIR / 'data' / 'name_logs'
PLAN_VERSION = 1


def settings():
    cfg = json.loads((APP_DIR / 'data' / 'settings.json').read_text(encoding='utf-8'))
    folders = {group: cfg['group_folders'][group] for group in 'ABCDE'}
    return Path(cfg['material_root']).resolve(), folders


def path_key(path):
    return os.path.normcase(str(path.resolve())).casefold()


def is_link(path, base):
    node = path
    while node != base:
        if node.is_symlink() or (hasattr(node, 'is_junction') and node.is_junction()):
            return True
        node = node.parent
    return False


def inventory(root, folders):
    if not root.is_dir():
        raise ValueError(f'素材根目录不存在：{root}')
    by_directory = defaultdict(list)
    skipped_opening = 0
    missing = []
    for group in 'ABCDE':
        base = root / folders[group]
        if not base.is_dir():
            missing.append(group)
            continue
        if is_link(base, root):
            raise ValueError(f'组目录是链接或目录联接，请先核对：{base}')
        for path in base.rglob('*'):
            if not path.is_file() or path.suffix.lower() not in VIDEO_EXTS:
                continue
            if is_link(path, root):
                raise ValueError(f'视频路径包含链接或目录联接：{path}')
            within = path.relative_to(base).parts
            if group == 'C' and len(within) >= 2 and within[0] == '开头':
                skipped_opening += 1
                continue
            by_directory[path.parent].append(path)
    return by_directory, skipped_opening, missing


def build_plan(root, folders):
    by_directory, skipped_opening, missing = inventory(root, folders)
    entries = []
    for directory in sorted(by_directory, key=lambda path: natural_key(str(path.relative_to(root)))):
        files = sorted(by_directory[directory],
                       key=lambda path: (natural_key(path.stem), path.suffix.casefold(), path.name.casefold()))
        for number, source in enumerate(files, 1):
            target = directory / f'{number}{source.suffix}'
            stat = source.stat()
            entries.append({'from': source.relative_to(root).as_posix(),
                            'to': target.relative_to(root).as_posix(),
                            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns})
    return {'version': PLAN_VERSION, 'material_root': str(root), 'group_folders': folders,
            'entries': entries, 'skipped_c_opening': skipped_opening, 'missing_groups': missing}


def active_batch_references(changes):
    changed = {path_key(source) for source, _, _ in changes}
    findings = []
    for manifest in (APP_DIR / 'data' / 'batches').glob('*/batch_manifest.json'):
        try:
            batch = json.loads(manifest.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            raise ValueError(f'无法核对已保存批次：{manifest}：{exc}') from exc
        if batch.get('abandoned_at'):
            continue
        for item in batch.get('items', []):
            if item.get('status') in ('completed', 'abandoned'):
                continue
            for clip in item.get('clips', []):
                source = clip.get('path')
                if not source and clip.get('relative_path'):
                    source = str(Path(batch['config']['material_root']) / clip['relative_path'])
                if source and path_key(Path(source)) in changed:
                    findings.append(f'{batch.get("id", manifest.parent.name)} / 第{item.get("number", "?")}条 / {source}')
                    if len(findings) >= 20:
                        return findings
    return findings


def checked_changes(plan):
    root = Path(plan['material_root']).resolve()
    if plan.get('version') != PLAN_VERSION:
        raise ValueError('命名方案版本不匹配，请重新预览。')
    current_root, current_folders = settings()
    if root != current_root or plan.get('group_folders') != current_folders:
        raise ValueError('素材目录设置已变化，请重新预览。')
    current = build_plan(root, current_folders)
    if current['entries'] != plan['entries']:
        raise ValueError('视频文件或自然排序已变化，请重新预览；尚未改名。')
    all_sources = {path_key(root / item['from']) for item in plan['entries']}
    changes = []
    for item in plan['entries']:
        source, target = root / item['from'], root / item['to']
        if source == target:
            continue
        if not source.is_file():
            raise ValueError(f'源文件不存在：{source}')
        if target.exists() and path_key(target) not in all_sources:
            raise ValueError(f'目标文件已被占用，拒绝覆盖：{target}')
        changes.append((source, target, item))
    return changes


def make_report(plan, references):
    entries = plan['entries']
    lines = [f'素材根目录：{plan["material_root"]}',
             'A～E 每个目录及子目录分别从 1 编号；原文件按文件名自然数字顺序排列。',
             f'共发现 {len(entries)} 个视频，其中需改名 {sum(item["from"] != item["to"] for item in entries)} 个。',
             f'C/开头特殊命名素材跳过 {plan["skipped_c_opening"]} 个。',
             f'缺少组目录：{", ".join(plan["missing_groups"]) or "无"}。',
             f'未完成批次占用待改名文件：{len(references)} 条。',
             '']
    if references:
        lines += ['未完成批次引用（执行时将拦截）：', *references, '']
    current = None
    for item in entries:
        directory = str(Path(item['from']).parent)
        if directory != current:
            current = directory
            lines += [f'[{directory}]']
        lines.append(f'{Path(item["from"]).name}  →  {Path(item["to"]).name}'
                     if item['from'] != item['to'] else f'{Path(item["from"]).name}  （已符合）')
    return '\n'.join(lines) + '\n'


def preview():
    root, folders = settings()
    plan = build_plan(root, folders)
    plan['created_at'] = datetime.now().astimezone().isoformat()
    # This check reads only saved JSON manifests, never historical video files.
    changes = [(root / item['from'], root / item['to'], item)
               for item in plan['entries'] if item['from'] != item['to']]
    references = active_batch_references(changes)
    PLAN_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_json(PLAN_PATH, plan)
    REPORT_PATH.write_text(make_report(plan, references), encoding='utf-8-sig')
    print(f'已保存预览：{REPORT_PATH}')
    print(f'共 {len(plan["entries"])} 个视频，需改名 {len(changes)} 个；C/开头跳过 {plan["skipped_c_opening"]} 个。')
    if references:
        print(f'有 {len(references)} 条未完成批次引用待改名文件；执行前需先处理这些批次，详情见预览。')


def journal_update(path, record):
    atomic_json(path, record)


def rollback(entries, journal, record):
    held = []
    errors = []
    for entry in entries:
        current = entry['temp'] if entry['state'] == 'temp' else entry['target'] if entry['state'] == 'final' else None
        if current is None:
            continue
        try:
            if not current.exists():
                raise OSError('临时文件或目标文件不存在')
            hold = current.with_name('.codex_rollback_' + uuid.uuid4().hex + '.tmp')
            current.rename(hold)
            held.append((entry, hold))
        except OSError as exc:
            errors.append(f'{current}：{exc}')
    for entry, hold in held:
        try:
            if entry['source'].exists():
                raise OSError('原文件名已被占用')
            hold.rename(entry['source'])
            entry['state'] = 'original'
        except OSError as exc:
            errors.append(f'{hold}：{exc}')
    record['status'] = 'needs_manual_check' if errors else 'rolled_back'
    record['rollback_errors'] = errors
    record['files'] = [{'from': str(e['source']), 'temp': str(e['temp']),
                        'to': str(e['target']), 'state': e['state']} for e in entries]
    journal_update(journal, record)
    return errors


def apply():
    if not PLAN_PATH.is_file():
        raise ValueError(f'尚无预览：{PLAN_PATH}；请先生成预览。')
    plan = json.loads(PLAN_PATH.read_text(encoding='utf-8'))
    changes = checked_changes(plan)
    references = active_batch_references(changes)
    if references:
        raise ValueError('待改名文件被未完成批次引用，已停止。请查看预览中的批次清单；不会改动旧方案。')
    if not changes:
        print('所有文件已按目录连续编号，无需改名。')
        return
    for previous in JOURNAL_DIR.glob('A到E改名_*.json'):
        try:
            if json.loads(previous.read_text(encoding='utf-8')).get('status') in ('in_progress', 'needs_manual_check'):
                raise ValueError(f'上次改名尚未完成，请先核对记录：{previous}')
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f'无法核对改名记录：{previous}：{exc}') from exc
    transaction = uuid.uuid4().hex
    entries = []
    for index, (source, target, _) in enumerate(changes):
        temp = source.with_name(f'.codex_rename_{transaction}_{index}.tmp')
        if temp.exists():
            raise ValueError(f'临时文件名已被占用：{temp}')
        entries.append({'source': source, 'temp': temp, 'target': target, 'state': 'original'})
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    journal = JOURNAL_DIR / f'A到E改名_{datetime.now():%Y%m%d_%H%M%S}_{transaction[:6]}.json'
    record = {'version': PLAN_VERSION, 'status': 'in_progress', 'plan': str(PLAN_PATH), 'files': []}
    journal_update(journal, record)
    try:
        for entry in entries:
            entry['source'].rename(entry['temp'])
            entry['state'] = 'temp'
            record['files'] = [{'from': str(e['source']), 'temp': str(e['temp']),
                                'to': str(e['target']), 'state': e['state']} for e in entries]
            journal_update(journal, record)
        for entry in entries:
            if entry['target'].exists():
                raise ValueError(f'目标文件被其他程序占用：{entry["target"]}')
            entry['temp'].rename(entry['target'])
            entry['state'] = 'final'
            record['files'] = [{'from': str(e['source']), 'temp': str(e['temp']),
                                'to': str(e['target']), 'state': e['state']} for e in entries]
            journal_update(journal, record)
    except Exception:
        errors = rollback(entries, journal, record)
        if errors:
            print('部分文件未能自动恢复，请查看记录：' + str(journal), file=sys.stderr)
        raise
    record['status'] = 'completed'
    journal_update(journal, record)
    print(f'已改名 {len(changes)} 个视频；只修改文件名，未写入视频内容。记录：{journal}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='A～E 各目录与子目录分别从 1 开始为视频编号。')
    parser.add_argument('action', nargs='?', choices=('menu', 'preview', 'apply'), default='menu')
    args = parser.parse_args(argv)
    try:
        action = args.action
        if action == 'menu':
            print('1. 预览 A～E 每个目录及子文件夹的编号')
            print('2. 按已保存的预览执行改名')
            choice = input('请选择 1 或 2（其他输入退出）：').strip()
            if choice not in ('1', '2'):
                print('已退出，未改名。')
                return 0
            action = 'preview' if choice == '1' else 'apply'
        preview() if action == 'preview' else apply()
    except (OSError, ValueError, KeyError) as exc:
        print(f'未执行改名：{exc}', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
