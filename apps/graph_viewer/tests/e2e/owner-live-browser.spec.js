import {test as baseTest,expect,chromium} from '@playwright/test';
import {readFileSync,realpathSync,mkdtempSync,rmSync} from 'node:fs';
import {createHash,X509Certificate} from 'node:crypto';
import {once} from 'node:events';
import {join} from 'node:path';
import {execFileSync,spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';

const test=baseTest.extend({
  nativeBrowser:async({},use)=>{
    const dir=realpathSync(process.env.GRAPH_BROWSER_FIXTURE);
    const profile=mkdtempSync(join(dir,'chrome-'));
    const cert=new X509Certificate(readFileSync(join(dir,'cert')));
    const key=createHash('sha256').update(cert.publicKey.export({type:'spki',format:'der'})).digest('base64');
    // Official system Chrome's normal sandbox remains on. The process has
    // only a disposable profile and trusts only this fixture's unique TLS key.
    const chrome=spawn('/opt/google/chrome/chrome',[
      '--no-first-run','--no-default-browser-check','--disable-background-networking',
      '--disable-component-update','--disable-default-apps','--disable-sync',
      '--user-data-dir='+profile,'--remote-debugging-address=127.0.0.1','--remote-debugging-port=0',
      '--ignore-certificate-errors-spki-list='+key,
      '--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader','about:blank',
    ],{stdio:'ignore'});
    let browser;
    const exited=once(chrome,'exit').catch(()=>{});
    try{
      let endpoint;
      for(let attempt=0;attempt<100&&!endpoint;attempt++){
        if(chrome.exitCode!==null||chrome.signalCode!==null)break;
        try{
          const [port,path]=readFileSync(join(profile,'DevToolsActivePort'),'utf8').split('\n');
          if(/^[0-9]{4,5}$/.test(port)&&+port<=65535&&/^\/devtools\/browser\/[a-f0-9-]+$/i.test(path))
            endpoint='ws://127.0.0.1:'+port+path;
        }catch{}
        if(!endpoint)await delay(100);
      }
      expect(endpoint,'Sandboxed system Chrome must open an ephemeral loopback DevTools socket').toBeTruthy();
      // Do not let Playwright continuously emulate focus for these real tabs.
      browser=await chromium.connectOverCDP(endpoint,{noDefaults:true,timeout:8000});
      const [context]=browser.contexts(),[page]=context.pages();
      expect(page).toBeTruthy();
      await use({context,page});
    }finally{
      try{await browser?.close();}finally{
        if(chrome.exitCode===null&&chrome.signalCode===null)chrome.kill('SIGTERM');
        await Promise.race([exited,delay(3000)]);
        if(chrome.exitCode===null&&chrome.signalCode===null)chrome.kill('SIGKILL');
        await Promise.race([exited,delay(1000)]);
        rmSync(profile,{recursive:true,force:true});
      }
    }
  }
});
test('authenticated warm cache then exact graph delta on one SSE, with no camera or geometry jump',async({nativeBrowser:{context,page}})=>{
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
    const postWorker=Worker.prototype.postMessage;
    Worker.prototype.postMessage=function(message,...args){
      if(window.__qaRejectNextDelta&&message?.kind==='load-patch'&&message.patch?.upsert_nodes?.length){
        window.__qaRejectNextDelta=false;
        window.__qaRejectedDeltas=(window.__qaRejectedDeltas??0)+1;
        throw new Error('Synthetic fixture: one worker patch rejected');
      }
      return postWorker.call(this,message,...args);
    };
    window.__fixtureDocument=crypto.randomUUID();
    window.__fixtureFrames=[];
    window.__fixtureCache={reads:0,deletions:0,lastGotValue:false,lastGotView:false,sessionAuthenticated:false,
      sessionScopeShape:false,sessionEpochShape:false,authMatchesStored:false};
    let storedScope,storedEpoch,sessionScope,sessionEpoch;
    function recordMatch(){
      if(!storedScope||!sessionScope||!storedEpoch||!sessionEpoch)return;
      const [format,origin,owner]=JSON.parse(storedScope);
      window.__fixtureCache.authMatchesStored=format===1&&origin===location.origin&&owner===sessionScope&&storedEpoch===sessionEpoch;
    }
    const get=IDBObjectStore.prototype.get;
    IDBObjectStore.prototype.get=function(key){
      const result=get.apply(this,arguments);
      if(this.transaction.db.name==='private-owner-graph-topology-v1'&&this.name==='checkpoint'&&key==='latest'){
        window.__fixtureCache.reads++;
        result.addEventListener('success',()=>{
          const item=result.result;
          window.__fixtureCache.lastGotValue=Boolean(item);
          window.__fixtureCache.lastGotView=Boolean(item?.value?.view);
          storedScope=item?.scope;storedEpoch=item?.value?.epoch;
          recordMatch();
        });
      }
      return result;
    };
    const remove=IDBObjectStore.prototype.delete;
    IDBObjectStore.prototype.delete=function(key){
      if(this.transaction.db.name==='private-owner-graph-topology-v1'&&this.name==='checkpoint'&&key==='latest')window.__fixtureCache.deletions++;
      return remove.apply(this,arguments);
    };
    window.fetch=function(input,...args){
      const url=new URL(input instanceof Request?input.url:input,location.href);
      const answer=fetchOriginal.call(this,input,...args);
      if(url.origin===location.origin&&url.pathname==='/api/v1/session'&&(args[0]?.method??'GET')==='GET'){
        void answer.then(r=>r.clone().json()).then(result=>{
          window.__fixtureCache.sessionAuthenticated=result.authenticated===true;
          sessionScope=result.graph_cache_scope;sessionEpoch=result.graph_cache_epoch;
          window.__fixtureCache.sessionScopeShape=/^[a-f0-9]{64}$/.test(sessionScope);
          window.__fixtureCache.sessionEpochShape=/^[a-f0-9]{64}$/.test(sessionEpoch);
          recordMatch();
        }).catch(()=>{});
      }
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
  context.on('request',r=>{
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

  const packed=process.env.GRAPH_BROWSER_VARIANT==='packed';
  await page.goto(origin+(packed?'/visual/':''));
  if(packed){
    const release=page.locator('.visual-release-version');
    await expect(release).toHaveText('Визуал: assertion-memory-visual-v0.0.0-browser-fixture');
    await expect(release).toHaveCSS('position','fixed');
    await expect(page.locator('#visual-queue-open')).toBeHidden();
  }
  expect(await page.evaluate(()=>Boolean(document.createElement('canvas').getContext('webgl2'))),
    'Installed sandboxed browser must supply WebGL2').toBe(true);
  await page.locator('#password').fill('synthetic-only-owner');
  await page.locator('#login-button').click();
  await expect(status).toHaveText('Live · обновляется');
  await waitGraph();
  await expect(page.locator('#space')).toBeVisible();
  if(packed){
    expect(requests.some(r=>/^\/visual\/assets\/index-[\w-]+\.js$/.test(r.path))).toBe(true);
    expect(requests.some(r=>/^\/visual\/assets\/index-[\w-]+\.css$/.test(r.path))).toBe(true);
    expect(requests.some(r=>/^\/visual\/assets\/graph\.worker-[\w-]+\.js$/.test(r.path))).toBe(true);
  }
  if(packed)await expect(page.locator('#visual-queue-open')).toBeVisible();
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
  const resumeAttempt=eventReq();
  if(packed&&resumeAttempt[0]?.after!==true){
    console.info('Packed warm checkpoint diagnostic '+JSON.stringify(await page.evaluate(()=>window.__fixtureCache)));
  }
  expect(resumeAttempt).toEqual([{path:'/api/v1/events',wire:'graph-v1',after:true,rev:true}]);
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

  execFileSync(process.env.GRAPH_FIXTURE_PYTHON||'python3',['-c',[
    'import sqlite3,sys',
    "with sqlite3.connect('file:'+sys.argv[1]+'?mode=rw',uri=True) as c:",
    ' assert c.execute("SELECT count(*) FROM memory_assertions").fetchone()==(6,)',
    " c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('zz-qa-live-added','Synthetic update','actor','saved','session','2025-01-01T00:00:01+00:00','2025-01-01T00:00:01+00:00','s','{}'))",
    " c.execute('UPDATE memory_state SET version=version+1 WHERE singleton=1')",
  ].join('\n'),db]);
  await page.waitForFunction(({rev,n})=>{
    const s=window.__graphViewer?.snapshot();
    return s?.revision&&s.revision!==rev&&s.nodeSlots===n+1&&window.__graphViewer.lodAuditData()?.revision===s.revision;
  },{rev:before.revision,n:before.nodeSlots},{timeout:15000});
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
  console.info('Browser verified cold cache, held-network warm paint, one authentic SSE delta, and fixed camera/geometry');

  // Assert the product update above independently; now perform the actual
  // tab hide/return and make sure it doesn't rebuild the settled graph.
  // window.open with no popup features creates a normal tab in the same
  // Chrome window. Drive Chrome's own tab-strip shortcut, not DOM events.
  const [other]=await Promise.all([page.waitForEvent('popup',{timeout:8000}),page.evaluate(()=>{window.open('about:blank','_blank');})]);
  const [mainFocus,otherFocus]=await Promise.all([context.newCDPSession(page),context.newCDPSession(other)]);
  await page.bringToFront();
  await page.waitForFunction(()=>document.visibilityState==='visible'&&document.hasFocus(),null,{timeout:8000});
  const [mainWindow,otherWindow]=await Promise.all([mainFocus.send('Browser.getWindowForTarget'),otherFocus.send('Browser.getWindowForTarget')]);
  expect(mainWindow.windowId,'The two pages must be tabs in the same actual Chrome window').toBe(otherWindow.windowId);
  const tabSwitch=()=>execFileSync('xdotool',['key','--clearmodifiers','ctrl+Tab'],{timeout:4000});
  tabSwitch();
  await expect.poll(()=>page.evaluate(()=>document.visibilityState),{timeout:8000}).toBe('hidden');
  tabSwitch();
  await expect.poll(()=>page.evaluate(()=>document.visibilityState),{timeout:8000}).toBe('visible');
  const returned=await pose();
  expect(returned.doc).toBe(after.doc);
  expect(returned.revision).toBe(after.revision);
  expect(returned.bits).toEqual(after.bits);
  expect(returned.edges).toEqual(after.edges);
  returned.camera.forEach((v,i)=>expect(v).toBeCloseTo(after.camera[i],7));
  expect(eventReq()).toHaveLength(1);
  expect(graphReq()).toEqual([]);
  await Promise.all([mainFocus.detach(),otherFocus.detach()]);
  await other.close();

  // Same packaged frontend and real Graph listener, with only one worker apply
  // artificially rejected. A resume of that cursor would replay the bad delta.
  await page.evaluate(()=>{window.__qaRejectNextDelta=true;});
  execFileSync(process.env.GRAPH_FIXTURE_PYTHON||'python3',['-c',[
    'import sqlite3,sys',
    "with sqlite3.connect('file:'+sys.argv[1]+'?mode=rw',uri=True) as c:",
    ' assert c.execute("SELECT count(*) FROM memory_assertions").fetchone()==(7,)',
    " c.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',('zz-qa-live-recovered','Synthetic retry update','actor','saved','session','2025-01-01T00:00:02+00:00','2025-01-01T00:00:02+00:00','s','{}'))",
    " c.execute('UPDATE memory_state SET version=version+1 WHERE singleton=1')",
  ].join('\n'),db]);
  await page.waitForFunction(({rev,n})=>{
    const s=window.__graphViewer?.snapshot();
    return s?.revision&&s.revision!==rev&&s.nodeSlots===n+1&&window.__graphViewer.lodAuditData()?.revision===s.revision;
  },{rev:returned.revision,n:returned.nodeSlots},{timeout:15000});
  const recovered=await pose();
  expect(await page.evaluate(()=>window.__qaRejectedDeltas)).toBe(1);
  expect(eventReq()).toEqual([
    {path:'/api/v1/events',wire:'graph-v1',after:true,rev:true},
    {path:'/api/v1/events',wire:'graph-v1',after:false,rev:false},
  ]);
  expect(graphReq()).toEqual([]);
  expect(recovered.doc).toBe(returned.doc);
  expect(recovered.bits.slice(0,returned.bits.length)).toEqual(returned.bits);
  expect(recovered.edges.slice(0,returned.edges.length)).toEqual(returned.edges);
  recovered.camera.forEach((v,i)=>expect(v).toBeCloseTo(returned.camera[i],7));
  await expect(status).toHaveText('Live · обновляется');
  console.info('Browser verified real graph recovery from one rejected worker apply without scene movement');
});

test('packed queue remains visible and readable after WebGL creation fails',async({nativeBrowser:{context,page}})=>{
  test.skip(process.env.GRAPH_BROWSER_VARIANT!=='packed','queue overlay is present only in the packaged release');
  const base=realpathSync(process.env.GRAPH_BROWSER_FIXTURE);
  const origin='https://127.0.0.1:'+Number(readFileSync(join(base,'fixture-port'),'utf8'));
  await context.route('**/*',route=>{
    const u=new URL(route.request().url());
    return u.origin===origin||['about:','blob:','data:'].includes(u.protocol)?route.continue():route.abort();
  });
  await page.addInitScript(()=>{
    const original=HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext=function(type,...args){
      return type==='webgl2'?null:original.call(this,type,...args);
    };
  });
  const graph=[],wire=[],queue=[];
  page.on('request',request=>{
    const url=new URL(request.url());
    if(url.origin!==origin)return;
    if(url.pathname==='/api/v1/graph'||url.pathname.startsWith('/api/v1/graph/'))graph.push(url.pathname);
    if(url.pathname==='/api/v1/events'&&url.searchParams.get('wire')==='graph-v1')wire.push(url.pathname);
    if(url.pathname==='/api/v1/capabilities'&&url.searchParams.get('queue_status')==='1')queue.push(url.pathname);
  });
  await page.goto(origin+'/visual/');
  const login=page.locator('#password');
  await login.fill('synthetic-only-owner');
  await page.locator('#login-button').click();
  await expect(page.locator('html')).toHaveAttribute('data-owner-graph-failure','baseline-render-failed');
  await page.locator('#visual-queue-open').click();
  await expect(page.locator('#visual-queue')).toBeVisible();
  await expect(page.locator('#visual-queue .queue-meta')).toHaveText('TEST · База fixture-base');
  expect(queue.length).toBeGreaterThan(0);
  expect(wire.length).toBeGreaterThan(0);
  expect(graph).toEqual([]);
});

test('TEST owner can use visible node preview without any mutation authority',async({nativeBrowser:{context,page}})=>{
  test.skip(process.env.GRAPH_BROWSER_VARIANT==='packed','source-built native Visual owns the preview button');
  const base=realpathSync(process.env.GRAPH_BROWSER_FIXTURE);
  const port=Number(readFileSync(join(base,'preview-port'),'utf8'));
  expect(Number.isInteger(port)&&port>1024&&port<65536).toBe(true);
  const origin='https://127.0.0.1:'+port;
  const node=readFileSync(join(base,'preview-node'),'utf8');
  const expectedSHA=readFileSync(join(base,'preview-db-sha'),'utf8');
  const nodePath='/api/v1/nodes/'+encodeURIComponent(node);
  const entry=new URL(origin);
  entry.searchParams.set('kind','node');
  entry.searchParams.set('id',node);
  const effects=[];
  let loggedIn=false;
  await context.route('**/*',route=>{
    const req=route.request(), url=new URL(req.url());
    if(url.origin!==origin)return ['about:','blob:','data:'].includes(url.protocol)?route.continue():route.abort();
    if(loggedIn&&url.pathname.startsWith('/api/')&&!['GET','HEAD'].includes(req.method())){
      effects.push([req.method(),url.pathname]);
      return route.abort();
    }
    return route.continue();
  });
  await page.goto(entry.href,{waitUntil:'domcontentloaded'});
  const toggle=page.getByRole('button',{name:'Проверить удаление',exact:true});
  await expect(toggle).toHaveCount(0);
  const anon=await page.evaluate(async()=>await fetch('/api/v1/capabilities',{cache:'no-store'}).then(async r=>({status:r.status,value:r.ok?await r.json():null})));
  expect(anon.status===401||anon.value?.capabilities?.owner_node_removal_preview!==true).toBe(true);
  await page.locator('#password').fill('synthetic-only-owner');
  const login=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/v1/session'&&r.request().method()==='POST'&&r.status()===200);
  await page.locator('#login-button').click();
  await login;
  loggedIn=true;
  const caps=await page.evaluate(async()=>await fetch('/api/v1/capabilities',{cache:'no-store'}).then(r=>r.json()));
  expect(caps.capabilities.owner_node_removal_preview).toBe(true);
  await expect(page.locator('#reader .assertion-text')).toHaveText('Fictional node preview');
  const response=page.waitForResponse(r=>{const u=new URL(r.url());return u.origin===origin&&u.pathname===nodePath&&u.searchParams.get('preview_removal')==='1';});
  await toggle.click();
  const preview=await response;
  expect(preview.request().method()).toBe('GET');
  expect(preview.status()).toBe(200);
  expect(await preview.json()).toEqual({eligible:true,reason:null,write_authority:false});
  await expect(page.getByText('Узел прошёл проверку. Удаление недоступно.',{exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Удалить',exact:true})).toHaveCount(0);
  expect(effects).toEqual([]);
  expect(createHash('sha256').update(readFileSync(join(base,'preview.sqlite3'))).digest('hex')).toBe(expectedSHA);
});

test('hello without graph data retries once and reaches real Live without a graph GET',async({nativeBrowser:{context,page}})=>{
  test.setTimeout(45_000);
  const base=realpathSync(process.env.GRAPH_BROWSER_FIXTURE);
  const origin='https://127.0.0.1:'+Number(readFileSync(join(base,'fixture-port'),'utf8'));
  const packed=process.env.GRAPH_BROWSER_VARIANT==='packed';
  await context.route('**/*',route=>{
    const u=new URL(route.request().url());
    return u.origin===origin||['about:','blob:','data:'].includes(u.protocol)?route.continue():route.abort();
  });
  await page.addInitScript(()=>{
    const fetchOriginal=window.fetch;
    let sessions=0;
    window.fetch=async function(input,options){
      const url=new URL(input instanceof Request?input.url:input,location.href);
      const response=await fetchOriginal.call(this,input,options);
      if(url.origin!==location.origin||url.pathname!=='/api/v1/events'||url.searchParams.get('wire')!=='graph-v1'||++sessions!==1)
        return response;
      if(!response.ok||!response.body)return response;
      const reader=response.body.getReader();
      const decoder=new TextDecoder(),encoder=new TextEncoder();
      return new Response(new ReadableStream({
        async start(controller){
          let buffered='';
          try{
            for(;;){
              const {done,value}=await reader.read();
              if(done)throw new Error('No real hello received from fixture');
              buffered+=decoder.decode(value,{stream:true});
              const boundary=/\r?\n\r?\n/.exec(buffered);
              if(boundary){
                const hello=buffered.slice(0,boundary.index+boundary[0].length);
                if(!hello.startsWith('event: hello\n'))throw new Error('Fixture did not open with hello');
                controller.enqueue(encoder.encode(hello));
                options.signal.addEventListener('abort',()=>controller.error(options.signal.reason),{once:true});
                break;
              }
            }
          }finally{void reader.cancel().catch(()=>{});}
        }
      }),{status:response.status,headers:response.headers});
    };
  });
  const events=[],graph=[];
  page.on('request',req=>{
    const url=new URL(req.url());
    if(url.origin!==origin)return;
    if(url.pathname==='/api/v1/events'&&url.searchParams.get('wire')==='graph-v1')events.push(url.pathname);
    if(url.pathname==='/api/v1/graph'||url.pathname.startsWith('/api/v1/graph/'))graph.push(url.pathname);
  });
  await page.goto(origin+(packed?'/visual/':''));
  await page.locator('#password').fill('synthetic-only-owner');
  await page.locator('#login-button').click();
  const attempt=await page.waitForFunction(()=>{
    const previous=JSON.parse(document.documentElement.dataset.ownerGraphLastAttempt??'null');
    return previous?.attempt===1&&previous.outcome==='initial-state-timeout'?previous:null;
  },null,{timeout:25_000});
  const timings=await attempt.jsonValue();
  expect(timings.headers_ms).not.toBeNull();
  expect(timings.hello_ms).not.toBeNull();
  await expect(page.locator('#connection-status')).toHaveText('Live · обновляется',{timeout:10_000});
  await page.waitForFunction(()=>window.__graphViewer?.snapshot().renderedFrames>1&&Boolean(window.__graphViewer.lodAuditData()));
  expect(events).toHaveLength(2);
  expect(graph).toEqual([]);
});
