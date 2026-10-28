// Only annotates local Remotion frame requests. Media URLs, results and timeouts stay unchanged.
if (typeof window !== 'undefined' && !window.__assemblyFetchDiagnosticsInstalled) {
  window.__assemblyFetchDiagnosticsInstalled = true;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, init) => {
    const raw = typeof input === 'string' ? input : input?.url;
    let url;
    try {url = new URL(raw, window.location.href);} catch (_) {return originalFetch(input, init);}
    const context = window.__assemblyFrameContext;
    if (!context || url.origin !== window.location.origin || url.pathname !== '/proxy') return originalFetch(input, init);
    const headers = new Headers(init?.headers ?? (typeof input === 'object' ? input.headers : undefined));
    headers.set('X-Assembly-Frame', String(context.frame));
    const started = performance.now();
    const fields = {job_id: context.jobId, failed_frame: context.frame, proxy_url: url.href,
      media_url: url.searchParams.get('src'), requested_time: Number(url.searchParams.get('time'))};
    const emit = more => console.log('ASSEMBLY_FETCH:' + JSON.stringify({...fields, elapsed_ms: Math.round(performance.now()-started), ...more}));
    const timer = setTimeout(() => emit({proxy_status: null, request_state: 'pending_at_25s'}), 25000);
    return originalFetch(input, {...init, headers}).then(response => {
      clearTimeout(timer);
      if (!response.ok || performance.now()-started > 1000) emit({proxy_status: response.status, request_state: 'headers_received'});
      return response;
    }, error => {clearTimeout(timer);emit({proxy_status: null, request_state: 'fetch_failed', error: String(error)});throw error;});
  };
}
