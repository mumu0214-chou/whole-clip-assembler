import React from 'react';
import {useCurrentFrame,useVideoConfig,interpolate} from 'remotion';

export const EditorialAccents=({snapshot})=>{
  const frame=useCurrentFrame();const {width,height}=useVideoConfig();
  const event=snapshot?.events?.find(e=>frame>=e.start_frame&&frame<e.end_frame);
  if(!event)return null;
  const local=frame-event.start_frame;
  const scale=interpolate(local,[0,5,10],[.88,1.04,1],{extrapolateRight:'clamp'});
  const opacity=Math.min(1,(local+1)/4,(event.end_frame-frame)/5);
  // Self-drawn reaction face, no external meme or web font dependency.
  return <div style={{position:'absolute',right:width*.07,top:height*.17,zIndex:24,
    transform:`scale(${scale})`,opacity,transformOrigin:'right center',display:'flex',alignItems:'center',gap:12,
    color:'#19242d',background:'#ffdc54',border:'4px solid #19242d',borderRadius:24,padding:'12px 20px',
    fontFamily:'Microsoft YaHei',fontSize:height*.029,fontWeight:900,boxShadow:'5px 6px 0 rgba(0,0,0,.7)'}}>
    {event.kind==='reaction'&&<svg width={height*.045} height={height*.045} viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="45" fill="#fff6be" stroke="#19242d" strokeWidth="5"/>
      <ellipse cx="34" cy="38" rx="5" ry="9"/><ellipse cx="66" cy="38" rx="5" ry="9"/>
      <ellipse cx="50" cy="67" rx="12" ry="15" fill="#19242d"/>
    </svg>}{event.text}
  </div>;
};

export function accentScale(snapshot,clip,frame){
  if(snapshot?.picture_accents===false)return 1;
  if(snapshot?.mode==='appetite')return 1+.025*frame/Math.max(1,clip.frames-1);
  const e=snapshot?.events?.find(e=>e.start_frame>=clip.from&&e.start_frame<clip.from+clip.frames&&frame+clip.from>=e.start_frame&&frame+clip.from<e.end_frame);
  if(!e)return 1;
  return interpolate(frame+clip.from,[e.start_frame,e.start_frame+6,e.end_frame-6,e.end_frame],[1,1.065,1.065,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'});
}
