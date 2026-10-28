'use strict';
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');

function createDiagnostics(request, directory) {
  const logPath = path.join(directory, 'render_requests.jsonl');
  const failurePath = path.join(directory, 'render_failure.json');
  let logFd;
  try {logFd=fs.openSync(logPath,'a');} catch(error) {console.error('ASSEMBLY:取帧日志打开失败：'+error.message);}
  const jobId = request.diagnostics?.jobId ?? null;
  const mappings = request.diagnostics?.media ?? [];
  const pending = new Map();
  const recent = [];
  const browserObservations = [];
  const resourceSamples = [];
  let sequence = 0, written = 0, stage = 'setup', failed = false, renderAttempt=0;
  const originalEmit = http.Server.prototype.emit;
  const servers = new WeakMap();
  const started = performance.now();
  let expectedTick = started + 2000;
  function write(event, fields = {}) {
    const record = {timestamp: new Date().toISOString(), job_id: jobId, attempt_id: request.diagnostics?.attemptId ?? null, event, stage,
      remotion_concurrency: request.concurrency, ...fields};
    record.render_attempt=renderAttempt;
    const line = JSON.stringify(record) + '\n';
    try {
      if (logFd !== undefined && written < 32 * 1024 * 1024) { fs.writeSync(logFd, line); written += Buffer.byteLength(line); }
    } catch (error) { console.error('ASSEMBLY:取帧诊断写入失败：' + error.message); }
    return record;
  }
  function identify(url, frame) {
    const choices = mappings.filter(m => m.url === url);
    const match = choices.find(m => Number.isFinite(frame) && frame >= m.from && frame < m.from + m.frames) ?? choices[0];
    return {original_path: match?.originalPath ?? null, render_path: match?.renderPath ?? null,
      clip_label: match?.label ?? null, clip_from: match?.from ?? null,
      source_window: match?.sourceWindow ?? null, mapping_confirmed: Boolean(match)};
  }
  function observe(server, req, res) {
    if (!req.url?.startsWith('/proxy?')) return;
    const origin = 'http://' + (req.headers.host || 'localhost');
    const parsed = new URL(req.url, origin);
    const mediaUrl = parsed.searchParams.get('src');
    const frameHeader = req.headers['x-assembly-frame'];
    const frame = frameHeader === undefined ? null : Number(frameHeader);
    const id = ++sequence;
    const row = {request_id: id, failed_frame: Number.isFinite(frame) ? frame : null,
      requested_time: Number(parsed.searchParams.get('time')), media_url: mediaUrl,
      proxy_url: parsed.href, ...identify(mediaUrl, frame), started: performance.now(),
      proxy_status: null, request_state: 'pending', elapsed_ms: 0};
    pending.set(id, row);
    write('proxy_request_start', {...row, started: undefined});
    let ended = false;
    const finish = state => {
      if (ended) return;
      ended = true;
      Object.assign(row, {elapsed_ms: Math.round(performance.now() - row.started),
        proxy_status: res.headersSent ? res.statusCode : null, request_state: state});
      pending.delete(id);recent.push(row);if (recent.length > 4096) recent.shift();
      write('proxy_request_end', {...row, started: undefined});
    };
    res.once('finish', () => finish('completed'));
    res.once('close', () => finish(res.writableFinished ? 'completed' : 'aborted'));
    res.once('error', error => {row.error = error.stack;finish('error');});
  }
  http.Server.prototype.emit = function(event, ...args) {
    try {
      if (event === 'listening') { servers.set(this, this.address());write('proxy_server_listening', {address: this.address()}); }
      if (event === 'request') observe(this, args[0], args[1]);
      if (event === 'close') write('proxy_server_closed', {address: servers.get(this)});
      if (event === 'error') write('proxy_server_error', {error: args[0]?.stack});
    } catch (error) { write('diagnostic_observer_error', {error: String(error)}); }
    return originalEmit.call(this, event, ...args);
  };
  const timer = setInterval(() => {
    const now = performance.now();
    const sample=write('render_resource_sample', {free_memory_bytes: os.freemem(), total_memory_bytes: os.totalmem(),
      node_memory: process.memoryUsage(), node_cpu: process.cpuUsage(),
      event_loop_lag_ms: Math.max(0, Math.round(now - expectedTick)), pending_proxy_requests: pending.size});
    resourceSamples.push(sample);if(resourceSamples.length>30)resourceSamples.shift();
    expectedTick = now + 2000;
  }, 2000);
  timer.unref();
  write('render_diagnostics_start', {node_pid: process.pid, requested_concurrency: request.requestedConcurrency ?? request.concurrency});
  return {
    beginAttempt(number) {renderAttempt=number;recent.length=0;pending.clear();browserObservations.length=0;},
    recovered() {
      failed=false;
      try {
        const report=JSON.parse(fs.readFileSync(failurePath,'utf8'));
        if(report.attempt_id===request.diagnostics?.attemptId && report.job_id===jobId) {
          report.recovered=true;report.recovered_by='single_concurrency_frame_retry';
          fs.writeFileSync(failurePath,JSON.stringify(report,null,2));
        }
      } catch(error) {write('diagnostic_recovery_report_error',{error:String(error)});}
      write('render_frame_wait_recovered');
    },
    stage(value) {stage = value;write('render_stage');},
    log: write,
    browserLog(log) {
      if (log.text?.startsWith('ASSEMBLY_FETCH:')) {
        try {
          const row=JSON.parse(log.text.slice(15));
          browserObservations.push({...identify(row.media_url,row.failed_frame),...row});
          if(browserObservations.length>128)browserObservations.shift();
          write('browser_proxy_observation',row);
        } catch (_) {write('browser_log', {text: log.text});}
      } else if (log.type === 'error' || log.type === 'warning') write('browser_log', log);
    },
    failure(error) {
      failed = true;
      const text = error.stack || String(error);
      const frameMatch = /(?:at )?frame (\d+)/i.exec(text);
      const proxyMatch = /Fetching (https?:\/\/[^\s"]+\/proxy\?[^\s"]+)/.exec(text);
      const all = [...recent, ...pending.values()];
      let requests = proxyMatch ? all.filter(r => r.proxy_url === proxyMatch[1]) : [];
      if (!requests.length && frameMatch) requests = all.filter(r => r.failed_frame === Number(frameMatch[1]));
      if (!requests.length) requests = all.filter(r => r.request_state !== 'completed' || r.proxy_status >= 400 || r.elapsed_ms >= 1000).slice(-32);
      const now = performance.now();
      requests = requests.map(r => ({...r, started: undefined,
        elapsed_ms: r.request_state === 'pending' ? Math.round(now-r.started) : r.elapsed_ms}));
      if (!requests.length) requests=browserObservations.filter(r=>!proxyMatch || r.proxy_url===proxyMatch[1]).slice(-16);
      if (!requests.length && proxyMatch) {
        const url=new URL(proxyMatch[1]);
        const frame=frameMatch ? Number(frameMatch[1]) : null;
        requests=[{proxy_url:url.href,media_url:url.searchParams.get('src'),
          requested_time:Number(url.searchParams.get('time')),failed_frame:frame,
          ...identify(url.searchParams.get('src'),frame),proxy_status:null,
          request_state:'not_observed',elapsed_ms:null}];
      }
      const failureStage = requests.some(r=>r.proxy_status>=400) ? 'proxy_http_error'
        : proxyMatch && requests.length && requests.every(r=>r.proxy_status===200 && r.request_state==='completed') ? 'browser_frame_delivery_or_consumption'
        : proxyMatch ? 'proxy_frame_request_wait' : stage === 'rendering' ? 'browser_frame_wait' : stage;
      const failure = {schema_version: 'render_failure_v1', timestamp: new Date().toISOString(),
        job_id: jobId, attempt_id: request.diagnostics?.attemptId ?? null,
        render_attempt: renderAttempt,
        failed_frame: frameMatch ? Number(frameMatch[1]) : requests.find(r => r.failed_frame !== null)?.failed_frame ?? null,
        failure_stage: failureStage, root_cause_status: 'requires_evidence_review',
        remotion_concurrency: request.concurrency, subprocess: {pid: process.pid, executable: process.execPath, exit_code: 1, error: text},
        requests, mapping: mappings,
        active_media_at_failure: frameMatch ? mappings.filter(m=>Number(frameMatch[1])>=m.from && Number(frameMatch[1])<m.from+m.frames) : [],
        free_memory_bytes: os.freemem(), total_memory_bytes: os.totalmem(),
        requests_log: logPath, resource_samples: resourceSamples, original_failure_preserved: true};
      try {
        fs.writeFileSync(failurePath, JSON.stringify(failure, null, 2));
        const attempt=request.diagnostics?.attemptId;
        if(attempt && /^[\w-]+$/.test(attempt)) {
          fs.writeFileSync(path.join(directory,`render_failure.${attempt}.json`),JSON.stringify(failure,null,2));
          fs.writeFileSync(path.join(directory,`render_failure.${attempt}.frame${renderAttempt}.json`),JSON.stringify(failure,null,2));
        }
      } catch (writeError) {console.error('ASSEMBLY:异常报告保存失败：'+writeError.message);}
      write('render_failure', failure);
      return failure;
    },
    close() {
      clearInterval(timer);http.Server.prototype.emit = originalEmit;
      write('render_diagnostics_end', {failed, pending_proxy_requests: pending.size, total_elapsed_ms: Math.round(performance.now()-started)});
      if(logFd !== undefined){fs.closeSync(logFd);logFd=undefined;}
    },
  };
}
module.exports = {createDiagnostics};
