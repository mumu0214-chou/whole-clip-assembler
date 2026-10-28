import './fetch-diagnostics';
import {StyledSubtitle, SubtitlePreview} from './StyledSubtitle';
import React from 'react';
import {EditorialAccents,accentScale} from './EditorialAccents';
import {AbsoluteFill, Composition, Img, OffthreadVideo, Sequence, registerRoot, interpolate, useCurrentFrame, useVideoConfig} from 'remotion';

const LegacySubtitleOverlay = ({subtitles, profile, topText, topTextStyle}) => {
  const frame = useCurrentFrame();
  const {height, width} = useVideoConfig();
  const cue = (subtitles ?? []).find((c) => frame >= c.start_frame && frame < c.end_frame);
  if (!cue) return null;
  const p = profile ?? {};
  const bottom = Number(p.safe_margin_bottom ?? 0.14) * height;
  const left = Number(p.safe_margin_left ?? 0.08) * width;
  const right = Number(p.safe_margin_right ?? 0.08) * width;
  const position = p.position ?? '下中';
  const positions = {'顶部': Number(p.safe_margin_top ?? 0.1) * height,
                     '上中': height * 0.34, '居中': height * 0.5,
                     '下中': height * 0.72, '底部安全区': height - bottom - 100,
                     '自定义': Number(p.custom_y ?? 0.72) * height};
  const topReserved = topText?.enabled ? Number(topTextStyle?.top ?? 90) +
    Number(topTextStyle?.fontSize ?? 60) * 2.5 : 0;
  const fontSize = Number(p.font_size ?? 58) * height / 1920;
  const lineHeight = fontSize * 1.22;
  const maxTop = height - bottom - lineHeight * 2 - 12;
  const top = Math.min(maxTop, Math.max(positions[position] ?? positions['下中'], topReserved + 24));
  const maxChars = Math.max(8, Number(p.max_chars_per_line ?? 16));
  const characters = Array.from(cue.text);
  const lines = Array.isArray(cue.lines) ? cue.lines : [];
  if (!lines.length) {
    for (let i = 0; i < characters.length; i += maxChars) lines.push(characters.slice(i, i + maxChars).join(''));
  }
  return <div style={{position:'absolute', top, left, right, zIndex:30,
    color:p.color ?? '#fff', textAlign:'center', fontFamily:`${p.font ?? 'Microsoft YaHei'}, sans-serif`,
    fontWeight:Number(p.font_weight ?? 800), fontSize, lineHeight:1.22,
    whiteSpace:Array.isArray(cue.lines) ? 'pre' : 'pre-wrap',
    WebkitTextStroke:`${Number(p.stroke_width ?? 3)}px ${p.stroke ?? '#000'}`,
    paintOrder:'stroke fill', textShadow:'0 2px 7px rgba(0,0,0,.8)',
    pointerEvents:'none'}}>{lines.join('\n')}</div>;
};

const SubtitleOverlay = (props) => props.profile?.style?.version === 1
  ? <StyledSubtitle subtitles={props.subtitles} style={props.profile.style}
      topReserved={props.profile.placement_revision === 'canvas_position_v1' ? 0 :
        (props.topText?.enabled ? Number(props.topTextStyle?.top ?? 90)+Number(props.topTextStyle?.fontSize ?? 60)*2.5 : 0)}/>
  : <LegacySubtitleOverlay {...props}/>;

