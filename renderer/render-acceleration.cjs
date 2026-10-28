'use strict';
// Execution policy only: never modifies the frozen frames, effects or audio.
const path = require('node:path');
const child = require('node:child_process');
const fs = require('node:fs');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
function testFrame() {
  // Uniform 256x256 RGB PNG, above NVENC's minimum dimensions. The bundled
  // FFmpeg omits rawvideo demuxing, so use Remotion's PNG pipe format.
  const crc=bytes=>{
    let value=0xffffffff;
    for (const b of bytes) {value^=b;for(let j=0;j<8;j++) value=(value>>>1)^((value&1)?0xedb88320:0);}
    return (value^0xffffffff)>>>0;
  };
  const chunk=(type,data)=>{
    const name=Buffer.from(type);const length=Buffer.alloc(4);length.writeUInt32BE(data.length);
    const checksum=Buffer.alloc(4);checksum.writeUInt32BE(crc(Buffer.concat([name,data])));
    return Buffer.concat([length,name,data,checksum]);
  };
  const header=Buffer.alloc(13);header.writeUInt32BE(256,0);header.writeUInt32BE(256,4);header[8]=8;header[9]=2;
  const row=Buffer.alloc(256*3+1,128);row[0]=0;
  return Buffer.concat([Buffer.from('89504e470d0a1a0a','hex'),chunk('IHDR',header),
    chunk('IDAT',zlib.deflateSync(Buffer.concat(Array(256).fill(row)))),chunk('IEND',Buffer.alloc(0))]);
}

function nvencArgs(args, quality) {
  if (!args.includes('h264_nvenc')) return args;
  const clean=[];
  for (let i=0;i<args.length;i++) {
    if (['-preset','-crf','-b:v','-rc','-cq'].includes(args[i])) {i++;continue;}
    clean.push(args[i]);
  }
  const output=clean.pop();
  return [...clean,'-preset','p5','-rc','vbr','-cq',String(quality),'-b:v','0',output];
}

function probeNvenc(d, run=child.spawnSync) {
  if (process.platform !== 'win32') return {available:false, reason:'NVENC is not configured on this platform; CPU encoding is used.'};
  let output;
  try {
    const dist=path.dirname(d.req.resolve('@remotion/renderer'));
    const {getExecutablePath}=require(path.join(dist,'compositor','get-executable-path.js'));
    const executable=getExecutablePath({type:'ffmpeg',indent:false,logLevel:'error',binariesDirectory:null});
    // Listing encoders is insufficient. Initialize the actual GPU encoder using
    // eight test frames. Delete only this new tiny probe, never user media.
    const root=path.resolve('D:\\GPTData\\temp\\assembler_nvenc_probe');
    fs.mkdirSync(root,{recursive:true});
    output=path.join(root,crypto.randomUUID()+'.mp4');
    const args=['-hide_banner','-loglevel','error','-r','30','-f','image2pipe','-s','256x256',
      '-vcodec','png','-i','pipe:0','-frames:v','8','-an',
      '-c:v','h264_nvenc','-preset','p5','-rc','vbr','-cq','16','-b:v','0','-pix_fmt','yuv420p',output];
    const cwd=path.dirname(executable);
    const result=run(executable,args,{input:Buffer.concat(Array(8).fill(testFrame())),timeout:15000,
      cwd,windowsHide:true,env:{...process.env,PATH:cwd+path.delimiter+(process.env.PATH??'')}});
    return {available:result.status===0&&!result.error,binary:executable,
      reason:result.status===0&&!result.error?null:String(result.error?.message??result.stderr??'GPU encoder probe failed').slice(-1200)};
  } catch(error) {return {available:false,reason:String(error.message).slice(-1200)};}
  finally {if (output&&path.dirname(output)===path.resolve('D:\\GPTData\\temp\\assembler_nvenc_probe')) {
    try {fs.unlinkSync(output);} catch(error) {if(error.code!=='ENOENT') console.warn('GPU probe cleanup: '+error.message);}
  }}
}

function encodingPolicy(request,d,probe=probeNvenc) {
  const mode=request.acceleration??'auto';
  if (!['auto','cpu'].includes(mode)) throw new Error('Invalid render acceleration mode');
  const crf=Number(request.crf??18);
  if (!Number.isFinite(crf)||crf<0||crf>40) throw new Error('Invalid render quality');
  if (mode==='cpu') return {mode,encoder:'libx264',gl:null,crf,reason:'CPU comparison or compatibility mode'};
  const test=probe(d);
  if (!test.available) return {mode,encoder:'libx264',gl:null,crf,reason:test.reason,probe:test};
  // CQ and CRF are different scales. Bias toward quality rather than assuming
  // their values are interchangeable; leave the saved CRF field untouched.
  return {mode,encoder:'h264_nvenc',gl:'angle',cq:Math.max(1,crf-2),crf,probe:test};
}

function encoderOptions(policy) {
  if (policy.encoder==='libx264') return {crf:policy.crf,x264Preset:'fast',hardwareAcceleration:'disable'};
  return {hardwareAcceleration:'required',
    ffmpegOverride:({args})=>nvencArgs(args,policy.cq)};
}

function gpuFailure(error) {
  return /nvenc|nvcuda|cuda.*(?:error|fail)|(?:hardware|encoder).*initializ|Direct3D|ANGLE|gpu process|GL renderer|eglInitialize/i.test(String(error.stack??error));
}

function cpuFallback(policy,error) {
  return {mode:policy.mode,encoder:'libx264',gl:null,crf:policy.crf,
    reason:String(error.message??error).slice(-1200),probe:policy.probe,fallback_from:'h264_nvenc'};
}
async function withCpuFallback(policy,operation,onFallback=()=>{}) {
  try {return {result:await operation(policy),policy};}
  catch(error) {
    if (policy.encoder!=='h264_nvenc'||!gpuFailure(error)) throw error;
    const fallback=cpuFallback(policy,error);onFallback(fallback,error);
    return {result:await operation(fallback),policy:fallback};
  }
}
module.exports={probeNvenc,encodingPolicy,encoderOptions,nvencArgs,gpuFailure,cpuFallback,withCpuFallback};
