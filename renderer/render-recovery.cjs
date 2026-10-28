'use strict';
// Execution recovery only: never rebuild or mutate a frozen composition.
function frameWaitFailure(error) {
  const text=String(error?.stack??error);
  if (/AbortError|user (?:cancel|abort)|render (?:was )?cancelled/i.test(text)) return false;
  return /delayRender\(\)/i.test(text) && /Fetching https?:\/\/[^\s"]+\/proxy\?/i.test(text)
    && /not cleared after \d+ms/i.test(text);
}
function framePolicy(concurrency) {
  return {concurrency,offthreadVideoThreads:2,timeoutInMilliseconds:120000,
    policy:'bounded_frame_wait_serial_retry_v1',maxRenderAttempts:2};
}
async function withFrameRecovery(policy,operation,{onFailure=()=>{},onRetry=()=>{}}={}) {
  try {return {result:await operation(policy,1),policy,attempts:1};}
  catch(error) {
    if (!frameWaitFailure(error)) throw error;
    if(onFailure(error,1)===false)throw error;
    const fallback={...policy,concurrency:1,offthreadVideoThreads:1,retryReason:'local_video_frame_wait_timeout'};
    onRetry(fallback,error);
    // A fresh render invocation owns a fresh browser. This is the only retry;
    // deterministic media/HTTP/codec errors and cancellation propagate intact.
    return {result:await operation(fallback,2),policy:fallback,attempts:2};
  }
}
module.exports={frameWaitFailure,framePolicy,withFrameRecovery};
