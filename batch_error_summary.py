"""One end-of-batch error view; per-item workers only record and continue."""
POLICY='skip_recompose_v1'

def planning_help(row):
    """A bounded search miss is not a proof that the entire library is infeasible."""
    if row.get('failure_code')!='VOICE_MATCH_SEARCH_EXHAUSTED':return row.get('error','处理失败')
    rejects=row.get('rejection_summary',{})
    history=sum(v for k,v in rejects.items() if 'HISTORY_DEDUP_REJECTED' in k)
    target=row.get('voice_seconds')
    timing=f'配音约{target:.2f}秒；' if target else ''
    cause='已达到搜索时间预算' if row.get('search_stop_reason')=='time_budget' else '本轮候选未通过全部校验'
    lines=[timing+cause+'，这不代表全库一定没有可用组合。']
    if history:lines.append(f'{history}个时长候选被历史重复限制拦截；增加不同画面素材可提高成功率。')
    if row.get('safe_cut_count',0)==0:lines.append('当前候选均未允许安全裁切，只能使用完整画面；可到“按组复核 / 安全裁切”检查确实可裁的素材。')
    else:lines.append('程序会保留裁切许可、首尾保护及最短镜头限制；检查素材余量或允许范围后可重新组合。')
    lines.append('成功方案已保留，可以先生成；原始错误和候选明细见批次清单。')
    return '\n'.join(lines)

def signature(batch):
    import hashlib,json
    rows=[(r.get('number'),r.get('script_id'),r.get('error')) for r in list(batch.get('planning_failed_tasks',[]))+list(batch.get('voice_failed_tasks',[]))+list(batch.get('items',[])) if r.get('error') or r.get('status') in ('PLANNING_FAILED','VOICE_FAILED','failed','RENDER_BLOCKED')]
    return hashlib.sha256(json.dumps([rows,batch.get('preparation_attempt_errors',[]),batch.get('candidate_errors',[])],ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def summary(batch):
    entries=[];seen=set()
    for row in list(batch.get('planning_failed_tasks',[]))+list(batch.get('voice_failed_tasks',[]))+list(batch.get('items',[])):
        if row.get('status') not in ('PLANNING_FAILED','VOICE_FAILED','failed','RENDER_BLOCKED') and not row.get('error'):continue
        identity=(row.get('number'),row.get('script_id'),row.get('error'))
        if identity in seen:continue
        seen.add(identity)
        entries.append(f'第{row.get("number","?")}条 {row.get("script_id","")}：{row.get("user_explanation") or planning_help(row)}')
    attempts=batch.get('preparation_attempt_errors',[])
    candidates=batch.get('candidate_errors',[])
    if not entries and not attempts and not candidates:return ''
    done=sum(i.get('status')=='completed' for i in batch.get('items',[]))
    lines=[f'本批处理结束：已组合{len(batch.get("items",[]))}条，已生成{done}条，仍失败{len(entries)}条。']
    if attempts:lines.append(f'配音准备已记录{sum(len(r.get("attempts",[])) for r in attempts)}次异常，并尝试其他可用配音；详细记录保留在批次清单。')
    if candidates:lines.append(f'组合候选发生{len(candidates)}次异常，已跳过并重新组合；详细记录保留在批次清单。')
    return '\n'.join(lines+entries[:20]+(['其余错误见批次清单和运行日志。'] if len(entries)>20 else []))
