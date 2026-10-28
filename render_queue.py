"""Durable manifest references; batches and frozen item identities are never merged."""
from pathlib import Path
from core import read_json,atomic_json


class RenderQueue:
    def __init__(self,path):
        self.path=Path(path);value=read_json(self.path,dict(revision=1,entries=[]))
        if value.get('revision')!=1 or not isinstance(value.get('entries'),list):raise ValueError('待剪辑队列损坏，原文件保留')
        self.entries=value['entries']
        for entry in self.entries:
            if entry.get('state') not in ('pending','running'):raise ValueError('待剪辑队列状态无效')
            if entry['state']=='running':entry['state']='pending'
        self.auto=False

    def save(self):atomic_json(self.path,dict(revision=1,entries=self.entries))

    def add(self,path,batch,*,rights_confirmed=False):
        path=str(Path(path).resolve())
        if any(e['path']==path or e['id']==batch['id'] for e in self.entries):return False
        self.entries.append(dict(path=path,id=batch['id'],state='pending',
            category=batch['config'].get('editing_profile_name',''),spec=batch['config'].get('product_spec_name','旧批次'),rights_confirmed=bool(rights_confirmed)))
        self.save();return True

    def start(self,path,batch):
        resolved=str(Path(path).resolve())
        if any(e['state']=='running' and e['path']!=resolved for e in self.entries):raise ValueError('另一个队列批次正在生成')
        self.add(path,batch,rights_confirmed=True)
        for e in self.entries:
            if e['path']==resolved:e['state']='running'
        self.save();self.auto=True

    def halt(self):
        self.auto=False
        for e in self.entries:
            if e['state']=='running':e['state']='pending'
        self.save()

    def complete(self,path):
        resolved=str(Path(path).resolve());self.entries=[e for e in self.entries if e['path']!=resolved];self.save()

    def next(self):return next((e for e in self.entries if e['state']=='pending'),None)

    def pending(self):return [e for e in self.entries if e['state']=='pending']

    def remove(self,path):
        resolved=str(Path(path).resolve())
        if any(e['path']==resolved and e['state']=='running' for e in self.entries):raise ValueError('当前正在剪辑的批次不能从队列移除')
        self.entries=[e for e in self.entries if e['path']!=resolved];self.save()
