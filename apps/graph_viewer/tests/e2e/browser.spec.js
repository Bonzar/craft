import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {auditProjection} from './lod-audit.js';
import { startChromeFrameTrace } from './frame-trace.js';
import { installPhaseObserver } from './phase-observer.js';
const ready = process.env.GRAPH_VIEWER_QA_READY === '1';
const manifestPath = process.env.GRAPH_VIEWER_QA_MANIFEST;
const manifest = manifestPath ? JSON.parse(fs.readFileSync(manifestPath, 'utf8')) : null;
test.skip(!ready, 'Root has not signaled browser readiness; no viewer acceptance run.');
test.skip(!manifest?.synthetic, 'Screenshots require the explicitly synthetic fixture.');
async function temporalExpected(mode,at,history=true,undated='separate'){
 const python=process.env.GRAPH_VIEWER_QA_PYTHON||'python3';
 return JSON.parse(execFileSync(python,[fileURLToPath(new URL('./temporal-expected.py',import.meta.url)),'--manifest',manifestPath,'--mode',mode,'--at',at,'--history',history?'1':'0','--undated',undated],{encoding:'utf8'}));
}
async function sourceForLod(page,projection){
 const reply=await page.request.get('/api/v1/graph?mode=current&history=0');expect(reply.status()).toBe(200);const m=await reply.json();expect(m.revision).toBe(projection.revision);
 const[nr,er,tr]=await Promise.all([page.request.get(m.nodes.url),page.request.get(m.edges.url),page.request.get(m.topics_url)]);const nodes=await nr.body(),bytes=await er.body(),t=await tr.json();const visible=new Uint8Array(m.counts.nodes);for(let i=0;i<visible.length;i++)visible[i]=(nodes.readUInt32LE(i*16+12)&3)===0?1:0;
 const members=new Map(t.memberships.map(row=>[row.topic_index,row.node_indices]));return{visible,edges:new Uint32Array(bytes.buffer,bytes.byteOffset,bytes.byteLength/4),edgeActive:new Uint8Array(m.counts.edges).fill(1),topics:t.topics.map(topic=>({id:topic.id,index:topic.index,source_ref:topic.source_ref,members:members.get(topic.index)||[]})),links:t.links};
}

