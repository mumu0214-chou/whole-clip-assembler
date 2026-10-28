"""Loopback-only, persistent IndexTTS-2.5 worker with one GPU inference lock."""
from mac_paths import path as _portable_path
from pathlib import Path
import argparse, json, os, sys, threading, time, traceback, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from voice_output_paths import validate_output_path, PATH_POLICY_VERSION
from indextts_runtime import (ACTIVE, Stages, ReferenceRuntime, TokenizerCache,
                              bounded_cache, instrument_official)
BOOT_STARTED=time.perf_counter()
RUNTIME_CONFIG=Path(__file__).with_name('data')/'indextts_runtime.json'
options={'use_bf16':True,'use_cuda_kernel':False,'use_accel':False,
         'use_torch_compile':False,'runtime_cache':True}
if RUNTIME_CONFIG.is_file(): options.update(json.loads(RUNTIME_CONFIG.read_text(encoding='utf-8')))

ROOT=Path(_portable_path(r'D:\DY video\本地配音\IndexTTS-2.5\index-tts'))
os.environ.update(PYTHONUTF8='1', HF_HOME=_portable_path(r'D:\GPTData\cache\huggingface'), HF_HUB_CACHE=_portable_path(r'D:\GPTData\cache\huggingface\hub'), TORCH_HOME=_portable_path(r'D:\GPTData\cache\torch'), NLTK_DATA=_portable_path(r'D:\GPTData\cache\nltk'), MPLCONFIGDIR=_portable_path(r'D:\GPTData\cache\matplotlib'), NUMBA_CACHE_DIR=_portable_path(r'D:\GPTData\cache\numba'), TEMP=_portable_path(r'D:\GPTData\temp'), TMP=_portable_path(r'D:\GPTData\temp'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
sys.path.insert(0,str(ROOT))
state={'engine':'indextts25','version':'2.5','path_policy':PATH_POLICY_VERSION,'performance_version':'runtime_v1','runtime_options':options,'pid':os.getpid(),'ready':False,'loading':True,'error':None,'model_load_count':0,'synthesis_count':0,'qwen_load_count':0,'sampling_supported':True,'active_inference':False}
lock=threading.Lock()
tts=None
runtime=None

def load():
    global tts, runtime
    try:
        import torch
        if torch.cuda.is_available():
            device='cuda:0'
        elif sys.platform=='darwin' and torch.backends.mps.is_available():
            device='mps'
        else:
            raise RuntimeError('没有可用的 CUDA 或 Apple MPS；请检查 PyTorch 和 GPU 支持。')
        import indextts.infer_v2_5 as official
        started=time.monotonic()
        state['worker_startup_seconds']=time.perf_counter()-BOOT_STARTED
        instrument_official(official)
        tts=official.IndexTTS2(cfg_path=str(ROOT/'checkpoints/config.yaml'),model_dir=str(ROOT/'checkpoints'),device=device,use_bf16=options['use_bf16'] if device.startswith('cuda') else False,use_cuda_kernel=options['use_cuda_kernel'] if device.startswith('cuda') else False,use_deepspeed=False,use_accel=options['use_accel'] if device.startswith('cuda') else False,use_torch_compile=options['use_torch_compile'] if device.startswith('cuda') else False,use_qwen_emo=False)
        runtime=ReferenceRuntime(tts,enabled=options['runtime_cache'])
        if options['runtime_cache']:
            tts.text_process.normalize=bounded_cache(tts.text_process.normalize)
            tts.tokenizer=TokenizerCache(tts.tokenizer)
        state.update(ready=True,loading=False,model_load_count=1,gpu=torch.cuda.get_device_name(0) if device.startswith('cuda') else 'Apple MPS',device=tts.device,load_seconds=time.monotonic()-started,bf16_supported=torch.cuda.is_bf16_supported() if device.startswith('cuda') else False,effective_cuda_kernel=tts.use_cuda_kernel)
        print('WORKER READY '+json.dumps(state),flush=True)
    except Exception as exc:
        state.update(loading=False,error=str(exc))
        traceback.print_exc()

class Handler(BaseHTTPRequestHandler):
    def reply(self,code,data):
        body=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path not in ('/health','/ready'): return self.reply(404,{'error':'Unknown endpoint'})
        data=dict(state)
        if state['ready'] and str(state.get('device','')).startswith('cuda'):
            try:
                import torch
                data.update(cuda_allocated_bytes=torch.cuda.memory_allocated(),cuda_reserved_bytes=torch.cuda.memory_reserved())
            except Exception as exc:data['resource_error']=str(exc)
        self.reply(200 if self.path=='/health' or state['ready'] else 503,data)
    def do_POST(self):
        inference_owned=False
        if self.path!='/synthesize': return self.reply(404,{'error':'Unknown endpoint'})
        if not state['ready']: return self.reply(503,dict(state))
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=131072: raise ValueError('Invalid request size')
            req=json.loads(self.rfile.read(length))
            text=str(req['text']).strip()
            if not text or len(text)>10000: raise ValueError('Invalid text length')
            ref=Path(req['reference_path']).resolve()
            if not ref.is_file(): raise ValueError('Speaker reference missing')
            print('SYNTHESIS OUTPUT '+str(Path(req['output_path']).resolve()),flush=True)
            output=validate_output_path(req['output_path'])
            speed=float(req.get('speed',1.0)); alpha=float(req.get('emotion_alpha',1.0))
            if not .5<=speed<=2 or not 0<=alpha<=1: raise ValueError('Invalid speed or emotion intensity')
            mode=req.get('emotion_mode','speaker')
            kwargs={'duration_factor':1/speed,'interval_silence':int(float(req.get('pause',.15))*1000),'emo_alpha':alpha}
            if mode=='audio':
                emo=Path(req.get('emotion_reference_path','')).resolve()
                if not emo.is_file(): raise ValueError('Emotion audio reference missing')
                kwargs['emo_audio_prompt']=str(emo)
            elif mode=='text': kwargs.update(use_emo_text=True,emo_text=str(req.get('emotion_text','')).strip() or None)
            elif mode=='vector':
                vec=req.get('emotion_vector')
                if not isinstance(vec,list) or len(vec)!=8 or any(not isinstance(v,(int,float)) or not 0<=v<=1 for v in vec): raise ValueError('Emotion vector needs 8 values in [0,1]')
                kwargs['emo_vector']=vec
            elif mode!='speaker': raise ValueError('Unknown emotion mode')
            output.parent.mkdir(parents=True,exist_ok=True)
            temporary=output.with_name(output.stem+'.'+uuid.uuid4().hex+'.part.wav')
            with lock:
                import torch, soundfile as sf
                if output.exists(): raise ValueError('Refusing to overwrite audio created by an earlier queued request')
                state['active_inference']=True
                inference_owned=True
                started=time.monotonic()
                is_cuda=str(state['device']).startswith('cuda')
                if is_cuda: torch.cuda.reset_peak_memory_stats()
                synchronize=torch.cuda.synchronize if is_cuda else torch.mps.synchronize
                stages=Stages(synchronize)
                if mode=='text' and tts.qwen_emo is None:
                    from unittest.mock import patch
                    import indextts.infer_v2_5 as official
                    original=official.AutoModelForCausalLM.from_pretrained
                    with stages.stage('emotion_text_model_load'):
                        with patch.object(official.AutoModelForCausalLM,'from_pretrained',side_effect=lambda *a,**kw:original(*a,**dict(kw,device_map='cpu'))):
                            tts.qwen_emo=official.QwenEmotion(str(ROOT/'checkpoints/qwen0.6bemo4-merge'))
                    state['qwen_load_count']+=1
                if req.get('sampling_seed') is not None:
                    import random, numpy as np
                    seed=int(req['sampling_seed']);random.seed(seed);np.random.seed(seed%(2**32));torch.manual_seed(seed)
                emotion=Path(req['emotion_reference_path']).resolve() if mode=='audio' else ref
                with stages.stage('conditioning_cache_restore'):
                    hits=runtime.before(str(ref),str(emotion))
                token=ACTIVE.set(stages)
                try:
                    tts.infer(spk_audio_prompt=str(ref),text=text,lang=str(req.get('language','ZH')).upper(),output_path=str(temporary),verbose=False,**kwargs)
                    synchronize()
                    runtime.after()
                finally:
                    ACTIVE.reset(token)
                    state['active_inference']=False
                info=sf.info(temporary)
                if info.frames<=0: raise RuntimeError('Generated WAV is empty')
                temporary.replace(output)
                state['synthesis_count']+=1
                elapsed=time.monotonic()-started
                result={'engine':'indextts25','output_path':str(output),'duration':info.duration,'sample_rate':info.samplerate,'generation_seconds':elapsed,'elapsed_seconds':elapsed,'gpu':state['gpu'],'cuda_peak_allocated':torch.cuda.max_memory_allocated() if is_cuda else None,'worker_pid':os.getpid(),'model_load_count':state['model_load_count'],'duration_factor':1/speed,'emotion_mode':mode,'emotion_alpha':alpha,
                        'video_id':req.get('video_id'),'job_id':req.get('job_id'),'script_id':req.get('script_id'),'voice_id':req.get('voice_id'),'text_sha256':__import__('hashlib').sha256(text.encode()).hexdigest(),'speaker_reference':str(ref),'emotion_reference':str(emotion),'text_length':len(text),
                        'worker_startup_seconds':state['worker_startup_seconds'],'model_first_load_seconds':state['load_seconds'],'sampling_seed':req.get('sampling_seed'),'request_index':state['synthesis_count'],'stages':stages.seconds,**hits}
                performance=Path(__file__).with_name('data')/'tts_performance.jsonl'
                with performance.open('a',encoding='utf-8') as trace:trace.write(json.dumps(result,ensure_ascii=False)+'\n')
            self.reply(200,result)
        except Exception as exc:
            if inference_owned:state['active_inference']=False
            if 'temporary' in locals():temporary.unlink(missing_ok=True)
            traceback.print_exc(); self.reply(400,{'engine':'indextts25','error':str(exc),'traceback':traceback.format_exc(),'job_id':req.get('job_id') if 'req' in locals() else None,'video_id':req.get('video_id') if 'req' in locals() else None})

if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=9881); args=parser.parse_args()
    server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
    threading.Thread(target=load,daemon=True).start()
    print(f'IndexTTS-2.5 worker 127.0.0.1:{args.port}',flush=True)
    server.serve_forever()
