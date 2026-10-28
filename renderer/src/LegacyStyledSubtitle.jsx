import React, {useEffect, useLayoutEffect, useRef, useState} from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig, delayRender, continueRender, cancelRender} from 'remotion';

const rgba = (hex, alpha) => {
  const c=hex.replace('#',''); return `rgba(${parseInt(c.slice(0,2),16)},${parseInt(c.slice(2,4),16)},${parseInt(c.slice(4,6),16)},${alpha})`;
};
const loaded = new Map();
export const StyledSubtitle = ({subtitles, style:s, topReserved=0}) => {
  const {width,height}=useVideoConfig(); const frame=useCurrentFrame();
  const cue=(subtitles??[]).find(c=>frame>=c.start_frame && frame<c.end_frame);
  const box=useRef(null); const [fontReady,setFontReady]=useState(false);
  const [handle]=useState(()=>delayRender('加载字幕字体'));
  const alias=s.fontSrc ? `caption_${s.font_sha256}` : s.font_family;
  useEffect(()=>{
    let alive=true;
    const key=s.fontSrc || s.font_family;
    if(!loaded.has(key)) loaded.set(key,(async()=>{
      if(s.fontSrc){const face=new FontFace(alias,`url("${s.fontSrc}")`);await face.load();document.fonts.add(face);}
      await document.fonts.load(`${s.bold?'800':'400'} ${s.font_size}px "${alias}"`);
      await document.fonts.ready;
    })());
    loaded.get(key).then(()=>{if(alive){setFontReady(true);continueRender(handle);}}).catch(cancelRender);
    return ()=>{alive=false;};
  },[alias,handle,s.fontSrc,s.font_family,s.bold,s.font_size]);
  const unit=height/1920;
  const margin=s.safe_horizontal*width;
  const maxWidth=Math.max(30,Math.min(width*(1-2*s.safe_horizontal),width*s.max_width));
  const outerWidth=s.bubble_width>0?Math.min(maxWidth,s.bubble_width*unit*s.scale):maxWidth;
  const center=Math.min(width-margin-outerWidth/2,Math.max(margin+outerWidth/2,width*s.x));
  const fontSize=s.font_size*unit*s.scale;
  const py=s.padding_y*unit*s.scale;
  const bubble=s.bubble!=='none';
  const effectSpace=(s.stroke_enabled?s.stroke_width:0)+
    (s.shadow_enabled?Math.max(Math.abs(s.shadow_x),Math.abs(s.shadow_y))+s.shadow_blur:0);
  const edge=Math.min(outerWidth*.15,Math.max(4,effectSpace*unit*s.scale));
  const px=Math.max(0,Math.min(s.padding_x*unit*s.scale,(outerWidth-edge*2-16*unit)/2));
  const tail=s.bubble==='speech'?20*unit*s.scale:0;
  const minTop=Math.max(edge,topReserved?topReserved+24*unit:edge);
  const maxBottom=height*(1-s.safe_bottom)-edge-tail;
  const [layout,setLayout]=useState({top:s.y*height,size:fontSize});
  useLayoutEffect(()=>{
    const el=box.current;if(!el||!cue||!fontReady)return;
    // Browser performs identical wrapping and measurement for stills and video.
    // Fit only when a large style cannot fit vertically inside the safe area.
    el.style.fontSize=`${fontSize}px`;
    let size=fontSize;
    for(let n=0;n<40 && (el.offsetHeight>maxBottom-minTop || el.scrollWidth>el.clientWidth) && size>8*unit;n++){
      size*=.9;el.style.fontSize=`${size}px`;
    }
    const top=Math.max(minTop,Math.min(s.y*height,maxBottom-el.offsetHeight));
    setLayout({top,size});
  },[cue?.text,fontReady,fontSize,outerWidth,minTop,maxBottom,s]);
  if(!cue)return null;
  const background=rgba(s.bubble_color,s.bubble_opacity);
  return <div data-caption-box="true" ref={box} style={{position:'absolute',zIndex:30,top:layout.top,
    left:center-outerWidth/2,width:outerWidth,boxSizing:'border-box',pointerEvents:'none',
    padding:`${(bubble?py:0)+edge}px ${(bubble?px:0)+edge}px`,
    minHeight:bubble?Math.min(s.bubble_height*unit*s.scale,Math.max(0,maxBottom-minTop)):undefined,
    borderRadius:bubble?s.bubble_radius*unit*s.scale:undefined,background:bubble?background:undefined,
    fontFamily:`"${alias}", sans-serif`,fontWeight:s.bold?800:400,fontStyle:s.italic?'italic':'normal',
    fontSize:layout.size,lineHeight:s.line_spacing,letterSpacing:s.letter_spacing*unit*s.scale,
    textAlign:s.align,whiteSpace:'pre-wrap',overflowWrap:'anywhere',wordBreak:'normal',
    visibility:fontReady?'visible':'hidden'}}>
    <span style={{color:s.color,opacity:s.opacity,textDecoration:s.underline?'underline':'none',
      WebkitTextStroke:s.stroke_enabled?`${s.stroke_width*unit*s.scale}px ${s.stroke_color}`:undefined,
      paintOrder:'stroke fill',textShadow:s.shadow_enabled?`${s.shadow_x*unit*s.scale}px ${s.shadow_y*unit*s.scale}px ${s.shadow_blur*unit*s.scale}px ${rgba(s.shadow_color,s.shadow_opacity)}`:undefined,
      ...(s.gradient?{backgroundImage:`linear-gradient(135deg,${s.color},${s.gradient_color})`,WebkitBackgroundClip:'text',backgroundClip:'text',WebkitTextFillColor:'transparent'}:{})
    }}>{cue.text}</span>
    {s.bubble==='speech'&&<div style={{position:'absolute',bottom:-tail,left:'18%',width:0,height:0,
       borderLeft:`${tail}px solid transparent`,borderTop:`${tail}px solid ${background}`}}/>}
  </div>;
};

export const SubtitlePreview=({subtitleProfile,subtitles,background='#26313f'})=><AbsoluteFill style={{background}}>
  <StyledSubtitle style={subtitleProfile.style} subtitles={subtitles}/>
</AbsoluteFill>;
