'use strict';
// Pure frame math: no clocks, random state, CSS animation or timers.
const clamp=(x)=>Math.max(0,Math.min(1,x));
function ease(x,kind){x=clamp(x);return kind==='ease_in'?x*x:kind==='ease_out'?1-(1-x)**3:kind==='ease_in_out'?x*x*(3-2*x):x;}
function animationAt(cue,frame,fps,unit=1){
  const a=cue.animation;const state={opacity:1,x:0,y:0,scale:1,reveal:1,clip:1,brightness:1};
  if(!a)return state;
  const local=frame-cue.start_frame;const length=cue.end_frame-cue.start_frame;
  const enter=a.entry,exit=a.exit,loop=a.loop;
  let entering=false,exiting=false;
  if(enter.kind!=='none'){
    const raw=clamp((local-enter.delay_frames)/Math.max(1,enter.duration_frames));
    const p=ease(raw,enter.easing);const rest=(1-p)*enter.intensity;entering=raw<1;
    if(['fade','pop','bounce','slide_up','slide_down','slide_left','slide_right'].includes(enter.kind))state.opacity=p;
    if(enter.kind==='slide_up')state.y=enter.distance*unit*rest;
    if(enter.kind==='slide_down')state.y=-enter.distance*unit*rest;
    if(enter.kind==='slide_left')state.x=-enter.distance*unit*rest;
    if(enter.kind==='slide_right')state.x=enter.distance*unit*rest;
    if(enter.kind==='pop')state.scale=1-(1-enter.scale)*rest;
    if(enter.kind==='bounce')state.y=-Math.sin(raw*Math.PI*2.5)*(1-raw)*enter.distance*unit*enter.intensity;
    if(['reveal','typewriter'].includes(enter.kind))state.reveal=raw;
    if(enter.kind==='wipe')state.clip=p;
  }
  if(exit.kind!=='none'){
    const start=length-exit.duration_frames-exit.delay_frames;
    const raw=clamp((local-start)/Math.max(1,exit.duration_frames));const p=ease(raw,exit.easing);exiting=local>=start;
    if(exiting){
      state.opacity*=exit.kind==='cut'?(raw>=.25?0:1):1-p;
      const distance=p*exit.intensity*exit.distance*unit;
      if(exit.kind==='slide_up')state.y-=distance;if(exit.kind==='slide_down')state.y+=distance;
      if(exit.kind==='slide_left')state.x-=distance;if(exit.kind==='slide_right')state.x+=distance;
      if(exit.kind==='shrink')state.scale*=1-(1-exit.scale)*p*exit.intensity;
    }
  }
  if(loop.kind!=='none'&&!entering&&!exiting){
    const stableStart=enter.duration_frames+enter.delay_frames+Math.round(loop.delay*fps);
    if(local>=stableStart){
      const wave=Math.sin((local-stableStart)/fps/loop.duration*Math.PI*2);
      if(loop.kind==='breath')state.scale*=1+(loop.scale-1)*wave*loop.intensity;
      if(loop.kind==='float')state.y+=wave*loop.distance*unit*loop.intensity;
      if(loop.kind==='bounce')state.y-=Math.max(0,wave)**2*loop.distance*unit*loop.intensity;
      if(loop.kind==='shimmer')state.brightness=1+Math.max(0,wave)*.25*loop.intensity;
    }
  }
  return state;
}
const clusters=(text)=>Array.from(new Intl.Segmenter('zh',{granularity:'grapheme'}).segment(text),x=>x.segment);
module.exports={animationAt,clusters,ease};
