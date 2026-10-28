"""Preview-only center guides, using the same caption box geometry as rendering."""
from smart_subtitles import geometry


def box(style, width, height, cue=None):
    # The editor displays a 540x960 Remotion preview at 225x400. Compute
    # geometry before scaling, including its minimum 4px safety stroke.
    ratio=height/960
    outer, _, edge, unit = geometry(style, width/ratio, 960)
    outer*=ratio;edge*=ratio;unit*=ratio
    count=len((cue or {}).get('lines') or ['字幕'])
    emphasis=max(1,style.get('highlight_scale',1)) if (cue or {}).get('highlight') else 1
    rows=count*style['font_size']*unit*style['line_spacing']*emphasis
    padding=style['padding_y']*unit if style['bubble']!='none' else 0
    h=max(rows+2*(edge+padding),style['bubble_height']*unit if style['bubble']!='none' else 0)
    tail=20*unit if style['bubble']=='speech' else 0
    margin=style['safe_horizontal']*width
    center=max(margin+outer/2,min(width-margin-outer/2,width*style['x']))
    requested=style['y']*height-(h/2 if style.get('vertical_anchor')=='center' else 0)
    top=max(edge,min(requested,height*(1-style['safe_bottom'])-edge-tail-h))
    return dict(width=outer,height=h,center_x=center,center_y=top+h/2,top=top,edge=edge,tail=tail)


def drag(style, width, height, center_x, center_y, cue=None, snap=True):
    b=box(style,width,height,cue)
    # A fixed screen-pixel threshold feels the same at all preview sizes.
    vx=snap and abs(center_x-width/2)<=6
    hy=snap and abs(center_y-height/2)<=6
    if vx:center_x=width/2
    if hy:center_y=height/2
    margin=style['safe_horizontal']*width
    center_x=max(margin+b['width']/2,min(width-margin-b['width']/2,center_x))
    center_y=max(b['edge']+b['height']/2,min(height*(1-style['safe_bottom'])-b['edge']-b['tail']-b['height']/2,center_y))
    return dict(x=center_x/width,y=center_y/height,center_x=center_x,center_y=center_y,
                width=b['width'],height=b['height'],vertical=bool(vx and abs(center_x-width/2)<.01),
                horizontal=bool(hy and abs(center_y-height/2)<.01))