async function instrument(page) {
  if(process.env.GRAPH_VIEWER_QA_PHASE_PROFILE==='1')await installPhaseObserver(page);
  await page.addInitScript(() => {
    const data = { graphLoadStarted: null, eventTimings: [], calls: 0, pointCountMax: 0, lineIndexCountMax: 0, lineDraws: 0, bufferBytesMax: 0, frameDrawTimes: [], lastDrawAt: -1 };
    Object.defineProperty(window, '__qaGL', { value: data, writable: false });
    const originalFetch = window.fetch;
    window.fetch = function (...args) {
      const value = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
      if (/\/api\/v1\/graph(?:\?|$)/.test(value) && data.graphLoadStarted === null) data.graphLoadStarted = performance.now();
      return Reflect.apply(originalFetch, this, args);
    };
    try {
      new PerformanceObserver(list => {
        for (const entry of list.getEntries()) data.eventTimings.push({ name: entry.name, startTime: entry.startTime, duration: entry.duration, interactionId: entry.interactionId });
      }).observe({ type: 'event', buffered: true, durationThreshold: 16 });
    } catch { /* Engine has no Event Timing; report unmeasured, do not invent pass. */ }
    for (const Constructor of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
      if (!Constructor) continue;
      for (const method of ['drawArrays', 'drawElements', 'drawArraysInstanced', 'drawElementsInstanced']) {
        const original = Constructor.prototype[method];
        if (!original) continue;
        Constructor.prototype[method] = function (...args) {
          const result = Reflect.apply(original, this, args);
          const count = method.includes('Elements') ? args[1] : args[2];
          const instances = method.includes('Instanced') ? args.at(-1) : 1;
          data.calls++;
          if (args[0] === this.POINTS) data.pointCountMax = Math.max(data.pointCountMax, count * instances);
          if (args[0] === this.LINES || args[0] === this.LINE_STRIP) {
            data.lineIndexCountMax = Math.max(data.lineIndexCountMax, count * instances); data.lineDraws++;
          }
          const now = performance.now();
          if (data.lastDrawAt < 0 || now - data.lastDrawAt > 2) {
            data.frameDrawTimes.push(now);
            if (data.frameDrawTimes.length > 3000) data.frameDrawTimes.shift();
          }
          data.lastDrawAt = now;
          return result;
        };
      }
      const original = Constructor.prototype.bufferData;
      Constructor.prototype.bufferData = function (...args) {
        data.bufferBytesMax = Math.max(data.bufferBytesMax, typeof args[1] === 'number' ? args[1] : args[1]?.byteLength || 0);
        return Reflect.apply(original, this, args);
      };
    }
  });
}
async function evidence(info, name, value) {
  await info.attach(name, { body: Buffer.from(JSON.stringify(value, null, 2)), contentType: 'application/json' });
}
async function open(page, info) {
  expect(process.env.GRAPH_VIEWER_QA_PASSWORD).toBeTruthy();
  await instrument(page);
  await page.goto('/');
  const login = page.getByRole('button', { name: 'Войти', exact: true });
  await expect(login).toBeVisible();
  await page.getByLabel('Пароль владельца', { exact: true }).fill(process.env.GRAPH_VIEWER_QA_PASSWORD);
  await login.click();
  await page.waitForFunction(() => window.__graphViewer?.snapshot().nodeSlots > 0 && window.__graphViewer.snapshot().renderedFrames > 0 && window.__qaGL.lineDraws > 0);
  const snapshot = await page.evaluate(() => window.__graphViewer.snapshot());
  const firstMapTiming = await page.evaluate(() => ({ graphLoadStarted: window.__qaGL.graphLoadStarted, observedUsableAt: performance.now(), eventTimings: window.__qaGL.eventTimings }));
  if (firstMapTiming.graphLoadStarted !== null) expect(firstMapTiming.observedUsableAt - firstMapTiming.graphLoadStarted).toBeLessThanOrEqual(10000);
  await evidence(info, 'first-complete-map-timing', { ...firstMapTiming, ms: firstMapTiming.graphLoadStarted === null ? null : firstMapTiming.observedUsableAt - firstMapTiming.graphLoadStarted, status: firstMapTiming.graphLoadStarted === null ? 'NOT_MEASURED' : 'OBSERVED' });
  expect(snapshot.nodeSlots).toBe(manifest.counts.nodes);
  expect(snapshot.edgeSlots).toBe(manifest.counts.edges);
  const environment = await page.evaluate(() => ({ userAgent: navigator.userAgent, dpr: devicePixelRatio, viewport: { width: innerWidth, height: innerHeight }, platform: navigator.platform }));
  await evidence(info, 'environment-and-scene', { ...environment, actualBrowserVersion: page.context().browser()?.version(), project: info.project.name,
    hardware: 'MacBookAir10,1 Apple M1 8GB macOS26.4', physicalPhone: false, mobileEmulation: info.project.name.includes('emulation'),
    network: 'local loopback; external/mobile 10Mbps/100ms not certified', sourceCounts: manifest.counts, sourceDigests: manifest.digests, snapshot });
  return snapshot;
}
async function waitCamera(page) {
  await page.waitForFunction(() => {
    const position=window.__graphViewer.snapshot().camera.position;
    const previous=window.__qaCameraPosition;
    const stable=previous&&position.every((value,index)=>Math.abs(value-previous[index])<.0001);
    window.__qaCameraPosition=position;window.__qaCameraStable=stable?(window.__qaCameraStable||0)+1:0;
    return window.__qaCameraStable>=3;
  });
}
async function pick(page,id,info=null,{enforceCardLimit=true}={}) {
  const revision=await page.evaluate(()=>window.__graphViewer.snapshot().revision);
  const response=await page.request.get(`/api/v1/nodes/${encodeURIComponent(id)}?revision=${encodeURIComponent(revision)}`);
  expect(response.status()).toBe(200);const node=await response.json();
  // A full-universe projection is not an occlusion oracle. Navigate through the
  // visible search UI, then zoom close and close the card before a real star tap.
  await page.getByRole('radio',{name:'По словам',exact:true}).check();
  const input=page.getByLabel('Поиск по вселенной',{exact:true});
  await input.fill(id===manifest.cases.literal_html?'буквальный текст':id.slice(-8));
  await input.press('Enter');
  const firstLine=node.text.split('\n')[0].slice(0,55).replace(/\s+/g,' ').trim();
  const match=firstLine.replace(/[.*+?^${}()|[\]\\]/g,'\\$&');
  const results=page.getByRole('region',{name:'Результаты поиска',exact:true});
  await results.getByRole('button',{name:new RegExp(match)}).first().click();
  await expect.poll(()=>page.evaluate(()=>window.__graphViewer.snapshot().selectedNodeId)).toBe(id);
  await waitCamera(page);
  await page.getByRole('button',{name:'Закрыть карточку',exact:true}).click();
  await page.getByRole('button',{name:'Закрыть результаты поиска',exact:true}).click();
  await expect.poll(()=>page.evaluate(()=>window.__graphViewer.snapshot().selectedNodeId)).toBeNull();
  for(let step=0;step<4;step++)await page.getByRole('button',{name:'Приблизить',exact:true}).click();
  await waitCamera(page);
  await expect.poll(()=>page.evaluate(ordinal=>window.__graphViewer.projectNode(ordinal),node.ordinal)).toMatchObject({visible:true});
  const position=await page.evaluate(ordinal=>window.__graphViewer.projectNode(ordinal),node.ordinal);
  const started=await page.evaluate(()=>performance.now());
  await page.mouse.click(position.x,position.y);
  await page.waitForFunction(({id,prefix})=>{const article=document.querySelector('[role=article][aria-label="Текст утверждения"]');return window.__graphViewer.snapshot().selectedNodeId===id&&article&&article.getClientRects().length>0&&article.textContent.startsWith(prefix);},{id,prefix:node.text.slice(0,60)});
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  const cardMs=await page.evaluate(start=>performance.now()-start,started);
  if(info)await evidence(info,'card-first-readable',{id,cardMs,targetMs:1000,targetMet:cardMs<=1000,enforcedInThisCase:enforceCardLimit,condition:'Warm public lookup/search then real star pick; rAF-visible text plus two-paint observation; not a cold-card or compositor certification.'});
  if(enforceCardLimit)expect(cardMs).toBeLessThanOrEqual(1000);return node;
}

