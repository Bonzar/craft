export type SourceObjectAccess='available'|'foreign_source'|'source_unavailable';
/** Missing namespace is allowed only for legacy links once the active source is known. */
export function sourceObjectAccess(currentProvider:string|null,targetProvider:string|null,availability?:string):SourceObjectAccess{
  if(!currentProvider)return'source_unavailable';
  if(targetProvider!==null&&targetProvider!==currentProvider)return'foreign_source';
  return availability==='provider_unavailable'?'source_unavailable':'available';
}
export function sourceTransport(live:boolean):{subscribe:boolean;label:string}{return live?{subscribe:true,label:'Live · подключено'}:{subscribe:false,label:'Сохранённый срез'};}

// The browser already displays the synthetic fixture and snapshot transport badges.
const INFORMATIONAL_READINESS = new Set(['synthetic_fixture', 'canonical_thoughts', 'sites_snapshot_transport']);
const READINESS_LABELS = new Map([
  ['semantic_unavailable', 'Поиск по смыслу пока недоступен.'],
  ['semantic_preparing', 'Поиск по смыслу готовится.'],
  ['global_cancellation', 'Отмена утверждений показана только по явным отметкам источника.'],
]);
export function sourceReadinessNotice(entries:readonly {code:string}[]):string {
  const notices=new Set<string>();
  for(const {code} of entries)if(!INFORMATIONAL_READINESS.has(code))
    notices.add(READINESS_LABELS.get(code)??'Часть возможностей источника пока недоступна.');
  return [...notices].join(' ');
}
