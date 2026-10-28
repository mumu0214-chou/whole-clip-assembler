"""In-memory conditioning LRU and measured stages around unchanged official inference."""
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
import ast
import hashlib
import inspect
import os
from pathlib import Path
import textwrap
import time

ACTIVE = ContextVar('indextts_performance', default=None)


class Stages:
    def __init__(self, synchronize=lambda:None):
        self.seconds = {}
        self.synchronize = synchronize

    @contextmanager
    def stage(self, name):
        self.synchronize()
        started = time.perf_counter()
        try:
            yield
        finally:
            self.synchronize()
            self.seconds[name] = self.seconds.get(name, 0.) + time.perf_counter()-started


@contextmanager
def measured(name):
    active = ACTIVE.get()
    if active is None:
        yield
    else:
        with active.stage(name):
            yield


def timed_call(name, function, *args, **kwargs):
    with measured(name):
        return function(*args, **kwargs)


def instrument_official(module):
    """AST adds timing scopes only; never edits installed model/vendor source files."""
    cls = module.IndexTTS2
    source = textwrap.dedent(inspect.getsource(cls.infer_generator))
    tree = ast.parse(source)
    function = tree.body[0]
    counts = {'speaker':0, 'emotion':0, 'text':0}

    def scope(name, body):
        return ast.With(items=[ast.withitem(context_expr=ast.Call(
            func=ast.Name(id='_runtime_measured',ctx=ast.Load()),
            args=[ast.Constant(name)],keywords=[]))],body=body)

    for index,node in enumerate(function.body):
        if isinstance(node,ast.If):
            condition = ast.unparse(node.test)
            if condition.startswith('self.cache_spk_cond is None'):
                function.body[index] = scope('speaker_reference_preprocessing',[node]);counts['speaker']+=1
            elif condition.startswith('self.cache_emo_cond is None'):
                function.body[index] = scope('emotion_reference_preprocessing',[node]);counts['emotion']+=1
    begin = next(i for i,n in enumerate(function.body) if isinstance(n,ast.Assign) and
                 any(isinstance(t,ast.Name) and t.id=='lang_prefix' for t in n.targets))
    end = next(i for i,n in enumerate(function.body) if isinstance(n,ast.Assign) and
               any(isinstance(t,ast.Name) and t.id=='do_sample' for t in n.targets))
    function.body[begin:end] = [scope('text_preprocessing',function.body[begin:end])]
    counts['text']+=1

    class Calls(ast.NodeTransformer):
        def visit_Call(self,node):
            self.generic_visit(node)
            expression = ast.unparse(node.func)
            stage = ('waveform_generation' if expression=='self.bigvgan' else
                     'core_inference' if expression in (
                         'self.gpt.inference_speech','self.semantic_codec.decode',
                         "self.s2mel.models['length_regulator']",
                         "self.s2mel.models['cfm'].inference") else None)
            if stage:
                return ast.copy_location(ast.Call(func=ast.Name(id='_runtime_timed_call',ctx=ast.Load()),
                      args=[ast.Constant(stage),node.func,*node.args],keywords=node.keywords),node)
            return node

    tree = Calls().visit(tree)
    if counts!={'speaker':1,'emotion':1,'text':1}:
        raise RuntimeError('Official inference structure changed; stage instrumentation must be reviewed')
    namespace = dict(module.__dict__, _runtime_measured=measured, _runtime_timed_call=timed_call)
    exec(compile(ast.fix_missing_locations(tree),inspect.getsourcefile(cls)+'::timings','exec'),namespace)
    cls.infer_generator = namespace['infer_generator']
    original_save = module.save_pcm_wav
    module.save_pcm_wav = lambda *a,**kw:timed_call('wav_write',original_save,*a,**kw)
    # The generated function has its own global namespace.
    namespace['save_pcm_wav'] = module.save_pcm_wav


def _map_tensors(value, device):
    if hasattr(value,'detach') and hasattr(value,'to'):
        return value.detach().to(device)
    if isinstance(value,tuple):return tuple(_map_tensors(v,device) for v in value)
    if isinstance(value,list):return [_map_tensors(v,device) for v in value]
    if isinstance(value,dict):return {k:_map_tensors(v,device) for k,v in value.items()}
    return value


class ReferenceRuntime:
    SPEAKER = ('cache_spk_cond','cache_s2mel_style','cache_s2mel_prompt','cache_mel')
    def __init__(self, model, enabled=True, capacity=6):
        self.model=model;self.enabled=enabled;self.capacity=capacity
        self.speakers=OrderedDict();self.emotions=OrderedDict();self.hashes={}

    def key(self,path):
        path=Path(path).resolve();stat=path.stat()
        identity=(os.path.normcase(str(path)),stat.st_size,stat.st_mtime_ns)
        if identity not in self.hashes:
            self.hashes={k:v for k,v in self.hashes.items() if k[0]!=identity[0]}
            self.hashes[identity]=hashlib.sha256(path.read_bytes()).hexdigest()
        return identity+(self.hashes[identity],)

    def _keep(self,cache,key,value):
        cache[key]=_map_tensors(value,'cpu');cache.move_to_end(key)
        while len(cache)>self.capacity:cache.popitem(last=False)

    def before(self,speaker,emotion):
        sk,ek=self.key(speaker),self.key(emotion)
        spk_hit=self.model.cache_spk_cond is not None and self.model.cache_spk_audio_prompt==str(speaker)
        emo_hit=self.model.cache_emo_cond is not None and self.model.cache_emo_audio_prompt==str(emotion)
        # File content changes invalidate the official path-only cache as well.
        if getattr(self,'last_speaker',None) not in (None,sk):spk_hit=False
        if getattr(self,'last_emotion',None) not in (None,ek):emo_hit=False
        if self.enabled and sk in self.speakers:
            values=_map_tensors(self.speakers[sk],self.model.device)
            for name,value in zip(self.SPEAKER,values):setattr(self.model,name,value)
            self.model.cache_spk_audio_prompt=str(speaker);spk_hit=True
        elif not spk_hit:
            self.model.cache_spk_cond=None
        if self.enabled and ek in self.emotions:
            self.model.cache_emo_cond=_map_tensors(self.emotions[ek],self.model.device)
            self.model.cache_emo_audio_prompt=str(emotion);emo_hit=True
        elif not emo_hit:
            self.model.cache_emo_cond=None
        self.last_speaker,self.last_emotion=sk,ek
        return {'speaker_cache_hit':spk_hit,'emotion_cache_hit':emo_hit,'speaker_key':sk[-1],'emotion_key':ek[-1]}

    def after(self):
        if self.enabled:
            self._keep(self.speakers,self.last_speaker,tuple(getattr(self.model,k) for k in self.SPEAKER))
            self._keep(self.emotions,self.last_emotion,self.model.cache_emo_cond)


def bounded_cache(function, capacity=256):
    cache=OrderedDict()
    def call(*args,**kwargs):
        key=repr((args,sorted(kwargs.items())))
        if key not in cache:
            cache[key]=function(*args,**kwargs)
            if len(cache)>capacity:cache.popitem(last=False)
        cache.move_to_end(key)
        result=cache[key]
        return list(result) if isinstance(result,list) else result
    return call


class TokenizerCache:
    def __init__(self,tokenizer):
        self.original=tokenizer;self.encode=bounded_cache(tokenizer.encode)
    def __getattr__(self,name):return getattr(self.original,name)