test('whole graph reaches actual WebGL and first/last stars can be picked', async ({ page }, info) => {
  await open(page, info);
  await page.waitForFunction(()=>Boolean(window.__graphViewer.lodAuditData?.()));
  const coarse=await page.evaluate(()=>window.__graphViewer.lodAuditData());const source=await sourceForLod(page,coarse);await evidence(info,'default-lod-exact-source-partition',auditProjection(coarse,source));
  await page.getByRole('button',{name:'Все звёзды',exact:true}).click();await page.waitForFunction(()=>window.__graphViewer.snapshot().lod.coverage.represented_nodes===0);const before=await page.evaluate(()=>window.__graphViewer.snapshot().renderedFrames);await page.waitForFunction(before=>window.__graphViewer.snapshot().renderedFrames>before+2,before);
  const scene=await page.evaluate(()=>window.__graphViewer.snapshot());const expanded=await page.evaluate(()=>window.__graphViewer.lodAuditData());await evidence(info,'expanded-exact-source-partition',auditProjection(expanded,source));
  await expect.poll(() => page.evaluate(() => window.__qaGL.calls)).toBeGreaterThan(0);
  const gl = await page.evaluate(() => ({ ...window.__qaGL }));
  expect(gl.pointCountMax).toBeGreaterThanOrEqual(scene.visibleNodes);
  expect(gl.lineIndexCountMax).toBeGreaterThanOrEqual(scene.visibleEdges * 2);
  expect(scene.positionBytes).toBeGreaterThanOrEqual(scene.nodeSlots * 12);
  expect(scene.indexBytes).toBeGreaterThanOrEqual(scene.visibleEdges * 8);
  await evidence(info, 'independent-real-gl-submissions', { gl, scene, limit: 'Submissions plus screenshots/picking; phone and paint timing separate.' });
  await page.screenshot({ path: info.outputPath('full-universe.png') });
  await pick(page, manifest.first_id, info);
  await page.keyboard.press('Escape');
  await pick(page, manifest.last_id, info);
  await page.screenshot({ path: info.outputPath('last-star-picked.png') });
});

