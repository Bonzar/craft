import type {DisplayProxy} from './lod.ts';
export {sourceTopicCaption as sourceCaption} from './caption-layout.ts';
export function proxyCaption(proxy:DisplayProxy):string{return proxy.kind==='cell'?`${proxy.represented_count.toLocaleString('ru')} утверждений`:`${proxy.visible_source_member_count.toLocaleString('ru')} в срезе · ${proxy.source_member_count.toLocaleString('ru')} в теме`;}
