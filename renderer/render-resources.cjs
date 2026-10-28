'use strict';
const GiB=1024**3;
// Execution-only limit: never changes the frozen clips, frame schedule or effects.
function chooseConcurrency(requested,{freeMemoryBytes,width=1080,height=1920,exact=false}) {
  if (!Number.isSafeInteger(requested) || requested<1 || requested>64) throw new Error('Invalid Remotion concurrency');
  const reserveBytes=2.5*GiB; // OS headroom, frame cache, decoder and encoder outside browser pages.
  const perPageBytes=Math.max(.75*GiB,GiB*width*height/(1080*1920));
  const budget=Number.isFinite(freeMemoryBytes)?Math.max(1,Math.floor((freeMemoryBytes-reserveBytes)/perPageBytes)):1;
  const effective=exact?requested:Math.min(requested,budget);
  return {requested,effective,free_memory_bytes:freeMemoryBytes,reserve_bytes:reserveBytes,
    estimated_bytes_per_page:perPageBytes,policy:exact?'explicit_diagnostic_comparison':'available_memory_v1',
    reason:exact?'Isolated diagnostic comparison at the specified concurrency':effective<requested?'Available memory limits simultaneous browser frames':'Requested concurrency fits the estimated available memory budget'};
}
module.exports={chooseConcurrency};
