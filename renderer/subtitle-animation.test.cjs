'use strict';
const assert=require('node:assert/strict');const {animationAt,clusters}=require('./src/subtitle-animation.cjs');
const entries=['none','fade','slide_up','slide_down','slide_left','slide_right','pop','bounce','reveal','typewriter','wipe'];
const exits=['none','fade','slide_down','slide_up','slide_left','slide_right','shrink','cut'];
const loops=['none','breath','float','bounce','shimmer'];
const part=(kind)=>({kind,duration_frames:6,delay_frames:0,duration:1.6,delay:0,intensity:.5,distance:28,scale:.85,direction:'left',easing:'ease_out'});
for(const entry of entries)for(const exit of exits)for(const loop of loops){
  const cue={start_frame:30,end_frame:90,animation:{entry:part(entry),exit:part(exit),loop:part(loop)}};
  for(let frame=30;frame<90;frame++){
    const state=animationAt(cue,frame,30);
    assert(Object.values(state).every(Number.isFinite));assert(state.opacity>=0&&state.opacity<=1);
    assert.deepEqual(state,animationAt(cue,frame,30));
  }
  assert.deepEqual(animationAt(cue,30,30),animationAt({...cue,start_frame:90,end_frame:150},90,30));
}
assert.deepEqual(clusters('中👩🏽‍💻🇨🇳é'),['中','👩🏽‍💻','🇨🇳','é']);
console.log('PASS all 440 animation combinations, frame determinism, cue restart and graphemes');
