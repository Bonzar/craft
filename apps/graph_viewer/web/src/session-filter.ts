import {ApiError,apiUrl,get} from './api.ts';
import {graphSessionsSchema} from './contracts.ts';
import type {GraphSession,GraphSessions,Manifest} from './contracts.ts';

type Pin=Pick<Manifest,'revision'|'source_revision'|'provider_id'>;
type LoadPage=(pin:Pin,cursor:string|null,signal:AbortSignal,query:string)=>Promise<GraphSessions>;
const loadPage:LoadPage=(pin,cursor,signal,query)=>get(apiUrl(`/graph/${encodeURIComponent(pin.revision)}/sessions`,{query,cursor,limit:50}),graphSessionsSchema,signal);
function shortId(id:string):string{return Array.from(id.replace(/^(?:native|logical):/,'')).slice(0,12).join('');}
export function sessionTitle(item:GraphSession):string{
  const time=item.first_recorded_at??item.last_recorded_at,date=time&&Number.isFinite(Date.parse(time))?new Intl.DateTimeFormat('ru',{dateStyle:'medium'}).format(new Date(time)):'дата неизвестна';
  return[item.display_title?.trim()||`Сессия ${shortId(item.id)}`,date,item.provenance_kind==='logical'?'логическая сессия':null].filter(Boolean).join(' · ');
}
export function sessionLabel(item:GraphSession):string{
  return`${sessionTitle(item)} · ${item.assertion_count.toLocaleString('ru')} утверждений`;
}
/** Options are provider-issued sessions; no reconstruction from topics or node cards. */
export class SessionFilter {
  private pin:Pin|null=null;private request:AbortController|null=null;private cursor:string|null=null;private loaded=false;private failed=false;private enabled=true;private query='';private timer:ReturnType<typeof setTimeout>|null=null;private items=new Map<string,GraphSession>();private knownItems=new Map<string,GraphSession>();
  private select:HTMLSelectElement;private status:HTMLElement;private more:HTMLButtonElement;private search:HTMLInputElement|null;private load:LoadPage;private selectedNote:HTMLElement|null;private onLabelsChanged:()=>void;
  constructor(select:HTMLSelectElement,status:HTMLElement,more:HTMLButtonElement,search:HTMLInputElement|null=null,load:LoadPage=loadPage,selectedNote:HTMLElement|null=null,onLabelsChanged:()=>void=()=>{}){this.select=select;this.status=status;this.more=more;this.search=search;this.load=load;this.selectedNote=selectedNote;this.onLabelsChanged=onLabelsChanged;select.addEventListener('change',()=>this.render());more.addEventListener('click',()=>{void this.open(true);});search?.addEventListener('input',()=>{if(this.timer)clearTimeout(this.timer);this.timer=setTimeout(()=>{this.timer=null;this.setQuery(search.value);},300);});search?.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();if(this.timer)clearTimeout(this.timer);this.timer=null;this.setQuery(search.value);}});this.render();}
  get value():string|null{return this.select.value||null;}
  labelFor(id:string):string{const item=this.knownItems.get(id);return item?sessionTitle(item):`Сессия ${shortId(id)}`;}
  setSelected(id:string|null|undefined):void{this.render(id??null);}
  setEnabled(enabled:boolean):void{this.enabled=enabled;this.select.disabled=!enabled;if(this.search)this.search.disabled=!enabled;if(!enabled){this.request?.abort();this.request=null;this.status.textContent='Источник пока не поддерживает фильтр по сессиям.';}this.render();}
  setQuery(query:string):void{if(query===this.query)return;this.query=query;this.request?.abort();this.request=null;this.items.clear();this.cursor=null;this.loaded=false;this.failed=false;this.render();void this.open();}
  bind(pin:Pin):void{if(this.pin?.revision===pin.revision&&this.pin.provider_id===pin.provider_id)return;this.request?.abort();this.request=null;if(this.pin?.provider_id!==pin.provider_id)this.knownItems.clear();this.pin={...pin};this.items.clear();this.cursor=null;this.loaded=false;this.failed=false;this.status.textContent='';this.render();}
  clear():void{if(this.timer)clearTimeout(this.timer);this.timer=null;this.request?.abort();this.request=null;this.pin=null;this.items.clear();this.knownItems.clear();this.cursor=null;this.loaded=false;this.failed=false;this.query='';if(this.search)this.search.value='';this.status.textContent='';this.render(null);}
  private render(selected=this.value):void{
    const option=(id:string,label:string):HTMLOptionElement=>{const item=document.createElement('option');item.value=id;item.textContent=label;return item;};
    this.select.replaceChildren(option('','Все сессии'));
    if(selected&&!this.items.has(selected))this.select.append(option(selected,this.labelFor(selected)));
    for(const item of this.items.values())this.select.append(option(item.id,sessionLabel(item)));
    this.select.value=selected??'';this.more.hidden=!this.enabled||!this.failed&&!this.cursor;this.more.disabled=this.request!==null;this.more.textContent=this.failed?'Повторить загрузку':'Ещё сессии';
    if(this.selectedNote){this.selectedNote.hidden=!selected;this.selectedNote.textContent=selected?this.labelFor(selected):'';}this.onLabelsChanged();
  }
  async open(next=false):Promise<void>{
    if(!this.enabled){this.status.textContent='Источник пока не поддерживает фильтр по сессиям.';return;}
    const pin=this.pin;if(!pin){this.status.textContent='Список сессий появится после загрузки карты.';return;}
    if(this.request||this.loaded&&!this.failed&&(!next||!this.cursor))return;
    const request=new AbortController();this.request=request;this.status.textContent='Загружаем сессии…';this.render();
    try{
      const page=await this.load(pin,this.loaded?this.cursor:null,request.signal,this.query);
      if(this.request!==request||request.signal.aborted)return;
      if(page.revision!==pin.revision||page.provider_id!==pin.provider_id||page.source_revision!==pin.source_revision)throw new Error('Список сессий относится к другому срезу.');
      for(const item of page.items){this.items.set(item.id,item);this.knownItems.set(item.id,item);}
      if(page.next_cursor&&page.next_cursor===this.cursor)throw new Error('Источник повторил страницу сессий.');
      this.cursor=page.next_cursor;this.loaded=true;this.failed=false;this.status.textContent=this.items.size?'Выберите сессию или оставьте все.':this.query?'По этому запросу сессий не найдено.':'В этом срезе сессий нет.';;
    }catch(error){if(this.request!==request||request.signal.aborted)return;this.failed=true;this.status.textContent=error instanceof ApiError&&error.code==='session_catalog_unavailable'?'Каталог сессий недоступен в этом срезе. Повторите загрузку списка.':error instanceof Error?`Не удалось загрузить сессии. ${error.message}`:'Не удалось загрузить сессии. Повторите попытку.';}
    finally{if(this.request===request){this.request=null;this.render();}}
  }
}