test('rotation and zoom submit at least30 real scene frames per second with million edges', async ({ page }, info) => {
  const initial = await open(page, info);
  const bounds = await page.locator('canvas').first().boundingBox();
  expect(bounds).toBeTruthy();
  const finishTrace = await startChromeFrameTrace(page);
  const start = await page.evaluate(() => ({ time: performance.now(), snapshot: window.__graphViewer.snapshot(), gl: { ...window.__qaGL } }));
  await page.mouse.move(bounds.x + bounds.width * .35, bounds.y + bounds.height * .55);
  await page.mouse.down();
  for (let step=1;step<=90;step++) {
    await page.mouse.move(bounds.x+bounds.width*(.35+.30*step/90),bounds.y+bounds.height*(.55+.05*step/90));
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  }
  await page.mouse.up();
  for (let step=0;step<20;step++) {
    await page.mouse.wheel(0,-15);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(resolve)));
  }
  const end = await page.evaluate(() => ({ time: performance.now(), snapshot: window.__graphViewer.snapshot(), gl: { ...window.__qaGL } }));
  if(finishTrace){const trace=await finishTrace();await evidence(info,'chrome-compositor-trace-summary',trace.summary);await info.attach('chrome-compositor-trace',{body:Buffer.from(trace.raw),contentType:'application/json'});}
  expect(end.snapshot.camera).not.toEqual(initial.camera);
  expect(end.snapshot.edgeSlots).toBe(1000000);
  expect(end.gl.lineDraws).toBeGreaterThan(start.gl.lineDraws);
  const times = end.snapshot.frameTimes.filter(value => value >= start.time && value <= end.time);
  const intervals = times.slice(1).map((time, index) => time - times[index]);
  const sorted = [...intervals].sort((a, b) => a - b);
  const fps = times.length > 1 ? 1000 * (times.length - 1) / (times.at(-1) - times[0]) : 0;
  await evidence(info, 'rendered-orbit-frame-samples', { fps, frameCount: times.length, intervals, p95IntervalMs: sorted[Math.floor(sorted.length * .95)], glSubmissionTicks: end.gl.frameDrawTimes.filter(value => value >= start.time).length, snapshot: end.snapshot });
  expect(fps,'necessary real-render submission rate; compositor certification is separate').toBeGreaterThanOrEqual(30);
  await page.screenshot({ path: info.outputPath('rotated-zoomed.png') });
});

test('word search finds distant universe sentinel and navigates to correct star', async ({ page }, info) => {
  await open(page, info);
  await page.getByRole('radio', { name: 'По словам', exact: true }).check();
  const input = page.getByLabel('Поиск по вселенной', { exact: true });
  await input.fill('ДАЛЁКИЙ УЗЕЛ ВСЕЙ ВСЕЛЕННОЙ');
  const started = await page.evaluate(() => performance.now());
  await input.press('Enter');
  const result = page.getByRole('button', { name: /ДАЛЁКИЙ УЗЕЛ ВСЕЙ ВСЕЛЕННОЙ/ }).first();
  await expect(result).toBeVisible();
  const elapsedMs = await page.evaluate(start => performance.now() - start, started);
  expect(elapsedMs).toBeLessThanOrEqual(2000);
  await result.click();
  await expect.poll(() => page.evaluate(() => window.__graphViewer.snapshot().selectedNodeId)).toBe(manifest.cases.unaffiliated_sentinel);
  await evidence(info, 'painted-search-result', { elapsedMs, stableId: manifest.cases.unaffiliated_sentinel });
  await page.screenshot({ path: info.outputPath('search-card.png') });
});

