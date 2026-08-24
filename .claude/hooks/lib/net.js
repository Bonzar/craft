// Сеть для инжекторов: чтение живого текста из connect-API Craft.
//
// Три вещи, без которых запрос не работает или врёт.
//
// БРАУЗЕРНЫЙ User-Agent обязателен при любом обращении к сайту, включая первый
// же запрос: стоит ноль, а без него часть хостов отдаёт отказ при полностью
// исправном канале.
//
// ДОВЕРЕННЫЙ КОРЕНЬ. В облачном контейнере весь исходящий HTTPS идёт через
// подменяющий прокси со своим сертификатом; node берёт корень из переменной
// окружения сам, поэтому здесь ничего задавать не нужно — но и снимать проверку
// сертификата нельзя, это не лечение, а сокрытие.
//
// ПРОКСИ. Node не читает переменные прокси при запросе, в отличие от внешнего
// клиента. Пока прямой выход жив, запрос идёт напрямую; если он не удался, а
// прокси в окружении задан — повтор идёт внешним клиентом, который туннель
// умеет. Так помощник ведёт себя одинаково и там, где выход прямой, и там, где
// он только через прокси.
import { spawnSync } from 'node:child_process';

const BROWSER_UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function viaExternalClient(url, accept, timeoutSec) {
  const proxy = process.env.HTTPS_PROXY || process.env.https_proxy || '';
  const args = ['-sS', '--fail', '--max-time', String(timeoutSec), '-H', `Accept: ${accept}`, '-A', BROWSER_UA];
  if (proxy) args.push('--proxy', proxy);
  args.push(url);
  const res = spawnSync('curl', args, { encoding: 'utf8', maxBuffer: 64 * 1024 * 1024 });
  return res.status === 0 ? res.stdout : '';
}

// Текст ответа или пустая строка. Пустая строка — единственный признак неудачи:
// инжекторы на ней молча оставляют прежний снимок, потому что мёртвая сеть не
// должна ронять старт сессии.
export async function fetchText(url, { accept = 'text/markdown', timeoutMs = 30000 } = {}) {
  try {
    const res = await fetch(url, {
      headers: { Accept: accept, 'user-agent': BROWSER_UA },
      signal: AbortSignal.timeout(timeoutMs),
    });
    if (!res.ok) return '';
    return await res.text();
  } catch {
    // Прямой выход не удался: пробуем тем же путём, что ходит внешний клиент.
    return viaExternalClient(url, accept, Math.ceil(timeoutMs / 1000));
  }
}