const TopOverlayText = ({topText, styleConfig, visual}) => {
  const frame = useCurrentFrame();
  if (!topText?.enabled || !topText?.text) return null;
  if (Array.isArray(topText.windows) && !topText.windows.some(
    (window) => frame >= window.start_frame && frame < window.end_frame)) return null;
  if (visual) return <Img src={visual.src} style={{position:'absolute',left:visual.left,top:visual.top,
    width:visual.width,height:visual.height,zIndex:20,pointerEvents:'none'}}/>;
  const lines = String(topText.text).split(/\r?\n/).filter(Boolean);
  const maxLineLength = Math.max(1, ...lines.map((line) => Array.from(line).length));
  const base = Number(styleConfig?.fontSize ?? 60);
  const linePenalty = Math.max(0, lines.length - 4) * 3;
  const lengthPenalty = Math.max(0, maxLineLength - 12) * 1.15;
  const fontSize = Math.max(38, Math.min(base, base - linePenalty - lengthPenalty));
  const top = Number(styleConfig?.top ?? 90);
  const maxWidth = Math.max(0.35, Math.min(0.95, Number(styleConfig?.maxWidth ?? 0.78)));
  const stroke = Math.max(0, Math.min(8, Number(styleConfig?.stroke ?? 3)));

  return (
    <div
      style={{
        position: 'absolute',
        top,
        left: '50%',
        transform: 'translateX(-50%)',
        width: `${maxWidth * 100}%`,
        color: '#fff',
        textAlign: 'center',
        whiteSpace: 'pre-wrap',
        fontFamily: '"Microsoft YaHei", "Microsoft YaHei UI", "Noto Sans CJK SC", sans-serif',
        fontWeight: 800,
        fontSize,
        lineHeight: 1.18,
        letterSpacing: 0,
        WebkitTextStroke: stroke ? `${stroke}px rgba(0,0,0,0.90)` : undefined,
        paintOrder: 'stroke fill',
        textShadow: '0 2px 7px rgba(0,0,0,0.72)',
        zIndex: 20,
        pointerEvents: 'none',
      }}
    >
      {topText.text}
    </div>
  );
};

// Input files remain immutable. Optional explicitly selected export speed/effects
// operate only on the visual layer; the top-text overlay is not flipped or tinted.
export const ClipVisual = ({clip, videoProcessing, visualLayout, editStyle}) => {
  const frame = useCurrentFrame();
  const effects = clip.effects ?? {};
  const mirror = effects.mirror ?? videoProcessing?.mirror ?? false;
  const color = effects.color ?? videoProcessing?.color ?? false;
  const brightness = effects.brightness ?? videoProcessing?.brightness ?? 1;
  const contrast = effects.contrast ?? videoProcessing?.contrast ?? 1;
  const saturation = effects.saturation ?? videoProcessing?.saturation ?? 1;
  const zoom = effects.zoom ?? false;
  const zoomStart = Number(effects.zoom_start ?? 1);
  const zoomEnd = Number(effects.zoom_end ?? 1);
  const progress = Math.min(1, Math.max(0, frame / Math.max(1, clip.frames - 1)));
  const eased = progress * progress * (3 - 2 * progress);
  const scale = zoom ? zoomStart + (zoomEnd - zoomStart) * (effects.zoom_ease ? eased : progress) : 1;
  const transforms = [];
  const editorialScale=accentScale(editStyle,clip,frame);
  if(editorialScale!==1)transforms.push(`scale(${editorialScale})`);
  if (mirror) transforms.push('scaleX(-1)');
  if (scale !== 1) transforms.push(`scale(${scale})`);
  const inc = clip.incomingTransition ?? {type: 'none', frames: 0};
  const out = clip.outgoingTransition ?? {type: 'none', frames: 0};
  const ip = inc.frames ? Math.min(1, Math.max(0, (frame + 1) / inc.frames)) : 1;
  const op = out.frames ? Math.min(1, Math.max(0, (frame - (clip.frames - out.frames) + 1) / out.frames)) : 0;
  let opacity = 1;
  let translate = 0;
  let clipPath;
  let blur = 0;
  if (inc.type === 'fade' || inc.type === 'blur_fade') opacity = ip;
  if (inc.type === 'fade_black' && inc.effect_frames) opacity *= Math.min(1, (frame + 1) / inc.effect_frames);
  if (out.type === 'fade_black' && out.effect_frames) opacity *= Math.max(0, Math.min(1, (clip.frames - frame - 1) / out.effect_frames));
  if (inc.type === 'slide_left') translate += (1 - ip) * 100;
  if (inc.type === 'slide_right') translate -= (1 - ip) * 100;
  if (out.type === 'slide_left') translate -= op * 100;
  if (out.type === 'slide_right') translate += op * 100;
  if (inc.type === 'wipe_left') clipPath = `inset(0 ${(1 - ip) * 100}% 0 0)`;
  if (inc.type === 'wipe_right') clipPath = `inset(0 0 0 ${(1 - ip) * 100}%)`;
  if (inc.type === 'blur_fade') blur += (1 - ip) * 10;
  if (out.type === 'blur_fade') blur += op * 10;
  const fx = effects.fx_type ?? 'none';
  const strength = Math.min(0.6, Math.max(0, Number(effects.fx_strength ?? 0)));
  const stacked = visualLayout?.mode === 'stacked';
  if (visualLayout && (visualLayout.revision !== 'visual_layout_v1' || !['normal','stacked'].includes(visualLayout.mode))) {
    throw new Error('Invalid frozen visual layout');
  }
  const picture = (key) => <OffthreadVideo key={key}
    src={clip.src} muted={true} volume={0} playbackRate={clip.playbackRate ?? 1}
    trimBefore={clip.sourceStartFrame ?? 0}
    style={{width:'100%',height:'100%',objectFit:visualLayout?.fit ?? (stacked?'cover':'contain'),
      transform:transforms.length?transforms.join(' '):undefined,
      filter:color?`brightness(${brightness}) contrast(${contrast}) saturate(${saturation})`:undefined}} />;
  return (
    <AbsoluteFill style={{backgroundColor: '#000', overflow: 'hidden', opacity,
      transform: translate ? `translateX(${translate}%)` : undefined,
      clipPath, filter: blur ? `blur(${blur}px)` : undefined}}>
      {stacked ? [0,1].map(panel=><div key={panel} data-stacked-panel={panel}
        style={{position:'absolute',left:0,top:`${panel*50}%`,width:'100%',height:'50%',overflow:'hidden'}}>
          {picture(panel)}
        </div>) : picture('normal')}
      {fx === 'vignette' && <AbsoluteFill style={{pointerEvents: 'none',
        background: `radial-gradient(ellipse at center, transparent 35%, rgba(0,0,0,${strength}) 100%)`}} />}
      {fx === 'soft_light' && <AbsoluteFill style={{pointerEvents: 'none',
        background: `linear-gradient(135deg, rgba(255,255,255,${strength}), transparent 60%)`}} />}
    </AbsoluteFill>
  );
};

