import type { Schema } from './schema.ts';
export class ApiError extends Error { status: number; code: string; constructor(status: number, code: string, message: string) { super(message); this.status = status; this.code = code; } }
export function apiUrl(path: string, params: Record<string, string | number | boolean | null | undefined> = {}): string {
  const url = new URL(path.startsWith('/api/v1/') ? path : `/api/v1${path}`, window.location.origin);
  if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/v1/')) throw new Error('Недопустимый адрес источника');
  for (const [key,value] of Object.entries(params)) if (value !== null && value !== undefined) url.searchParams.set(key,String(value));
  return url.pathname + url.search;
}
export async function response(path: string, signal?: AbortSignal, options: RequestInit = {}): Promise<Response> {
  const reply = await fetch(apiUrl(path), { ...options, signal, credentials:'same-origin', cache:'no-store', headers:{ Accept:'application/json',...options.headers } });
  if (!reply.ok) {
    let code = '';
    try { const body: unknown = await reply.json(); if (typeof body === 'object' && body !== null && 'error' in body) { const problem = body.error; if (typeof problem === 'object' && problem !== null && 'code' in problem && typeof problem.code === 'string') code = problem.code; } }
    catch { code = 'unreadable_error'; }
    const message = reply.status === 401 ? 'Для просмотра нужен вход владельца.' : reply.status === 409 ? 'Эта версия уже недоступна. Обновляем вселенную.' : reply.status === 503 ? 'Источник или выбранная возможность пока недоступны.' : reply.status === 404 ? 'Объект недоступен в выбранном срезе.' : reply.status === 429 ? 'Слишком много попыток. Подождите немного.' : 'Не удалось получить данные. Попробуйте ещё раз.';
    throw new ApiError(reply.status, code, message);
  }
  return reply;
}
export async function get<T>(path: string, schema: Schema<T>, signal?: AbortSignal): Promise<T> { return schema.parse(await (await response(path,signal)).json()); }
export async function write<T>(path: string, method: string, value: unknown, csrf: string, schema: Schema<T>): Promise<T> {
  return schema.parse(await (await response(path,undefined,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(value)})).json());
}
export function parsedEvent<T>(event: MessageEvent<string>, schema: Schema<T>): T { let parsed: unknown; try { parsed=JSON.parse(event.data); } catch { throw new Error('Обновление повреждено. Запрашиваем целую версию.'); } return schema.parse(parsed); }
