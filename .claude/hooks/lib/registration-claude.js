// Регистрация хуков в настройках Claude Code: есть ли у чекаута, в котором идёт
// сессия, СВОЯ проектная регистрация диспетчера.
//
// Зачем. Слой зарегистрирован дважды — проектно и в пользовательских настройках.
// Если проектная регистрация есть, полную цепочку ведёт она, и пользовательскому
// контуру писать метрики нельзя: он не видел проектных хуков и записал бы «allow»
// там, где проектный гвард отказал.
//
// Файл настроек и его форма — устройство этого харнеса, поэтому вопрос живёт в
// адаптере, а общая часть получает от обёртки готовый ответ.
import fs from 'node:fs';
import path from 'node:path';

// Ищется ВВЕРХ от рабочего каталога события.
export function projectDispatcherAt(cwd) {
  if (!cwd) return false;
  let probe;
  try {
    if (!fs.statSync(cwd).isDirectory()) return false;
    probe = fs.realpathSync(cwd);
  } catch {
    return false;
  }
  while (probe && probe !== path.dirname(probe)) {
    const settings = path.join(probe, '.claude', 'settings.json');
    let text;
    try {
      text = fs.readFileSync(settings, 'utf8');
    } catch { probe = path.dirname(probe); continue; }
    return registersDispatcher(text);
  }
  return false;
}

// Опознаётся по РЕГИСТРАЦИИ, а не по слову в файле: имя dispatch.js не наше, и в
// чужом проекте оно встречается своим скриптом. По подстроке такой проект
// считался бы ведущим полную цепочку, и метрики его сессий не писал бы никто.
function registersDispatcher(text) {
  let hooks;
  try {
    hooks = JSON.parse(text).hooks;
  } catch { return false; }
  if (!hooks || typeof hooks !== 'object') return false;
  for (const groups of Object.values(hooks)) {
    if (!Array.isArray(groups)) continue;
    for (const group of groups) {
      const list = group && Array.isArray(group.hooks) ? group.hooks : [];
      for (const hook of list) {
        const cmd = hook && typeof hook.command === 'string' ? hook.command : '';
        if (cmd.split(/\s+/).some((w) => w.replace(/^["']|["']$/g, '').endsWith('/.claude/hooks/dispatch.js'))) {
          return true;
        }
      }
    }
  }
  return false;
}
