// Appended as a module to the pinned viewer by visual-overlay.mjs.
;(() => {
  const by = id => document.getElementById('admin-' + id);
  const panel = document.getElementById('memory-admin');
  if (!panel) return;
  const stageName = value => value === 'prod' ? 'PROD' : value === 'test' ? 'TEST' : null;
  const string = value => typeof value === 'string' && value !== '' ? value : '';
  const integer = value => Number.isSafeInteger(value) && value >= 0;
  let stage, capabilities = {}, request = 0, changing = false;
  let reset, resetting = false, enrollment = false, expiry;
  const requests = async (path, data) => {
    const r = await fetch('/visual/admin/' + path, {
      credentials:'same-origin', redirect:'error', cache:'no-store',
      headers: {accept:'application/json', ...(data === undefined ? {} : {'content-type':'application/json'})},
      ...(data === undefined ? {} : {method:'POST', body:JSON.stringify(data)})
    });
    if (!r.ok) throw Error('admin request failed');
    if (r.status === 204) return null;
    if (!/^application\/json(?:\s*;|$)/i.test(r.headers.get('content-type') || '')) throw Error('admin response type');
    return r.json();
  };
  const text = (id, value) => { by(id).textContent = value; };
  function affordances() {
    by('reset-open').disabled = changing || !stage || capabilities.reset_database !== true;
    by('device-open').disabled = changing || !stage || capabilities.enroll_device !== true;
  }
  function make(tag, value) {
    const node = document.createElement(tag);
    if (value) node.textContent = value;
    return node;
  }
  function draw(data) {
    const queues = by('queues');
    queues.replaceChildren();
    if (!Array.isArray(data.queues)) return queues.append(make('p','Список очередей недоступен.'));
    if (!data.queues.length) return queues.append(make('p','База не вернула ни одной очереди.'));
    if (data.global_order === false) queues.append(make('p','Между очередями нет единого порядка; они могут обрабатываться параллельно.'));
    for (const queue of data.queues) {
      if (!queue || !string(queue.id)) continue;
      const section = make('section');
      section.append(make('h3',queue.id + (integer(queue.total) ? ' · ' + queue.total : '')));
      if (string(queue.status)) section.append(make('p',queue.status));
      if (string(queue.ordering)) section.append(make('p','Порядок: ' + queue.ordering));
      const list = make('ul');
      const add = (page) => {
        for (const item of page) {
          if (!item || !string(item.id)) continue;
          const position = integer(item.position) ? '№' + item.position + ' · ' : '';
          const note = [item.id, string(item.phase), string(item.status), string(item.available_at)].filter(Boolean).join(' · ');
          list.append(make('li',position + note));
        }
      };
      if (!Array.isArray(queue.items)) section.append(make('p','Записи очереди недоступны.'));
      else if (!queue.items.length && queue.total === 0) section.append(make('p','Пусто.'));
      else if (!queue.items.length) section.append(make('p','На этой странице записей нет.'));
      else {
        add(queue.items);
        section.append(list);
      }
      let offset = queue.next_offset;
      if (integer(offset) && offset > 0) {
        const more = make('button','Следующие записи');
        more.type = 'button';
        const note = make('p'); note.setAttribute('role','status');
        more.addEventListener('click', async () => {
          more.disabled = true; note.textContent = '';
          try {
            const page = await requests('status?limit=50&offset=' + offset);
            const next = page?.queues?.find(item => item?.id === queue.id);
            if (stageName(page?.stage) !== stage || !next || !Array.isArray(next.items)) throw Error();
            list.replaceChildren(); add(next.items);
            if (!list.parentNode) section.insertBefore(list, more);
            note.textContent = next.items.length ? 'Страница с позиции сдвига ' + offset + '.' : 'На этой странице записей нет.';
            if (integer(next.next_offset) && next.next_offset > offset) offset = next.next_offset;
            else more.hidden = true;
          } catch { note.textContent = 'Не удалось загрузить следующие записи.'; }
          finally { more.disabled = false; }
        });
        section.append(more,note);
      }
      queues.append(section);
    }
  }
  async function refresh() {
    const serial = ++request;
    changing = true; affordances();
    by('refresh').disabled = true;
    text('status','Получаем статус базы…');
    try {
      const response = await requests('status?limit=50&offset=0');
      if (serial !== request) return;
      stage = stageName(response?.stage);
      capabilities = response?.capabilities || {};
      text('badge',[stage || 'Среда неизвестна', string(response?.release_version) || 'версия неизвестна'].join(' · '));
      text('status',string(response?.visual_release_version) ? 'Визуал: ' + response.visual_release_version : '');
      draw(response || {});
    } catch {
      if (serial !== request) return;
      stage = null; capabilities = {};
      text('badge','Статус недоступен');
      text('status','Не удалось получить версию, среду и очереди базы.');
      by('queues').replaceChildren();
    } finally {
      if (serial === request) { changing = false; affordances(); by('refresh').disabled = false; }
    }
  }
  const device = by('device'), dialog = by('reset');
  for (const [node,busy] of [[device,()=>enrollment],[dialog,()=>resetting]]) {
    node.querySelector('[data-close]').addEventListener('click',()=>{ if (!busy()) node.close(); });
    node.addEventListener('cancel',event=>{ if (busy()) event.preventDefault(); });
  }
  by('device-open').addEventListener('click',()=>{
    by('device-form').reset(); by('device-submit').disabled = false;
    text('device-title','Авторизация устройства · ' + stage);
    text('device-status','');
    device.showModal();
  });
  by('device-form').addEventListener('submit',async event=>{
    event.preventDefault();
    const name = by('device-name').value.trim(), fingerprint = by('device-fingerprint').value;
    if (enrollment || changing || !stage || capabilities.enroll_device !== true || !name || name.length > 80 || !/^[a-f0-9]{64}$/.test(fingerprint)) return;
    enrollment = true; by('device-submit').disabled = true;
    text('device-status','Авторизуем устройство…');
    try {
      await requests('devices',{name,fingerprint});
      by('device-form').reset();
      text('device-status','Устройство авторизовано в ' + stage + '.');
    } catch { text('device-status','Не удалось подтвердить авторизацию. Проверьте состояние перед повтором.'); }
    finally { enrollment = false; }
  });
  const resetInput = by('reset-input'), submit = by('reset-submit');
  const enableReset = () => {
    submit.disabled = !reset || resetting || changing || reset.deadline <= Date.now() || resetInput.value !== reset.phrase;
  };
  resetInput.addEventListener('input',enableReset);
  dialog.addEventListener('close',()=>{ reset = null; clearTimeout(expiry); resetInput.value = ''; });
  by('reset-open').addEventListener('click',async ()=>{
    if (changing || !stage || capabilities.reset_database !== true) return;
    const lane = stage;
    reset = null; submit.disabled = true; resetInput.disabled = true; resetInput.value = '';
    text('reset-warning','Вы удалите данные базы ' + lane + ' без резервной копии. Это действие нельзя отменить.');
    text('reset-phrase',''); text('reset-status','Готовим подтверждение…');
    dialog.showModal();
    const operation = Symbol();
    dialog.operation = operation;
    try {
      const proof = await requests('reset/prepare',{});
      if (!dialog.open || dialog.operation !== operation) return;
      if (stageName(proof?.stage) !== lane || !string(proof?.confirmation_id) || !string(proof?.phrase) || proof.phrase.length > 160 || !integer(proof?.expires_in) || proof.expires_in < 1) throw Error();
      reset = {id:proof.confirmation_id, phrase:proof.phrase, deadline:Date.now()+proof.expires_in*1000,lane};
      text('reset-phrase',reset.phrase); text('reset-status',''); resetInput.disabled = false; resetInput.focus();
      expiry = setTimeout(()=>{
        reset = null; enableReset(); resetInput.disabled = true;
        text('reset-status','Подтверждение истекло. Закройте окно и откройте снова.');
      },Math.min(proof.expires_in*1000,2147483647));
    } catch {
      if (dialog.open && dialog.operation === operation) text('reset-status','Не удалось подготовить подтверждение.');
    }
  });
  by('reset-form').addEventListener('submit',async event=>{
    event.preventDefault(); enableReset();
    if (submit.disabled || !reset || stage !== reset.lane || capabilities.reset_database !== true) return;
    resetting = true; clearTimeout(expiry); enableReset(); resetInput.disabled = true;
    text('reset-status','Очищаем базу…');
    try {
      await requests('reset',{confirmation_id:reset.id,confirmation:resetInput.value});
      location.replace('/'); // Resolve the current visual and discard the displayed graph snapshot.
    } catch { text('reset-status','Не удалось подтвердить очистку. Обновите статус базы, прежде чем повторять.'); }
    finally { resetting = false; reset = null; resetInput.value = ''; enableReset(); }
  });
  by('refresh').addEventListener('click',refresh);
  refresh();
})();
