import type {Topic} from './contracts.ts';

/** Role comes from Source purpose, independently of name review and freshness. */
export function topicRole(topic:Pick<Topic,'purpose'>):'Карта записей'|'Сохранённый материал'{return topic.purpose==='routing_only'?'Карта записей':'Сохранённый материал';}
export interface OverviewSegment {text:string;citationId?:string;citationNumber?:number}
/** Only explicit bracketed Source assertion IDs become links; prose is never searched. */
export function overviewSegments(text:string):OverviewSegment[]{
  const segments:OverviewSegment[]=[],numbers=new Map<string,number>();let offset=0;
  for(const match of text.matchAll(/\[(assertion-[A-Za-z0-9][A-Za-z0-9:_-]*)\]/gu)){
    const start=match.index,id=match[1]!;if(start>offset)segments.push({text:text.slice(offset,start)});
    let number=numbers.get(id);if(number===undefined){number=numbers.size+1;numbers.set(id,number);}
    segments.push({text:match[0],citationId:id,citationNumber:number});offset=start+match[0].length;
  }
  if(offset<text.length)segments.push({text:text.slice(offset)});return segments;
}
/** Search may point only to the exact local Source overview endpoint. */
export function approvedOverviewPath(value:string,topicId:string,origin:string):string{
  const url=new URL(value,origin),prefix='/api/v1/overviews/';
  if(url.origin!==origin||url.username||url.password||url.hash||!url.pathname.startsWith(prefix)||decodeURIComponent(url.pathname.slice(prefix.length))!==topicId)throw new Error('Недопустимый адрес материала источника');
  for(const key of url.searchParams.keys())if(key!=='revision')throw new Error('Недопустимый адрес материала источника');
  return `${prefix}${encodeURIComponent(topicId)}`;
}
