// Хеш вызова инструмента: по нему узнаётся «тот же вызов» — повтор внутри хода и
// проход после отказа. Живёт отдельно, потому что его считают двое: тот, кто
// решает (пишет хеш в журнал решений), и хук метрик.
//
// Считается ВЕСЬ данный вход. Какие поля входа служебные, знает адаптер харнеса —
// он и отсеивает их до вызова; сюда вход приходит уже готовым.
import { sha256 } from './hash.js';

export function callHash(tool, input) {
  const src = input && typeof input === 'object' ? input : {};
  const semantic = Object.keys(src)
    .sort()
    .map((k) => `${k}=${canonical(src[k])}`)
    .join('\n');
  return sha256(`${tool}\n${semantic}`).slice(0, 16);
}

// Значение в устойчивом виде: ключи сортируются на КАЖДОМ уровне. Порядок полей
// смысла не несёт, а один и тот же вызов обязан давать один хеш — на нём стоит
// узнавание повтора после отказа. Порядок элементов массива, наоборот, значим.
function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value && typeof value === 'object') {
    return `{${Object.keys(value).sort().map((k) => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  }
  return JSON.stringify(value);
}