test('literal source markup stays inert and ordinary card text wraps at six widths', async ({ page }, info) => {
  await open(page, info);
  for (const width of [320,375,768,1024,1440,1920]) {
    await page.setViewportSize({ width, height: 900 });
    await pick(page, manifest.cases.literal_html);
    await expect(page.getByRole('article',{name:'Текст утверждения',exact:true})).toContainText(/<img src=x onerror=/);
    expect(await page.evaluate(() => window.__fixtureXss)).toBeUndefined();
    expect(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), `overflow at ${width}`).toBe(false);
    await page.screenshot({ path: info.outputPath(`literal-${width}.png`) });
    await page.keyboard.press('Escape');
  }
  await pick(page, manifest.cases.long_assertion);
  await expect(page.getByRole('article',{name:'Текст утверждения',exact:true})).toContainText(`НАЧАЛО ${manifest.cases.long_assertion}`);
  await page.screenshot({ path: info.outputPath('long-card.png') });
});

test('long assertion and entire saved source remain readable with exact Unicode fragment', async ({ page }, info) => {
  await open(page, info);
  const initial = await pick(page, manifest.cases.long_assertion);
  const card = page.getByRole('region', { name: 'Карточка', exact: true });
  const prose = card.getByRole('article', { name: 'Текст утверждения', exact: true });
  let expected = initial.text;
  let cursor = initial.next_text_offset;
  while (cursor !== null) {
    const oldLength = (await prose.textContent()).length;
    await card.getByRole('button', { name: 'Продолжить текст', exact: true }).click();
    await expect.poll(async () => (await prose.textContent()).length).toBeGreaterThan(oldLength);
    const response = await page.request.get(`/api/v1/nodes/${encodeURIComponent(initial.id)}?revision=${encodeURIComponent(initial.revision)}&offset=${cursor}&limit=12000`);
    expect(response.status()).toBe(200);
    const continuation = await response.json();
    expected += continuation.text; cursor = continuation.next_text_offset;
  }
  expect(await prose.textContent()).toBe(expected);
  await expect(prose).toContainText(`КОНЕЦ ${initial.id}`);
  await card.getByRole('button', { name: 'Исходный фрагмент 1', exact: true }).click();
  await expect(card.getByRole('heading', { name: 'Сохранённый источник', exact: true })).toBeVisible();
  const source = card.getByRole('article', { name: 'Текст источника', exact: true });
  await expect(source.locator('mark')).toHaveText(manifest.sources.deep_unicode.quote);
  await page.screenshot({ path: info.outputPath('exact-source-fragment.png') });
  const [firstSourceReply] = await Promise.all([
    page.waitForResponse(response => response.url().includes('/api/v1/sources/') && new URL(response.url()).searchParams.get('offset') === '0' && response.status() === 200),
    card.getByRole('button', { name: 'Весь сохранённый источник', exact: true }).click(),
  ]);
  const firstSourcePage = await firstSourceReply.json();
  await expect.poll(() => source.textContent()).toBe(firstSourcePage.text);
  let savedCharacters = 0; let pages = 0;
  while (true) {
    await expect(source).toBeVisible();
    savedCharacters += Array.from(await source.textContent()).length; pages++;
    const next = card.getByRole('button', { name: 'Следующая часть источника', exact: true });
    if (!(await next.isVisible())) break;
    const [reply] = await Promise.all([
      page.waitForResponse(response => response.url().includes('/api/v1/sources/') && new URL(response.url()).searchParams.has('offset') && response.status() === 200),
      next.click(),
    ]);
    const nextPage = await reply.json();
    expect(nextPage.offset).toBe(savedCharacters);
    await expect.poll(() => source.textContent()).toBe(nextPage.text);
  }
  await expect(source).toContainText('КОНЕЦ СОХРАНЁННОГО ИСТОЧНИКА');
  await evidence(info, 'complete-reading-journey', { assertionCharacters: Array.from(expected).length, sourceCharactersRead: savedCharacters, sourcePagesRead: pages, exactCodepointFocus: manifest.sources.deep_unicode.start });
  await page.screenshot({ path: info.outputPath('source-tail.png') });
});

