// Адаптер запасного канала HTTP: внешний клиент, умеющий туннель через прокси.
//
// Зачем он вообще. Node не читает переменные прокси при запросе, в отличие от
// внешнего клиента. Пока прямой выход жив, запрос идёт напрямую; если он не
// удался, а прокси в окружении задан — повтор идёт этим адаптером. Так помощник
// ведёт себя одинаково и там, где выход прямой, и там, где он только через прокси.
//
// Имя команды и её ключи живут ЗДЕСЬ; общая часть (net.js) знает лишь, что у неё
// есть запасной канал, и получает от него текст.
import { spawnSync } from 'node:child_process';

export function viaExternal(url, { accept, timeoutSec, userAgent }) {
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy || '';
  const args = ['-sS', '--fail', '--max-time', String(timeoutSec), '-H', `Accept: ${accept}`, '-A', userAgent];
  if (proxy) args.push('--proxy', proxy);
  args.push(url);
  const res = spawnSync('curl', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  return res.status === 0 ? res.stdout : '';
}