export const WholeClipAssembly = ({clips, sourceAudio, topText, topTextStyle, topTextVisual, videoProcessing, visualLayout, subtitles, subtitleProfile, renderDiagnostics,editStyle}) => {
  const diagnosticFrame = useCurrentFrame();
  if (typeof window !== 'undefined' && renderDiagnostics) window.__assemblyFrameContext = {frame: diagnosticFrame, jobId: renderDiagnostics.jobId};
  if (sourceAudio !== 'discard') throw new Error('Source audio must be discarded');
  return (
    <AbsoluteFill style={{backgroundColor: '#000'}}>
      {clips.map((clip, i) => (
        <Sequence key={i} from={clip.from} durationInFrames={clip.frames} name={clip.label}>
          <ClipVisual clip={clip} videoProcessing={videoProcessing} visualLayout={visualLayout} editStyle={editStyle} />
        </Sequence>
      ))}
      <TopOverlayText topText={topText} styleConfig={topTextStyle} visual={topTextVisual} />
      <SubtitleOverlay subtitles={subtitles} profile={subtitleProfile} topText={topText} topTextStyle={topTextStyle} />
      <EditorialAccents snapshot={editStyle}/>
    </AbsoluteFill>
  );
};

const defaults = {
  clips: [],
  sourceAudio: 'discard',
  videoProcessing: {enabled: false, color: false, mirror: false, speed: 1},
  topText: {enabled: false, text: ''},
  topTextStyle: {fontSize: 60, top: 90, maxWidth: 0.78, stroke: 3},
  subtitles: [], subtitleProfile: {},
  width: 1080,
  height: 1920,
  fps: 30,
  durationInFrames: 30,
};

const Root = () => (
  <>
  <Composition
    id="WholeClipAssembly"
    component={WholeClipAssembly}
    width={1080}
    height={1920}
    fps={30}
    durationInFrames={30}
    defaultProps={defaults}
    calculateMetadata={({props}) => ({
      width: props.width,
      height: props.height,
      fps: props.fps,
      durationInFrames: props.durationInFrames,
    })}
  />
  <Composition id="SubtitlePreview" component={SubtitlePreview} width={1080} height={1920} fps={30}
    durationInFrames={30} defaultProps={{subtitleProfile:{style:{}},subtitles:[],width:1080,height:1920}}
    calculateMetadata={({props})=>({width:props.width,height:props.height,fps:props.fps||30,durationInFrames:props.durationInFrames||30})}/>
  </>
);

registerRoot(Root);