test('history perspectives change source slice while surfacing undated knowledge', async ({ page }, info) => {
  await open(page, info);
  for (const mode of ['known_then', 'known_now']) {
    await page.getByRole('button', { name: /История/ }).click();
    const panel = page.getByRole('region', { name: 'История вселенной', exact: true });
    await panel.getByRole('combobox',{name:/Перспектива/}).selectOption(mode);
    await panel.getByLabel('Дата', { exact: true }).fill('2026-01-05');
    await panel.getByRole('combobox',{name:/Часовой пояс/}).selectOption('UTC');
    await panel.getByLabel('Показывать заменённые и отменённые').check();
    await panel.getByRole('button', { name: 'Показать вселенную', exact: true }).click();
    await expect.poll(() => page.evaluate(() => window.__graphViewer.snapshot().selector.mode)).toBe(mode);
    const expected=await temporalExpected(mode,'2026-01-05T23:59:59.999Z',true);
    await expect.poll(() => page.evaluate(() => window.__graphViewer.snapshot().nodeSlots)).toBe(expected.counts.nodes);
    await expect.poll(() => page.evaluate(() => window.__graphViewer.snapshot().edgeSlots)).toBe(expected.counts.edges);
    const snapshot = await page.evaluate(() => window.__graphViewer.snapshot());
    expect(Date.parse(snapshot.selector.at)).toBe(Date.parse('2026-01-05T23:59:59.999Z'));
    await evidence(info, `history-${mode}`, snapshot);
    await page.screenshot({ path: info.outputPath(`history-${mode}.png`) });
    if (mode === 'known_now') {
      await page.getByRole('button', { name: /История/ }).click();
      await expect(panel.getByText(/Без даты события:/)).toBeVisible();
      await panel.getByRole('combobox',{name:/Сведения без даты события/}).selectOption('only');
      await panel.getByRole('button', { name: 'Показать вселенную', exact: true }).click();
      const undated=await temporalExpected(mode,'2026-01-05T23:59:59.999Z',true,'only');await expect.poll(() => page.evaluate(() => window.__graphViewer.snapshot().nodeSlots)).toBe(undated.counts.nodes);
    }
  }
});

