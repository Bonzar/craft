import { test, expect } from '@playwright/test';

test.skip(process.env.GRAPH_VIEWER_QA_READY!=='1','Root browser coordination flag required.');

test('logout removes saved source DOM and topic labels before a clean owner relogin',async({page},info)=>{
  const password=process.env.GRAPH_VIEWER_QA_PASSWORD;
  expect(password).toBeTruthy();
  const authenticate=async()=>{
    await page.getByLabel('Пароль владельца',{exact:true}).fill(password);
    await page.getByRole('button',{name:'Войти',exact:true}).click();
    await page.waitForFunction(()=>window.__graphViewer?.snapshot().nodeSlots===100000&&window.__graphViewer.snapshot().renderedFrames>12);
  };
  await page.goto('/?kind=node&id=qa-node-00000000&mode=current');
  await page.getByLabel('Пароль владельца',{exact:true}).waitFor({state:'visible'});
  await authenticate();
  const caps=await(await page.request.get('/api/v1/capabilities')).json();
  expect(caps.readiness.some(row=>row.code==='synthetic_fixture'),'private-source screenshots and probes refused').toBe(true);
  await expect(page.getByRole('article',{name:'Текст утверждения',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Исходный фрагмент 1',exact:true}).click();
  const source=page.getByRole('article',{name:'Текст источника',exact:true});
  await expect(source).toBeVisible();
  const reader=page.getByRole('region',{name:'Карточка',exact:true,includeHidden:true});
  const sourceWasLoaded=(await reader.textContent()).includes('Контекст до фрагмента');
  expect(sourceWasLoaded).toBe(true);
  const topics=page.getByRole('button',{name:/^Тема:/,includeHidden:true});
  await page.getByRole('button',{name:'Вся вселенная',exact:true}).click();
  await expect.poll(()=>topics.count()).toBeGreaterThan(0);
  const beforeTopicCount=await topics.count();

  await page.getByRole('button',{name:'Выйти',exact:true}).click();
  await expect(page.getByLabel('Пароль владельца',{exact:true})).toBeVisible();
  const privateSourceRetained=(await reader.textContent()).includes('Контекст до фрагмента');
  const afterLogoutTopics=await topics.count();
  expect.soft(privateSourceRetained,'saved private source must leave DOM on logout').toBe(false);
  expect.soft(afterLogoutTopics,'source topic labels must leave DOM on logout').toBe(0);

  await authenticate();
  const afterReloginTopics=await topics.count();
  expect.soft(afterReloginTopics,'relogin must not accumulate old topic buttons').toBeLessThanOrEqual(beforeTopicCount);
  const evidence={sourceWasLoaded,privateSourceRetained,beforeTopicCount,afterLogoutTopics,afterReloginTopics,sourceNodes:100000,sourceEdges:1000000};
  await info.attach('logout-retention-and-relogin',{body:Buffer.from(JSON.stringify(evidence,null,2)),contentType:'application/json'});
});
