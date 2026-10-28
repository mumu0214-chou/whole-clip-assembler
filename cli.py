# -*- coding: utf-8 -*-
"""Command-line batch entry. Uses the same code/settings/history as the desktop UI."""
import argparse
import copy
from pathlib import Path
import sys
from core import *


def main():
    parser=argparse.ArgumentParser(description='整段组合器命令行：纯BGM，不裁视频')
    parser.add_argument('--data-dir', default=str(APP_DIR/'data'))
    parser.add_argument('--resume', help='继续已有batch_manifest.json')
    parser.add_argument('--plan-only', action='store_true', help='只保存组合，不渲染')
    parser.add_argument('--scan-only', action='store_true', help='只读取技术清单')
    parser.add_argument('--count',type=int)
    parser.add_argument('--music-approved',action='store_true',help='明确确认本次音乐使用权限')
    parser.add_argument('--confirm-current-clips',action='store_true',help='明确确认本次扫描的启用片段均已人工复核且可整段使用')
    args=parser.parse_args()
    data=Path(args.data_dir)
    with instance_lock(data):
        store=Store(data)
        from batch_retention import sweep,ensure_resumable,REVISION
        sweep(store)
        if args.resume:
            if not args.music_approved:
                raise AssemblyError('继续渲染需添加 --music-approved 确认本次音乐用途。')
            path=Path(args.resume)
            from script_library import ScriptLibrary
            frozen=ensure_resumable(path,store)
            library=ScriptLibrary(frozen.get('config',{}).get('script_library_path') or data/'script_library.sqlite3')
            library.reconcile_batch(frozen)
            result=render_batch(path,store,print,on_item=lambda item:library.finish_item(item,item['status']=='completed') if item.get('status') in ('completed','failed','VOICE_FAILED') else None)
        else:
            cfg=copy.deepcopy(DEFAULTS)
            from bgm_balance import upgrade_settings
            cfg.update(upgrade_settings(read_json(data/'settings.json',{})))
            cfg['history_retention_revision']=REVISION
            if cfg.get('voice_source')=='external_files' and not args.scan_only:
                raise AssemblyError('CLI 仅支持续跑已冻结的外部配音批次；新外部配音规划请使用图形界面。')
            if args.count is not None:cfg['count']=args.count
            clips,music,warnings=scan_library(cfg,store,print)
            for w in warnings:print('提示：',w)
            if args.scan_only:
                atomic_json(data/'last_scan.json',{'clips':clips,'music':music,'warnings':warnings})
                print('扫描清单：',data/'last_scan.json')
                return
            if not args.music_approved:
                raise AssemblyError('规划/生成需添加 --music-approved 确认本次音乐用途。')
            if args.confirm_current_clips:
                for c in clips:
                    if c['enabled']:
                        store.set_flag(c['hash'],reviewed=True)
                        c['reviewed']=True
            ranges=read_json(data/'music_ranges.json',[])
            batch=plan_batch(clips,music,ranges,cfg,store,print)
            path=save_batch(batch,store)
            print('批次清单：',path)
            if args.plan_only:return
            result=render_batch(path,store,print)
        print('已完成：',sum(i['status']=='completed' for i in result['items']), '/',len(result['items']))
        if any(i['status']!='completed' for i in result['items']):
            raise SystemExit(2)

if __name__=='__main__':
    try:main()
    except AssemblyError as exc:
        print(str(exc),file=sys.stderr)
        raise SystemExit(1)