test('ready live supersession preserves open text, source scroll and both cameras', async ({ page, browser }, info) => {
  test.skip(!process.env.GRAPH_VIEWER_QA_PYTHON || process.env.GRAPH_VIEWER_QA_ALLOW_FIXTURE_WRITES !== '1' || manifest.writable_live_probes !== true, 'Explicit writable clone and fixture-stimulus opt-in required.');
  await open(page, info);
  await pick(page,manifest.first_id,info,{enforceCardLimit:false});
  const context = await browser.newContext({ baseURL: process.env.GRAPH_VIEWER_QA_URL, viewport: { width: 1440, height: 960 } });
  const sourcePage = await context.newPage();
  let probeFile = null;
  try {
    await open(sourcePage, info);
    await pick(sourcePage,manifest.first_id,info,{enforceCardLimit:false});
    await sourcePage.getByRole('button', { name: 'Исходный фрагмент 1', exact: true }).click();
    await expect(sourcePage.getByRole('article', { name: 'Текст источника', exact: true })).toBeVisible();
    for (const target of [page, sourcePage]) {
      await expect(target.getByText(/Live · (подключено|обновляется|обновлено)/)).toBeVisible();
      await target.waitForFunction(() => {
        const camera=window.__graphViewer.snapshot().camera.position;
        const previous=window.__qaLastLiveCamera;
        const stable=previous&&camera.every((value,index)=>Math.abs(value-previous[index])<0.0001);
        window.__qaLastLiveCamera=camera;
        window.__qaLiveStable=stable?(window.__qaLiveStable||0)+1:0;
        return window.__qaLiveStable>=3;
      });
    }
    const assertion = page.getByRole('article', { name: 'Текст утверждения', exact: true });
    await assertion.hover();
    const scrollBeforeWheel=await assertion.evaluate(article=>{let scroll=article.parentElement;while(scroll&&!['auto','scroll'].includes(getComputedStyle(scroll).overflowY))scroll=scroll.parentElement;return scroll?.scrollTop??0;});
    await page.mouse.wheel(0,500);
    await assertion.evaluate(async(article,base)=>{
      let scroll=article.parentElement;while(scroll&&!['auto','scroll'].includes(getComputedStyle(scroll).overflowY))scroll=scroll.parentElement;
      if(!scroll)throw new Error('Reader scroll container unavailable');
      let previous=scroll.scrollTop,stable=0,stableSince=performance.now();
      for(let frame=0;frame<180;frame++){
        await new Promise(resolve=>requestAnimationFrame(resolve));
        const current=scroll.scrollTop;
        const settled=current>=base+450&&Math.abs(current-previous)<.5;
        if(settled){stable++;}else{stable=0;stableSince=performance.now();}
        previous=current;if(stable>=3&&performance.now()-stableSince>=150)return;
      }
      throw new Error('Requested reader wheel did not finish before live probe');
    },scrollBeforeWheel);
    const capture = async (target, label) => target.getByRole('article', { name: label, exact: true }).evaluate(article => {
      let scroll=article.parentElement;
      while(scroll&& !['auto','scroll'].includes(getComputedStyle(scroll).overflowY))scroll=scroll.parentElement;
      return { text:article.textContent,scrollTop:scroll?.scrollTop??0,readingOffsetPx:article.getBoundingClientRect().top-(scroll?.getBoundingClientRect().top??0),camera:window.__graphViewer.snapshot().camera,id:window.__graphViewer.snapshot().selectedNodeId };
    });
    await expect.poll(async()=> (await capture(page,'Текст утверждения')).scrollTop).toBeGreaterThan(100);
    const controlCard=await capture(page,'Текст утверждения');
    const controlSource=await capture(sourcePage,'Текст источника');
    await page.evaluate(()=>new Promise(resolve=>{const start=performance.now();const frame=()=>performance.now()-start>=2500?resolve():requestAnimationFrame(frame);requestAnimationFrame(frame);}));
    const beforeCard=await capture(page,'Текст утверждения');
    const beforeSource=await capture(sourcePage,'Текст источника');
    for(const [before,after] of [[controlCard,beforeCard],[controlSource,beforeSource]]){
      expect(after.text).toBe(before.text);expect(after.id).toBe(before.id);
      expect(Math.abs(after.readingOffsetPx-before.readingOffsetPx),'no-source-change reading anchor control').toBeLessThanOrEqual(1);
    }
    await evidence(info,'no-source-change-control',{durationMs:2500,cardScrollBefore:controlCard.scrollTop,cardScrollAfter:beforeCard.scrollTop,sourceScrollBefore:controlSource.scrollTop,sourceScrollAfter:beforeSource.scrollTop,cardOffsetBefore:controlCard.readingOffsetPx,cardOffsetAfter:beforeCard.readingOffsetPx,sourceOffsetBefore:controlSource.readingOffsetPx,sourceOffsetAfter:beforeSource.readingOffsetPx});
    const { execFile } = await import('node:child_process');
    const { promisify } = await import('node:util');
    const { fileURLToPath } = await import('node:url');
    const run=promisify(execFile);
    const script=fileURLToPath(new URL('../../scripts/live_fixture.py',import.meta.url));
    probeFile=info.outputPath('synthetic-live-probe.json');
    const output=await run(process.env.GRAPH_VIEWER_QA_PYTHON,[script,'--fixture-manifest',manifestPath,'--action','supersede','--probe-file',probeFile]);
    const probe=JSON.parse(output.stdout);
    await page.waitForFunction(()=>Array.from(document.querySelectorAll('button')).some(button=>button.textContent==='Перейти к замене'&&button.getClientRects().length>0),{},{timeout:15000});
    await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
    const elapsedMs=await page.evaluate(ready=>performance.timeOrigin+performance.now()-ready,probe.source_ready_epoch_ms);
    const untimedFunctional=process.env.GRAPH_VIEWER_QA_UNTIMED_FUNCTIONAL==='1';
    if(untimedFunctional)info.annotations.push({type:'verification_scope',description:'Untimed functional run during explicit model warmup. Mandatory live<=2s target is recorded, not certified.'});
    else expect.soft(elapsedMs,'approved source-ready to visible live deadline').toBeLessThanOrEqual(2000);
    const afterCard=await capture(page,'Текст утверждения');
    const afterSource=await capture(sourcePage,'Текст источника');
    const compact=value=>({scrollTop:value.scrollTop,readingOffsetPx:value.readingOffsetPx,camera:value.camera,id:value.id,textCharacters:Array.from(value.text).length});
    await evidence(info,'live-reading-anchor-observed',{elapsedMs,liveTargetMs:2000,liveTargetObservedMet:elapsedMs<=2000,untimedFunctional,probe,beforeCard:compact(beforeCard),afterCard:compact(afterCard),beforeSource:compact(beforeSource),afterSource:compact(afterSource)});
    if(process.env.GRAPH_VIEWER_QA_PHASE_PROFILE==='1'){const primary=await page.evaluate(()=>window.__qaLivePhases);const source=await sourcePage.evaluate(()=>window.__qaLivePhases);const backend=await(await page.request.get('/api/v1/capabilities')).json();await evidence(info,'aggregate-live-phase-profile',{sourceReadyEpochMs:probe.source_ready_epoch_ms,primary,source,backend:backend.live_profile??null});}
    for(const [before,after] of [[beforeCard,afterCard],[beforeSource,afterSource]]) {
      expect(after.text).toBe(before.text);expect(after.id).toBe(before.id);
      expect(Math.abs(after.readingOffsetPx-before.readingOffsetPx)).toBeLessThanOrEqual(1);
      for(const component of ['position','target'])for(let i=0;i<3;i++)expect(Math.abs(after.camera[component][i]-before.camera[component][i])).toBeLessThanOrEqual(.0001);
    }
    expect(await page.evaluate(()=>window.__graphViewer.snapshot().nodeSlots)).toBe(100001);
    await expect.soft(sourcePage.getByRole('button',{name:'Перейти к замене',exact:true}),'live replacement status remains visible while reading source').toBeVisible({timeout:2000});
    await evidence(info,'live-source-ready-to-visible',{ elapsedMs,probe,cardScroll:afterCard.scrollTop,sourceScroll:afterSource.scrollTop,cardAndSourceTextPreserved:true,cameraPreserved:true });
    await page.screenshot({path:info.outputPath('live-superseded-pinned-card.png')});
    await sourcePage.screenshot({path:info.outputPath('live-retained-source.png')});
  } finally {
    if(probeFile&&fs.existsSync(probeFile)) {
      const {execFile}=await import('node:child_process');const {promisify}=await import('node:util');const {fileURLToPath}=await import('node:url');
      await promisify(execFile)(process.env.GRAPH_VIEWER_QA_PYTHON,[fileURLToPath(new URL('../../scripts/live_fixture.py',import.meta.url)),'--fixture-manifest',manifestPath,'--action','cleanup','--probe-file',probeFile]);
    }
    if(probeFile&&fs.existsSync(probeFile)) {
      await expect.poll(async()=>{
        const response=await page.request.get('/api/v1/graph?mode=current&history=0');
        return response.status()===200?(await response.json()).counts.nodes:null;
      },{ timeout:60000 }).toBe(manifest.counts.nodes);
    }
    await context.close();
  }
});
