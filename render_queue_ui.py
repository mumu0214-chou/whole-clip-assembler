"""Append frozen batches behind the current consumer without replacing its state."""
from pathlib import Path
from core import read_json


from review_ui import ReviewControls


class RenderQueueControls(ReviewControls):
    def queue_plan(self,path):
        from batch_retention import ensure_resumable
        if getattr(self,'render_batch_path',None) and Path(path).resolve()==Path(self.render_batch_path).resolve() and self.render_running:
            self.log('该批次正在生成，没有重复加入队列。');return
        batch=ensure_resumable(Path(path),self.store)
        if batch.get('schema_version')!=1 or not isinstance(batch.get('items'),list):raise ValueError('不是本工具支持的批次清单')
        if not batch.get('items'):raise ValueError('该批次没有待剪辑组合')
        from background_tasks import snapshot_batch_references
        snapshot_batch_references(batch,Path(path).parent/'input_snapshot')
        added=self.video_queue.add(path,batch,rights_confirmed=bool(self.rights_var.get()))
        self._queue_batches[batch['id']]=batch
        if not self.render_running and (not self.render_batch_state or all(i['status']=='completed' for i in self.render_batch_state.get('items',[]))):
            first=self.video_queue.next();first_batch=self._queue_batches.get(first['id']) or read_json(Path(first['path']),None)
            self.render_batch_state=first_batch;self.render_batch_path=Path(first['path'])
        self.refresh_render();self.tabs.select(self.render_tab)
        self.log(('已追加到待剪辑队列：' if added else '该批次已在待剪辑队列：')+batch['id']+'；当前生成保持，后续按顺序剪辑。')

    def continue_render_queue(self):
        if self.busy or not self.video_queue.auto or self.pending_close:return
        entry=self.video_queue.next()
        if not entry:return
        try:
            self.open_render_batch_path(Path(entry['path']));self.render()
        except Exception as exc:
            self.video_queue.auto=False;self.log('待剪辑队列暂停：'+str(exc))

    def refresh_queued_rows(self):
        current=(self.render_batch_state or {}).get('id')
        for entry in self.video_queue.pending():
            if entry['id']==current:continue
            batch=self._queue_batches.get(entry['id'])
            if batch is None:
                batch=read_json(Path(entry['path']),None)
                if batch:self._queue_batches[entry['id']]=batch
            if not batch:continue
            for item in batch.get('items',[]):
                if item['status']=='completed':continue
                if not self.render_tree.exists(item['id']):self.render_tree.insert('','end',iid=item['id'])
                self.update_tree_item(self.render_tree,item,batch)
                values=list(self.render_tree.item(item['id'],'values'));values[1]='排队待剪辑'
                values[6]='【'+entry['category']+' / '+entry['spec']+'】'+str(values[6])
                self.render_tree.item(item['id'],values=values)
        self.queue_status.set(f'待剪辑队列：{len(self.video_queue.pending())}批；追加后按顺序处理，关闭工具后保留，重开须手动开始。')

    def remove_selected_queued(self):
        ids=set(self.render_tree.selection())
        if not ids:raise ValueError('请先选择排队中的组合')
        targets=[]
        for entry in self.video_queue.pending():
            batch=self._queue_batches.get(entry['id']) or read_json(Path(entry['path']),{})
            if any(item['id'] in ids for item in batch.get('items',[])):targets.append(entry)
        if not targets:raise ValueError('选中项属于当前剪辑批次，不能移除')
        for entry in targets:self.video_queue.remove(entry['path'])
        if not self.render_running and any(e['id']==(self.render_batch_state or {}).get('id') for e in targets):
            self.render_batch_state=None;self.render_batch_path=None
        self.refresh_render();self.log('已从队列移除'+str(len(targets))+'批；组合清单与预留保留，可从历史批次重新追加。')
