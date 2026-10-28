"""Bounded local replacement of complete actions on frozen transition slots."""
from copy import deepcopy
import math
import time

REVISION='complete_action_slot_repair_v1'


def repair_block(seq,pool,name,target,cfg,seed,snapshot,rng,*,deadline=None,max_nodes=6000):
    """Replace at most three final shots; never crop, reroll effects or relax gates.

    Large pools cannot enumerate all subsets. The greedy sequence gives a useful
    stem. Match its remainder with exact frame lengths and the same slot-specific
    transitions that the shared timeline builder will use.
    """
    from grouped_materials import pair_key,conflict
    from timeline_processing import bake_timeline,build_slot_profiles,transition_frames
    from voice_edge_fitting import prepare
    if cfg.get('processing_enabled') and cfg.get('processing_speed_enabled'):
        return None
    indices=[i for i,c in enumerate(seq) if c.get('planning_block')==name]
    if not indices or name=='package' or indices!=list(range(indices[0],indices[-1]+1)):
        return None
    first,last=indices[0],indices[-1]
    out,_,_=bake_timeline(prepare(seq),cfg,seed)
    frames=[c['duration_frames'] for c in out]
    before=sum(frames[first:last+1])-sum(out[i]['outgoing_transition']['frames'] for i in range(first,last))
    if before==target:return deepcopy(seq)
    if any(c.get('safe_cut_confirmed') for c in seq[first:last+1]):return None
    _,requests=build_slot_profiles(len(seq),cfg,seed,set(range(len(seq))),voice_fitting=True)
    states={r['pair_key']:r['state'] for r in snapshot['pairs']}
    assets=list(pool);lookup={c['relative_path']:i for i,c in enumerate(assets)}
    masks=[]
    for a in assets:
        masks.append(sum(1<<j for j,b in enumerate(assets) if a['hash']==b['hash'] or
            a['identity_key']==b['identity_key'] or states.get(pair_key(a,b),'pending')!='clear'))
    order=list(range(len(assets)))
    rng.shuffle(order)
    sizes=[max(1,math.floor(c['duration']*cfg['fps']+1e-8)) for c in assets]
    minimum,maximum=min(sizes),max(sizes)
    max_overlap=max((r['requested_frames'] for r in requests),default=0)
    visited=0
    for width in range(1,min(3,len(indices))+1):
        cut=last-width+1
        fixed=seq[:cut]+seq[last+1:]
        blocked=0
        for c in fixed:
            blocked |= masks[lookup[c['relative_path']]]
        kept_frames=sum(frames[first:cut])-sum(out[i]['outgoing_transition']['frames'] for i in range(first,cut-1))
        previous=frames[cut-1] if cut>first else 0
        chosen=[]
        def search(position,total,prev,forbidden):
            nonlocal visited
            visited+=1
            if visited>max_nodes or (deadline is not None and time.monotonic()>deadline):return None
            if position>last:
                if total!=target:return None
                result=deepcopy(seq[:cut])+[dict(assets[j],planning_block=name,boundary_no_transition=False) for j in chosen]+deepcopy(seq[last+1:])
                if conflict(result,snapshot):return None
                baked,_,_=bake_timeline(prepare(result),cfg,seed)
                actual=sum(c['duration_frames'] for c in baked[first:last+1])-sum(baked[i]['outgoing_transition']['frames'] for i in range(first,last))
                return result if actual==target else None
            remaining=last-position+1
            if total+remaining*maximum<target or total+remaining*max(0,minimum-max_overlap)>target:return None
            for j in order:
                if forbidden & (1<<j) or assets[j].get('safe_cut_confirmed'):continue
                n=sizes[j]
                overlap=transition_frames(requests[position-1],prev,n) if position>first else 0
                value=total+n-overlap
                if value>target:continue
                chosen.append(j)
                found=search(position+1,value,n,forbidden|masks[j])
                chosen.pop()
                if found is not None:return found
                if visited>max_nodes or (deadline is not None and time.monotonic()>deadline):break
            return None
        found=search(cut,kept_frames,previous,blocked)
        if found is not None:return found
        if visited>max_nodes or (deadline is not None and time.monotonic()>deadline):break
    return None
