// Chrome's bundled protocol types document Tracing/IO. This collector starts
// after owner authentication; it never records login or source text snapshots.
export async function startChromeFrameTrace(page) {
  if (page.context().browser()?.browserType().name() !== 'chromium') return null;
  const session = await page.context().newCDPSession(page);
  const supported = new Set((await session.send('Tracing.getCategories')).categories);
  const categories = ['cc','benchmark','devtools.timeline','viz','gpu','disabled-by-default-devtools.timeline.frame'].filter(name=>supported.has(name));
  await session.send('Tracing.start',{categories:categories.join(','),transferMode:'ReturnAsStream',streamFormat:'json',streamCompression:'none'});
  await session.send('Tracing.recordClockSyncMarker',{syncId:'graph-viewer-orbit-start'});
  return async function finish() {
    await session.send('Tracing.recordClockSyncMarker',{syncId:'graph-viewer-orbit-end'});
    const complete = new Promise(resolve=>session.once('Tracing.tracingComplete',resolve));
    await session.send('Tracing.end');
    const result = await complete;
    if (!result.stream) throw new Error('Chrome frame trace did not produce a stream');
    let raw='';
    try {
      while(true) {
        const part=await session.send('IO.read',{handle:result.stream,size:262144});
        raw+=part.base64Encoded?Buffer.from(part.data,'base64').toString('utf8'):part.data;
        if(part.eof)break;
      }
    } finally {await session.send('IO.close',{handle:result.stream});await session.detach();}
    const trace=JSON.parse(raw);
    const relevant=new Map();
    for(const event of trace.traceEvents||[]) {
      if(/Frame|DrawAndSwap|SwapBuffer|Presentation|Presented|Display::/i.test(event.name||'')) {
        const key=`${event.name} [${event.cat||''}]`;
        relevant.set(key,(relevant.get(key)||0)+1);
      }
    }
    return {raw,summary:{categories,dataLossOccurred:result.dataLossOccurred,eventCount:trace.traceEvents?.length||0,
      eventKinds:[...relevant].sort((a,b)=>b[1]-a[1]).map(([name,count])=>({name,count})),
      qualification:'Raw compositor/GPU trace for inspection. No presented-FPS pass inferred from rAF, DrawFrame count or event-name matches alone.'}};
  };
}
