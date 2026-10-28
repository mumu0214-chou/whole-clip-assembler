"""Join renderer diagnostics with the actual Python child-process failure."""
from pathlib import Path
import json,re,traceback
from datetime import datetime,timezone

def save_render_failure(folder,request,error):
    folder=Path(folder);target=folder/'render_failure.json'
    context=request.get('diagnostics',{})
    try:report=json.loads(target.read_text(encoding='utf-8'))
    except (OSError,ValueError):report={}
    if report.get('attempt_id')!=context.get('attemptId'):
        report={}
    text=''.join(traceback.format_exception(error))
    frame=re.search(r'(?:at )?frame (\d+)',text,re.I)
    if not report:
        report=dict(schema_version='render_failure_v1',job_id=context.get('jobId'),attempt_id=context.get('attemptId'),
                    timestamp=datetime.now(timezone.utc).isoformat(),failed_frame=int(frame[1]) if frame else None,
                    failure_stage='remotion_subprocess',remotion_concurrency=request.get('concurrency'),
                    requests=[],mapping=context.get('media',[]),root_cause_status='requires_evidence_review')
        # Node may have been killed before its catch handler. Recover only this attempt's
        # observed requests and actual concurrency; never reuse an older failure report.
        observed={}
        try:
            with (folder/'render_requests.jsonl').open(encoding='utf-8') as stream:
                for line in stream:
                    try:row=json.loads(line)
                    except ValueError:continue
                    if row.get('attempt_id')!=context.get('attemptId'):continue
                    if row.get('remotion_concurrency') is not None:
                        report['remotion_concurrency']=row['remotion_concurrency']
                    if row.get('event') in ('proxy_request_start','proxy_request_end'):
                        observed[row['request_id']]=row
        except OSError:pass
        report['requests']=[dict(r,elapsed_ms=None if r.get('request_state')=='pending' else r.get('elapsed_ms'))
                            for r in observed.values() if (frame and r.get('failed_frame')==int(frame[1])) or
                            r.get('request_state')!='completed' or (r.get('proxy_status') or 0)>=400][-32:]
        report['active_media_at_failure']=[m for m in context.get('media',[]) if frame and
                                          m.get('from',0)<=int(frame[1])<m.get('from',0)+m.get('frames',0)]
    cause=error
    code=None
    while cause is not None:
        if getattr(cause,'returncode',None) is not None:code=cause.returncode;break
        cause=cause.__cause__
    report['parent_subprocess']=dict(exit_code=code,error=text,log_path=str(folder/'render.log'))
    report['local_media_log']=str(folder/'media_server.jsonl')
    report['requests_log']=str(folder/'render_requests.jsonl')
    report['diagnostic_unknowns']=['Fields set to null were not observed; no source/frame or root cause is guessed.']
    content=json.dumps(report,ensure_ascii=False,indent=2)
    target.write_text(content,encoding='utf-8')
    attempt=context.get('attemptId','')
    if re.fullmatch(r'[\w-]+',attempt):target.with_name('render_failure.'+attempt+'.json').write_text(content,encoding='utf-8')
    return report
