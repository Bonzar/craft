export type SourceObjectAccess='available'|'foreign_source'|'source_unavailable';
/** Missing namespace is allowed only for legacy links once the active source is known. */
export function sourceObjectAccess(currentProvider:string|null,targetProvider:string|null,availability?:string):SourceObjectAccess{
  if(!currentProvider)return'source_unavailable';
  if(targetProvider!==null&&targetProvider!==currentProvider)return'foreign_source';
  return availability==='provider_unavailable'?'source_unavailable':'available';
}
export function sourceTransport(live:boolean):{subscribe:boolean;label:string}{return live?{subscribe:true,label:'Live · подключено'}:{subscribe:false,label:'Сохранённый срез'};}
