export type Mode = 'current' | 'known_then' | 'known_now';
export type Undated = 'separate' | 'include' | 'only';
export interface Selector { mode: Mode; at: string | null; history: boolean; undated: Undated; session_id?: string | null; run_id?: string | null; output_snapshot_id?: number | null }
export interface Span { start: number; end: number }
export interface TextSegment { text: string; marked: boolean }

export function codepointSegments(text: string, spans: readonly Span[], status = 'exact', offset = 0): TextSegment[] {
  if (status !== 'exact' || spans.length === 0) return [{ text, marked: false }];
  const points = Array.from(text);
  const ranges = spans.toSorted((a, b) => a.start - b.start);
  const segments: TextSegment[] = [];
  let cursor = 0;
  for (const span of ranges) {
    const start = Math.max(0, span.start - offset);
    const end = Math.min(points.length, span.end - offset);
    if (!Number.isInteger(span.start) || !Number.isInteger(span.end) || span.start < 0 || span.end < span.start || (offset === 0 && span.end > points.length)) throw new Error('Invalid source span');
    if (end <= cursor || end <= start) continue;
    if (start > cursor) segments.push({ text: points.slice(cursor, start).join(''), marked: false });
    segments.push({ text: points.slice(Math.max(start, cursor), end).join(''), marked: true });
    cursor = end;
  }
  if (cursor < points.length) segments.push({ text: points.slice(cursor).join(''), marked: false });
  return segments;
}

function zoneOffset(instant: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }).formatToParts(instant);
  const fields: Record<string, number> = {};
  for (const part of parts) if (part.type !== 'literal') fields[part.type] = Number(part.value);
  return Date.UTC(fields.year ?? 0, (fields.month ?? 1) - 1, fields.day ?? 1, fields.hour ?? 0, fields.minute ?? 0, fields.second ?? 0) - Math.floor(instant / 1000) * 1000;
}

export function temporalSelector(mode: Mode, day: string, timeZone: string, history: boolean, undated: Undated): Selector {
  if (mode === 'current') return { mode, at: null, history, undated: 'separate' };
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) throw new Error('Выберите дату');
  const midnight = Date.parse(`${day}T00:00:00Z`);
  if (!Number.isFinite(midnight) || new Date(midnight).toISOString().slice(0, 10) !== day) throw new Error('Некорректная дата');
  const localEnd = midnight + 86400000 - 1;
  let instant = localEnd;
  for (let iteration = 0; iteration < 3; iteration++) instant = localEnd - zoneOffset(instant, timeZone);
  return { mode, at: new Date(instant).toISOString(), history, undated };
}

export function canApplyDelta(revision: string, delta: { base_revision: string; revision: string }, mode: Mode): boolean {
  return mode !== 'known_then' && delta.base_revision === revision && delta.revision !== revision;
}

interface PinnedCard { id: string; text_revision: string; text: string; scrollTop: number; status: { disposition: string } }
export function pinCard<T extends PinnedCard>(pinned: T, incoming: Pick<T, 'id' | 'text_revision' | 'text' | 'status'>): T {
  if (pinned.id !== incoming.id) return pinned;
  return { ...pinned, status: incoming.status };
}

export function humanError(error: unknown): string {
  if (error instanceof DOMException && error.name === 'AbortError') return '';
  return error instanceof Error ? error.message : 'Не удалось получить данные. Повторите попытку.';
}

export function formatTime(raw: string | null, precision = 'unknown'): string {
  if (!raw) return 'Неизвестно';
  const date = new Date(raw);
  if (!Number.isFinite(date.getTime())) return 'Дата не распознана';
  if (precision === 'unknown') return `${raw.slice(0, 10)} · точность неизвестна`;
  if (precision === 'year') return raw.slice(0, 4);
  if (precision === 'month') return new Intl.DateTimeFormat('ru', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(date);
  if (precision === 'day' || precision === 'date') return new Intl.DateTimeFormat('ru', { dateStyle: 'long', timeZone: 'UTC' }).format(date);
  return `${new Intl.DateTimeFormat('ru', { dateStyle: 'medium', timeStyle: 'short' }).format(date)} · ${Intl.DateTimeFormat().resolvedOptions().timeZone}`;
}

interface SourceTopicRef {id:string;index:number}
interface SourceTopicLink {parent:string|number;child:string|number;relation:string}
export interface SourceHierarchyLink {parent:string;child:string}
export function sourceHierarchy(topics:readonly SourceTopicRef[],links:readonly SourceTopicLink[]):SourceHierarchyLink[]{
  const byIndex=new Map(topics.map(topic=>[topic.index,topic.id]));
  const ids=new Set(topics.map(topic=>topic.id));
  const result:SourceHierarchyLink[]=[];
  for(const link of links){
    if(link.relation!=='subquestion')continue;
    const parent=typeof link.parent==='number'?byIndex.get(link.parent):link.parent;
    const child=typeof link.child==='number'?byIndex.get(link.child):link.child;
    if(parent&&child&&ids.has(parent)&&ids.has(child))result.push({parent,child});
  }
  return result;
}
