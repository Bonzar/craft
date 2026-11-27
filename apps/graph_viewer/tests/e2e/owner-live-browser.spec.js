import {test,expect} from '@playwright/test';
import {readFileSync,realpathSync} from 'node:fs';
import {join} from 'node:path';
import {execFileSync} from 'node:child_process';

test.use({ignoreHTTPSErrors:true,reducedMotion:'reduce',launchOptions:{chromiumSandbox:true,ignoreDefaultArgs:['--disable-backgrounding-occluded-windows','--disable-renderer-backgrounding']}});
test('authenticated warm cache then exact graph delta on one SSE, with no camera or geometry jump',async({context,page})=>{
  const base=realpathSync(process.env.GRAPH_BROWSER_FIXTURE);
  const port=Number(readFileSync(join(base,'fixture-port'),'utf8'));
  expect(Number.isInteger(port)&&port>1024&&port<65536).toBe(true);
  const origin='https://127.0.0.1:'+port, db=realpathSync(join(base,'fixture.sqlite3'));
  expect(db).toBe(join(base,'fixture.sqlite3'));
  await context.route('**/*',r=>{
    const u=new URL(r.request().url());
    return u.origin===origin||['about:','blob:','data:'].includes(u.protocol)?r.continue():r.abort();
  });
  await page.addInitScript(()=>{
    const fetchOriginal=window.fetch;
    window.__fixtureDocument=crypto.randomUUID();
    window.__fixtureFrames=[];
    window.fetch=function(input,...args){
      const url=new URL(input instanceof Request?input.url:input,location.href);
      const answer=fetchOriginal.call(this,input,...args);
      if(url.origin!==location.origin||url.pathname!=='/api/v1/events'||url.searchParams.get('wire')!=='graph-v1'
          ||window.name!=='hold-qa-warm')return answer;
      return Promise.all([answer,new Promise(resolve=>{window.__fixtureRelease=resolve;})]).then(([response])=>{
        // Read only names, never keep or log payloads, against the isolated fixture.
        const reader=response.clone().body.getReader(),decoder=new TextDecoder();
        void (async()=>{
          let part='';
          for(;;){
            const {done,value}=await reader.read();
            if(done)return;
            const lines=(part+decoder.decode(value,{stream:true})).split('\n');
            part=lines.pop();
            for(const line of lines)if(line.startsWith('event: '))window.__fixtureFrames.push(line.slice(7).trim());
          }
        })().catch(()=>{});
        return response;
      });
    };
  });
  const requests=[];
  page.on('request',r=>{
    const u=new URL(r.url());
    if(u.origin===origin)requests.push({path:u.pathname,wire:u.searchParams.get('wire'),
      after:u.searchParams.has('after'),rev:u.searchParams.has('revision')});
  });
  const eventReq=()=>requests.filter(r=>r.path==='/api/v1/events');
  const graphReq=()=>requests.filter(r=>r.path==='/api/v1/graph'||r.path.startsWith('/api/v1/graph/'));
  const waitGraph=()=>page.waitForFunction(()=>window.__graphViewer?.snapshot().renderedFrames>1&&Boolean(window.__graphViewer.lodAuditData()));
  const status=page.locator('#connection-status');
  const pose=()=>page.evaluate(()=>{
    const viewer=window.__graphViewer,{camera,revision,nodeSlots}=viewer.snapshot(),data=viewer.lodAuditData();
    return {doc:window.__fixtureDocument,revision,nodeSlots,camera:[...camera.position,...camera.target,...camera.projectionMatrix],
      bits:Array.from(new Uint32Array(data.visual_positions.buffer,data.visual_positions.byteOffset,data.visual_positions.length)),
      edges:Array.from(data.source_edges)};
  });

  await page.goto(origin);
  expect(await page.evaluate(()=>Boolean(document.createElement('canvas').getContext('webgl2'))),
    'Installed sandboxed browser must supply WebGL2').toBe(true);
  await page.locator('#password').fill('synthetic-only-owner');
  await page.locator('#login-button').click();
  await expect(status).toHaveText('Live · обновляется');
  await waitGraph();
  const cold=await pose();
  expect(eventReq()).toEqual([{path:'/api/v1/events',wire:'graph-v1',after:false,rev:false}]);
  expect(graphReq()).toEqual([]);
  await page.waitForFunction(rev=>new Promise(resolve=>{
    const open=indexedDB.open('private-owner-graph-topology-v1',1);
    // If the application's cache isn't present yet do not create or pre-seed it.
    open.onupgradeneeded=()=>open.transaction.abort();
    open.onerror=()=>resolve(false);
    open.onsuccess=()=>{
      const db=open.result;
      if(!db.objectStoreNames.contains('checkpoint')){db.close();resolve(false);return;}
      const read=db.transaction('checkpoint').objectStore('checkpoint').get('latest');
        read.onsuccess=()=>{db.close();resolve(read.result?.value?.revision===rev&&Boolean(read.result.value.view));};
      read.onerror=()=>{db.close();resolve(false);};
    };
  }),cold.revision);

  await page.evaluate(()=>{window.name='hold-qa-warm';});
  requests.length=0;
  await page.reload();
  await page.waitForFunction(()=>typeof window.__fixtureRelease==='function',null,{timeout:10000});
  expect(eventReq()).toEqual([{path:'/api/v1/events',wire:'graph-v1',after:true,rev:true}]);
  await expect(status).toHaveText('Снимок · подключаемся к Live');
  await waitGraph();
  const warm=await pose();
  expect(warm.revision).toBe(cold.revision);
  expect(warm.bits).toEqual(cold.bits);
  expect(warm.edges).toEqual(cold.edges);

  await page.evaluate(()=>{window.name='';window.__fixtureRelease();});
  await expect(status).toHaveText('Live · обновляется');
  await page.getByRole('button',{name:'Приблизить',exact:true}).click();
  await page.waitForFunction(previous=>window.__graphViewer.snapshot().camera.position.some((x,i)=>Math.abs(x-previous[i])>1e-4),warm.camera.slice(0,3));
  const before=await pose();

  // window.open with no popup features creates a normal tab in the same
  // Chrome window. Drive Chrome's own tab-strip shortcut, not DOM events.
  const [other]=await Promise.all([page.waitForEvent('popup'),page.evaluate(()=>{window.open('about:blank','_blank');})]);
  // Playwright enables synthetic focus for every main frame by default.
  // Restore real browser focus/visibility for this explicit tab journey.
  const [mainFocus,otherFocus]=await Promise.all([context.newCDPSession(page),context.newCDPSession(other)]);
  await Promise.all([mainFocus.send('Emulation.setFocusEmulationEnabled',{enabled:false}),
    otherFocus.send('Emulation.setFocusEmulationEnabled',{enabled:false})]);
  await page.bringToFront();
  await page.waitForFunction(()=>document.visibilityState==='visible'&&document.hasFocus());
  const [mainWindow,otherWindow]=await Promise.all([mainFocus.send('Browser.getWindowForTarget'),otherFocus.send('Browser.getWindowForTarget')]);
  expect(mainWindow.windowId,'The two pages must be tabs in the same actual Chrome window').toBe(otherWindow.windowId);
  const tabSwitch=()=>execFileSync('xdotool',['key','--clearmodifiers','ctrl+Tab'],{timeout:4000});
  tabSwitch();
  await expect.poll(()=>page.evaluate(()=>document.visibilityState),{timeout:8000}).toBe('hidden');
  execFileSync(process.env.GRAPH_FIXTURE_PYTHON||'python3',['-c',[
    'import sqlite3,sys',
    "with sqlite3.connect('file:'+sys.argv[1]+'?mode=rw',uri=True) as c:",
    ' assert c.execute("SELECT count(*) FROM memory_assertions").fetchone()==(6,)',
    " c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('zz-qa-live-added','Synthetic update','actor','saved','session','2025-01-01T00:00:01+00:00','2025-01-01T00:00:01+00:00','s','{}'))",
    " c.execute('UPDATE memory_state SET version=version+1 WHERE singleton=1')",
  ].join('\n'),db]);
  // Confirm the actual transport signal arrived while this tab was away.
  await expect.poll(()=>page.evaluate(()=>window.__fixtureFrames.includes('delta')),{timeout:12000}).toBe(true);
  tabSwitch();
  await expect.poll(()=>page.evaluate(()=>document.visibilityState),{timeout:8000}).toBe('visible');
  await page.waitForFunction(({rev,n})=>{
    const s=window.__graphViewer?.snapshot();
    return s?.revision&&s.revision!==rev&&s.nodeSlots===n+1&&window.__graphViewer.lodAuditData()?.revision===s.revision;
  },{rev:before.revision,n:before.nodeSlots});
  const after=await pose();
  expect(after.doc).toBe(before.doc);
  expect(after.bits.slice(0,before.bits.length)).toEqual(before.bits);
  expect(after.edges.slice(0,before.edges.length)).toEqual(before.edges);
  after.camera.forEach((v,i)=>expect(v).toBeCloseTo(before.camera[i],7));
  const frames=await page.evaluate(()=>window.__fixtureFrames);
  expect(frames[0]).toBe('hello');
  expect(frames).toContain('caught-up');
  expect(frames).toContain('delta');
  expect(frames).not.toContain('snapshot-start');
  expect(frames).not.toContain('reset');
  expect(eventReq()).toHaveLength(1);
  expect(graphReq()).toEqual([]);
  await expect(status).toHaveText('Live · обновляется');
  await Promise.all([mainFocus.detach(),otherFocus.detach()]);
  await other.close();
});
