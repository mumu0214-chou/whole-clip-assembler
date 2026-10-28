import React, {useEffect, useLayoutEffect, useRef, useState, useMemo} from 'react';
import {AbsoluteFill, useCurrentFrame, useVideoConfig, delayRender, continueRender, cancelRender} from 'remotion';
import {StyledSubtitle as Legacy} from './LegacyStyledSubtitle';
import {animationAt,clusters} from './subtitle-animation.cjs';
const rgba=(hex,alpha)=>{const c=hex.slice(1);return `rgba(${parseInt(c.slice(0,2),16)},${parseInt(c.slice(2,4),16)},${parseInt(c.slice(4,6),16)},${alpha})`;};
const loaded=new Map();
const FrozenSubtitle=({subtitles,style:s,topReserved=0})=>{
  const {width,height,fps}=useVideoConfig();const frame=useCurrentFrame();
  const cue=subtitles.find(c=>frame>=c.start_frame&&frame<c.end_frame);
  const box=useRef(null);const [ready,setReady]=useState(false);
  const [handle]=useState(()=>delayRender('加载智能字幕字体'));
  const alias=s.fontSrc?`caption_${s.font_sha256}`:s.font_family;
  const weight=s.bold?(s.font_weight??800):400;
  useEffect(()=>{
    let alive=true;const key=`${s.fontSrc||s.font_family}:${weight}:${s.italic}`;
    if(!loaded.has(key))loaded.set(key,(async()=>{
      if(s.fontSrc){const face=new FontFace(alias,`url("${s.fontSrc}")`);await face.load();document.fonts.add(face);}
      await document.fonts.load(`${s.italic?'italic ':''}${weight} ${s.font_size}px "${alias}"`);
      await document.fonts.ready;
      if(!document.fonts.check(`${weight} ${s.font_size}px "${alias}"`))throw new Error(`字幕字体加载失败：${alias}`);
    })());
    loaded.get(key).then(()=>{if(alive){setReady(true);continueRender(handle);}}).catch(cancelRender);
    return()=>{alive=false;};
  },[alias,handle,s.fontSrc,s.font_family,weight,s.italic,s.font_size]);
  const unit=height/1920*s.scale;const fontSize=s.font_size*unit;
  const margin=s.safe_horizontal*width;const bubble=s.bubble!=='none';
  const outerWidth=cue?.layout?.outer_width??Math.min(width*(1-2*s.safe_horizontal),width*s.max_width);
  const edge=cue?.layout?.edge??4;const px=bubble?s.padding_x*unit:0;const py=bubble?s.padding_y*unit:0;
  const tail=s.bubble==='speech'?20*unit:0;
  const bar=s.bubble==='teal_bar';const icon=bar?80*unit:0;
  const center=Math.max(margin+outerWidth/2,Math.min(width-margin-outerWidth/2,width*s.x));
  const emphasis=cue?.highlight?Math.max(1,Number(s.highlight_scale??1)):1;
  const rowHeight=fontSize*s.line_spacing*emphasis;
  const boxHeight=Math.max((cue?.lines.length??1)*rowHeight+2*(edge+py),bubble?s.bubble_height*unit:0);
  const requestedTop=s.y*height-(s.vertical_anchor==='center'?boxHeight/2:0);
  const top=Math.max(edge,topReserved?topReserved+24*unit:edge,Math.min(requestedTop,height*(1-s.safe_bottom)-edge-tail-boxHeight));
  const layout=useMemo(()=>cue?.lines.map(line=>clusters(line))??[],[cue]);
  const count=layout.reduce((sum,line)=>sum+line.length,0);const state=cue?animationAt(cue,frame,fps,unit):{};
  useLayoutEffect(()=>{
    if(!ready||!cue||!box.current)return;
    const available=outerWidth-2*(edge+px)-icon;
    for(const line of box.current.querySelectorAll('[data-caption-line]')){
      if(line.scrollWidth>available+2)cancelRender(new Error(`SUBTITLE_QC width overflow cue=${cue.source_range} font=${alias} actual=${line.scrollWidth} available=${available}`));
    }
    if(boxHeight>height*(1-s.safe_bottom)-2*edge-tail)cancelRender(new Error(`SUBTITLE_QC height overflow cue=${cue.source_range}`));
  },[ready,cue,outerWidth,edge,px,icon,boxHeight,height,s.safe_bottom,tail,alias]);
  if(!cue)return null;
  const background=rgba(s.bubble_color,s.bubble_opacity);const visible=Math.ceil(state.reveal*count);let index=0;
  state.x=Math.max(margin+outerWidth*state.scale/2-center,Math.min(width-margin-outerWidth*state.scale/2-center,state.x));
  state.y=Math.max(edge+(boxHeight*(state.scale-1)/2)-top,Math.min(height*(1-s.safe_bottom)-edge-tail-boxHeight*(state.scale+1)/2-top,state.y));
  const direction=cue.animation?.entry?.direction??'left';const hidden=(1-state.clip)*100;
  const clip=direction==='right'?`inset(0 0 0 ${hidden}%)`:direction==='up'?`inset(${hidden}% 0 0 0)`:direction==='down'?`inset(0 0 ${hidden}% 0)`:`inset(0 ${hidden}% 0 0)`;
  return <div style={{position:'absolute',inset:0,zIndex:30,pointerEvents:'none'}}>
    <div data-caption-box="true" ref={box} style={{position:'absolute',left:center-outerWidth/2,top,width:outerWidth,
      boxSizing:'border-box',height:boxHeight,padding:`${edge+py}px ${edge+px}px`,
      ...(bar?{paddingLeft:edge+px+icon,paddingTop:(boxHeight-layout.length*rowHeight)/2,
        boxShadow:`inset 0 0 0 ${4*unit}px #adccdc`}:{}),
      background:bubble?background:undefined,borderRadius:bubble?s.bubble_radius*unit:undefined,
      visibility:ready?'visible':'hidden',opacity:state.opacity,
      transform:`translate(${state.x}px,${state.y}px) scale(${state.scale})`,transformOrigin:'center center',
      filter:state.brightness!==1?`brightness(${state.brightness})`:undefined,clipPath:state.clip<1?clip:undefined,
      fontFamily:`"${alias}",sans-serif`,fontSize,fontWeight:weight,fontStyle:s.italic?'italic':'normal',lineHeight:s.line_spacing,
      letterSpacing:s.letter_spacing*unit,textAlign:s.align,whiteSpace:'pre',overflow:'visible'}}>
      {bar&&<><div style={{position:'absolute',left:icon,right:8*unit,top:8*unit,bottom:8*unit,
        background:'#ffffff',borderRadius:7*unit}}/>
        <svg width={48*unit} height={48*unit} viewBox="0 0 48 48"
          style={{position:'absolute',left:22*unit,top:(boxHeight-48*unit)/2}}>
          <circle cx="24" cy="24" r="24" fill="white"/><circle cx="24" cy="24" r="17" fill="#a95de1"/>
          <path d="M19 13 L19 35 L35 24 Z" fill="white"/>
        </svg></>}
      {layout.map((line,i)=><div key={i} style={{height:rowHeight,position:'relative'}}>
        <span data-caption-line="true" style={{display:'inline-block',whiteSpace:'pre',color:s.color,opacity:s.opacity,
          textDecoration:s.underline?'underline':'none',WebkitTextStroke:s.stroke_enabled?`${s.stroke_width*unit}px ${s.stroke_color}`:undefined,
          paintOrder:'stroke fill',textShadow:s.shadow_enabled?`${s.shadow_x*unit}px ${s.shadow_y*unit}px ${s.shadow_blur*unit}px ${rgba(s.shadow_color,s.shadow_opacity)}`:undefined,
          ...(s.gradient?{backgroundImage:`linear-gradient(135deg,${s.color},${s.gradient_color})`,WebkitBackgroundClip:'text',backgroundClip:'text',WebkitTextFillColor:'transparent'}:{})}}>
          {line.map((g,j)=>{const at=index++;const value=cue.lines[i];const match=cue.highlight&&cue.lines.findIndex(part=>part.includes(cue.highlight))===i?value.indexOf(cue.highlight):-1;
            const offset=line.slice(0,j).join('').length;const marked=match>=0&&offset>=match&&offset<match+cue.highlight.length;
            return <span key={j} style={{visibility:at<visible?'visible':'hidden',
              color:marked?s.highlight_color:undefined,fontSize:marked?`${emphasis}em`:undefined,
              WebkitTextFillColor:marked&&s.gradient?s.highlight_color:undefined}}>{g}</span>;})}
        </span>
      </div>)}
      {s.bubble==='speech'&&<div style={{position:'absolute',bottom:-tail,left:'18%',width:0,height:0,borderLeft:`${tail}px solid transparent`,borderTop:`${tail}px solid ${background}`}}/>}
    </div>
  </div>;
};
export const StyledSubtitle=(props)=>props.subtitles?.[0]?.revision==='smart_caption_v1'?<FrozenSubtitle {...props}/>:<Legacy {...props}/>;
export const SubtitlePreview=({subtitleProfile,subtitles,background='#26313f'})=><AbsoluteFill style={{background}}>
  <StyledSubtitle style={subtitleProfile.style} subtitles={subtitles}/>
</AbsoluteFill>;
