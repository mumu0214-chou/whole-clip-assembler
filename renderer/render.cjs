'use strict';
// An adapter using dependencies from an existing Remotion project, or this folder.
// It does NOT modify that project's sources, configuration, index, or package.json.
const path = require('node:path');
const fs = require('node:fs');
const {createRequire} = require('node:module');
const crypto = require('node:crypto');
const {createDiagnostics} = require('./render-diagnostics.cjs');
const {chooseConcurrency} = require('./render-resources.cjs');
const os = require('node:os');
const {encodingPolicy,encoderOptions,gpuFailure,cpuFallback,withCpuFallback}=require('./render-acceleration.cjs');
const {framePolicy,withFrameRecovery}=require('./render-recovery.cjs');

function dependencies(root) {
  const req = createRequire(path.join(root, 'package.json'));
  const renderer = req('@remotion/renderer');
  const bundler = req('@remotion/bundler');
  const names = ['remotion', '@remotion/renderer', '@remotion/bundler'];
  const versions = names.map(n => req(n + '/package.json').version);
  if (new Set(versions).size !== 1) throw new Error('Remotion package versions must match: ' + versions.join(', '));
  if (!versions[0].startsWith('4.')) throw new Error('This adapter expects Remotion 4.x. Found ' + versions[0]);
  return {req, renderer, bundler, version: versions[0]};
}
function validate(props) {
  if (props.sourceAudio !== 'discard' || !Array.isArray(props.clips) || props.clips.length < 2) {
    throw new Error('Invalid input or source audio is not muted');
  }
  if (props.topText?.enabled && typeof props.topText.text !== 'string') {
    throw new Error('Invalid top text');
  }
  if (props.topTextVisual) {
    const v=props.topTextVisual;
    if (typeof v.src!=='string' || !v.src ||
        !['left','top','width','height'].every(k=>Number.isFinite(v[k])) ||
        v.left<0 || v.top<0 || v.width<=0 || v.height<=0 ||
        v.left+v.width>props.width+1 || v.top+v.height>props.height+1) {
      throw new Error('Invalid frozen top-text overlay');
    }
  }
  if (props.topText?.windows) {
    if (!Array.isArray(props.topText.windows) || props.topText.windows.some(
        window => !Number.isSafeInteger(window.start_frame) ||
          !Number.isSafeInteger(window.end_frame) || window.start_frame < 0 ||
          window.end_frame <= window.start_frame || window.end_frame > props.durationInFrames)) {
      throw new Error('Invalid top-text time window');
    }
  }
  if (props.subtitles) {
    if (!Array.isArray(props.subtitles)) throw new Error('Invalid subtitles');
    for (const caption of props.subtitles) {
      if (typeof caption.text !== 'string' || !Number.isSafeInteger(caption.start_frame) ||
          !Number.isSafeInteger(caption.end_frame) || caption.start_frame < 0 ||
          caption.end_frame > props.durationInFrames || caption.end_frame <= caption.start_frame) {
        throw new Error('Invalid subtitle timing');
      }
      if (caption.lines) {
        if(caption.revision==='smart_caption_v1') {
          if(caption.lines.length<1||caption.lines.length>2||caption.lines.some(x=>!x)||caption.lines.join('')!==caption.text||
             caption.line_widths.some(x=>!Number.isFinite(x)||x>caption.layout.available_width+.5)||!caption.animation||caption.animation.stable_frames<0)
            throw new Error('Smart subtitle frozen layout/animation QC failed');
          continue;
        }
        if (!Array.isArray(caption.lines) || caption.lines.length < 1 || caption.lines.length > 2 ||
            caption.lines.some(line => typeof line !== 'string' || [...line].length < 2) ||
            caption.lines.join('') !== caption.text ||
            !Array.isArray(caption.line_widths_px) ||
            caption.line_widths_px.some(value => !Number.isFinite(value) || value > caption.safe_width_px)) {
          throw new Error('Subtitle layout gate failed: over two lines, unsafe width or orphan character');
        }
      }
    }
  }
  let offset = 0;
  const advanced = Boolean(props.timelineRevision);
  const validTypes = ['none', 'fade', 'slide_left', 'slide_right', 'wipe_left', 'wipe_right', 'blur_fade'];
  if (advanced && props.transitions?.length !== props.clips.length - 1) throw new Error('Invalid transition count');
  let index = 0;
  for (const c of props.clips) {
    if (!Number.isSafeInteger(c.frames) || c.frames <= 0 || c.from !== offset) throw new Error('Invalid frame schedule');
    const rate = c.playbackRate ?? 1;
    if (!Number.isFinite(rate) || rate < 0.5 || rate > 2) throw new Error('Invalid playback rate');
    if (!Number.isFinite(c.sourceStartFrame ?? 0) || (c.sourceStartFrame ?? 0) < 0) throw new Error('Invalid source window start');
    if (!Number.isFinite(c.sourceEndSeconds ?? 0) || (c.sourceEndSeconds ?? 0) < 0) throw new Error('Invalid source window end');
    if (c.sourceEndSeconds && ((c.sourceStartFrame ?? 0) / props.fps + c.frames * rate / props.fps > c.sourceEndSeconds + 1 / props.fps + 1e-6)) throw new Error('Source window exceeds selected range');
    if (!advanced && rate !== (props.videoProcessing?.speed ?? 1)) throw new Error('Playback rate must match export settings');
    if (!c.src.startsWith('http://127.0.0.1:')) throw new Error('Only temporary local media URLs are accepted');
    let overlap = 0;
    if (advanced) {
      const incoming = c.incomingTransition ?? {type:'none',frames:0};
      const outgoing = c.outgoingTransition ?? {type:'none',frames:0};
      for (const t of [incoming,outgoing]) {
        if (!validTypes.includes(t.type) || !Number.isSafeInteger(t.frames) || t.frames < 0) throw new Error('Invalid transition');
      }
      if (incoming.frames + outgoing.frames >= c.frames) throw new Error('Transition overlaps consume clip');
      if (index === 0 && incoming.frames !== 0) throw new Error('Unexpected first transition');
      if (index === props.clips.length - 1 && outgoing.frames !== 0) throw new Error('Unexpected last transition');
      if (index) {
        const previous = props.clips[index-1].outgoingTransition;
        if (incoming.frames !== previous.frames || incoming.type !== previous.type) throw new Error('Inconsistent overlap');
      }
      if (index < props.clips.length-1) {
        const t = props.transitions[index];
        if (t.frames !== outgoing.frames || t.type !== outgoing.type || t.start_frame !== c.from+c.frames-t.frames) {
          throw new Error('Transition manifest mismatch');
        }
      }
      overlap = outgoing.frames;
    }
    offset += c.frames - overlap;
    index += 1;
  }
  if (offset !== props.durationInFrames) throw new Error('Whole-clip total does not match');
}
async function bundleFor(request,d) {
  const entry = path.join(__dirname, 'src', 'index.jsx');
  const key = crypto.createHash('sha256').update(fs.readdirSync(path.dirname(entry)).sort().map(name=>fs.readFileSync(path.join(path.dirname(entry),name))).join('\n')).update(d.version).update(request.dependencyRoot).digest('hex').slice(0, 20);
  const target = path.join(__dirname, '.bundle-cache', key);
  fs.mkdirSync(path.dirname(target), {recursive: true});
  let serveUrl = target;
  if (!fs.existsSync(path.join(target, 'index.html')) || !fs.existsSync(path.join(target, '.ready'))) {
    // Concurrent preview/export builds publish only complete immutable bundles.
    const staging = target + '-' + process.pid + '-' + crypto.randomUUID();
    console.log('ASSEMBLY:正在准备通用Remotion模板（仅首次或版本变化时）。');
    serveUrl = await d.bundler.bundle({
      entryPoint: entry,
      outDir: staging,
      webpackOverride: config => ({
        ...config,
        resolve: {
          ...config.resolve,
          alias: {
            ...(config.resolve?.alias ?? {}),
            'react': path.dirname(d.req.resolve('react/package.json')),
            'react-dom': path.dirname(d.req.resolve('react-dom/package.json')),
            'remotion': path.dirname(d.req.resolve('remotion/package.json')),
          },
          modules: [path.join(request.dependencyRoot, 'node_modules'), ...(config.resolve?.modules ?? ['node_modules'])],
        },
      }),
    });
    fs.writeFileSync(path.join(staging, '.ready'), d.version);
    try { fs.renameSync(staging,target); serveUrl=target; }
    catch(error) {
      // Another builder may have completed first. Keep its published bundle.
      if (!fs.existsSync(path.join(target,'.ready'))) throw error;
      serveUrl=target;
      if (path.dirname(staging)===path.dirname(target) && staging.startsWith(target+'-')) fs.rmSync(staging,{recursive:true});
    }
  }
  return serveUrl;
}
async function main() {
  if (process.argv[2] === '--check') {
    const d = dependencies(path.resolve(process.argv[3]));
    console.log('READY Remotion ' + d.version);
    return;
  }
  const started = performance.now();
  // Keep Chromium/FFmpeg intermediate files on the user's designated data drive.
  const tempDirectory = process.platform === 'win32' ? 'D:\\GPTData\\temp\\assembler_remotion' : path.join(process.env.ASSEMBLER_TEMP || os.tmpdir(), 'assembler_remotion');
  fs.mkdirSync(tempDirectory, {recursive: true});
  process.env.TEMP = tempDirectory;
  process.env.TMP = tempDirectory;
  const request = JSON.parse(fs.readFileSync(process.argv[2], 'utf8').replace(/^\uFEFF/, ''));
  const requestedConcurrency = Math.max(1, Number(request.concurrency) || 4);
  const resourcePolicy = chooseConcurrency(requestedConcurrency, {freeMemoryBytes: os.freemem(),
    width: request.props.width, height: request.props.height, exact: request.diagnostics?.exactConcurrency === true});
  let concurrency = resourcePolicy.effective;
  let execution=framePolicy(concurrency);
  request.requestedConcurrency = requestedConcurrency;
  request.concurrency = concurrency;
  request.props.renderDiagnostics = {jobId: request.diagnostics?.jobId ?? null};
  const diagnostics = createDiagnostics(request, path.dirname(process.argv[2]));
  diagnostics.log('render_resource_policy',resourcePolicy);
  try {
  const videoCacheBytes = 512 * 1024 * 1024;
  const d = dependencies(request.dependencyRoot);
  validate(request.props);
  diagnostics.stage('bundling');
  const serveUrl = await bundleFor(request,d);
  const browserOptions = request.browser ? {browserExecutable: request.browser, chromeMode: 'chrome-for-testing'} : {};
  let encoding=encodingPolicy(request,d);
  const gpuBrowser=()=>encoding.gl?{...browserOptions,chromiumOptions:{gl:encoding.gl}}:browserOptions;
  diagnostics.log('render_acceleration_policy',encoding);
  console.log(`ASSEMBLY:视频加速：${encoding.encoder==='h264_nvenc'?'NVIDIA显卡编码 + ANGLE画面加速':'CPU软件编码'}${encoding.reason?'；'+encoding.reason:''}`);
  diagnostics.stage('composition_selection');
  let composition;
  try {
    composition=await d.renderer.selectComposition({serveUrl,id:'WholeClipAssembly',inputProps:request.props,onBrowserLog:diagnostics.browserLog,timeoutInMilliseconds:execution.timeoutInMilliseconds,...gpuBrowser()});
  } catch(error) {
    if (encoding.encoder!=='h264_nvenc'||!gpuFailure(error)) throw error;
    encoding=cpuFallback(encoding,error);
    console.log('ASSEMBLY:显卡初始化失败，自动使用CPU兼容路径。');
    diagnostics.log('render_acceleration_fallback',encoding);
    composition=await d.renderer.selectComposition({serveUrl,id:'WholeClipAssembly',inputProps:request.props,onBrowserLog:diagnostics.browserLog,timeoutInMilliseconds:execution.timeoutInMilliseconds,...browserOptions});
  }
  let lastPercent = -1;
  let lastProgress = {};
  const renderStarted = performance.now();
  console.log(`ASSEMBLY:渲染设置：请求并发 ${requestedConcurrency}，实际并发 ${concurrency}；可用内存 ${(resourcePolicy.free_memory_bytes/1024**3).toFixed(2)}GiB；视频帧缓存上限 512MB。`);
  diagnostics.stage('rendering');
  const render=selected=>{encoding=selected;return d.renderer.renderMedia({
    serveUrl,
    composition,
    inputProps: request.props,
    outputLocation: request.output,
    codec: 'h264',
    pixelFormat: 'yuv420p',
    ...encoderOptions(encoding),
    concurrency,
    timeoutInMilliseconds: execution.timeoutInMilliseconds,
    offthreadVideoThreads: execution.offthreadVideoThreads,
    logLevel: 'verbose',
    onBrowserLog: diagnostics.browserLog,
    offthreadVideoCacheSizeInBytes: videoCacheBytes,
    // Second protection: the intermediate render has NO audio stream at all.
    muted: true,
    overwrite: true,
    ...(request.diagnostics?.benchmarkOnly===true&&request.diagnostics?.frameRange?
      {frameRange:request.diagnostics.frameRange}:{}),
    ...gpuBrowser(),
    onProgress: (details) => {
      lastProgress = details;
      const {progress} = details;
      const percent = Math.floor(progress * 100);
      if (percent >= lastPercent + 5 || percent === 100) {
        console.log('ASSEMBLY:Remotion静音画面渲染 ' + percent + '%');
        lastPercent = percent;
      }
    },
  });};
  const recovered=await withFrameRecovery(execution,async(policy,attempt)=>{
    execution=policy;concurrency=policy.concurrency;request.concurrency=concurrency;
    diagnostics.beginAttempt(attempt);diagnostics.log('render_frame_wait_policy',policy);
    return withCpuFallback(encoding,render,fallback=>{
      diagnostics.log('render_acceleration_fallback',fallback);
      console.log('ASSEMBLY:显卡编码/驱动失败，自动用CPU重新生成当前临时画面；冻结方案保持。');
      lastPercent=-1;
    });
  },{onFailure:error=>{
    const failure=diagnostics.failure(error);
    return !failure.requests.some(row=>row.proxy_status>=400);
  },onRetry:policy=>{
    console.log('ASSEMBLY:本条取视频帧超时，自动降为单并发重新渲染一次；镜头、字幕和声音保持。');
    diagnostics.log('render_frame_wait_retry',policy);lastPercent=-1;
  }});
  const rendered=recovered.result;
  if(recovered.attempts>1)diagnostics.recovered();
  encoding=rendered.policy;
  const seconds = milliseconds => Math.round(milliseconds) / 1000;
  const metrics = {
    schema_version: 'render_performance_v1',
    concurrency,
    requested_concurrency: requestedConcurrency,
    resource_policy: resourcePolicy,
    frame_wait_policy: recovered.policy,
    render_attempts: recovered.attempts,
    video_cache_bytes: videoCacheBytes,
    temp_directory: tempDirectory,
    encoding_policy: encoding,
    setup_seconds: seconds(renderStarted - started),
    render_seconds: seconds(performance.now() - renderStarted),
    total_seconds: seconds(performance.now() - started),
    frames_seconds: lastProgress.renderedDoneIn == null ? null : seconds(lastProgress.renderedDoneIn),
    final_stitch_seconds: lastProgress.encodedDoneIn == null ? null : seconds(lastProgress.encodedDoneIn),
    rendered_frames: lastProgress.renderedFrames,
    encoded_frames: lastProgress.encodedFrames,
    note: '画面阶段包含流水编码等待；收尾不是全部编码耗时，子项不与总耗时相加。',
  };
  console.log(`ASSEMBLY:渲染耗时：准备 ${metrics.setup_seconds.toFixed(1)}秒；画面及流水编码 ${metrics.frames_seconds ?? '未知'}秒；编码封装收尾 ${metrics.final_stitch_seconds ?? '未知'}秒；总计 ${metrics.total_seconds.toFixed(1)}秒。`);
  try {
    fs.writeFileSync(path.join(path.dirname(process.argv[2]), 'render_performance.json'), JSON.stringify(metrics, null, 2));
  } catch (error) {
    console.warn('ASSEMBLY:耗时报告未能保存：' + error.message);
  }
  console.log('ASSEMBLY:静音画面完成，下一步只封装BGM音轨。');
  diagnostics.stage('render_complete');
  } catch (error) {
    diagnostics.failure(error);
    throw error;
  } finally {
    diagnostics.close();
  }
}
if (require.main === module) main().catch(error => {console.error(error.stack || String(error)); process.exitCode = 1;});
module.exports = {validate, dependencies, bundleFor, main};
