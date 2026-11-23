import type {ProxyEdgeCounts} from './lod.ts';
import { ApiError, apiUrl, get } from './api.ts';
import { edgeSchema, incidentPageSchema, nodeSchema, sourceSchema, overviewSchema } from './contracts.ts';
import type { EdgeCard, NodeCard, NodeStatus, SourcePage, Topic, Topics, Manifest } from './contracts.ts';
import { button, detail, element, note, setBusy } from './dom.ts';
import { lifecycle } from './lifecycle.ts';
import {overviewSegments,topicRole} from './topic-content.ts';
import { memberVisibilityLabel } from './interaction.ts';
import { codepointSegments, formatTime, humanError, pinCard } from './logic.ts';
import type { Span,Selector } from './logic.ts';
const STATUS:Record<string,string>={assertion:'Утверждение',proposal:'Предложение',observation:'Наблюдение',decision:'Решение',implementation:'Реализация',inference:'Вывод',superseded:'Заменено',cancelled:'Отменено',conflict:'Расхождение',exception:'Исключение',unknown:'Статус неизвестен'};
const RELATIONS:Record<string,string>={supports:'Подтверждает',contradicts:'Противоречит',supersedes:'Заменяет',refines:'Уточняет',implements:'Реализует',depends_on:'Зависит от',causes:'Приводит к',exception:'Исключение',related:'Связано',evidence:'Свидетельство',answers:'Отвечает',subquestion:'Подвопрос'};
export interface ReaderSourceContext {revision:string;provider_id:string}
interface ReaderActions {material?:(id:string,version:number,context:ReaderSourceContext,intent:number)=>void;materialLabel?:(id:string)=>string;citation?:(id:string,context:{revision:string;provider_id:string},intent:number)=>void;close:()=>void;node:(id:string,context?:ReaderSourceContext,intent?:number)=>void;edge:(id:string,context?:ReaderSourceContext,intent?:number)=>void;bookmark:(kind:'node'|'topic',id:string,label:string,context?:{provider_id:string;selector?:Selector})=>Promise<void>;report:(message:string)=>void;failed?:(error:unknown)=>void;overview?:()=>void;canOverview?:()=>boolean;connectionInfo?:(id:string)=>{external:number;self:number}|null}
interface PinAvailability {state:'available'|'unavailable';checkedRevision:string}
interface NodePin {view:HTMLElement;nodeScrollTop:number;availability:PinAvailability;card:NodeCard;statusRevision:string;body:HTMLElement;status:HTMLElement;connections:HTMLElement;request:AbortController;topics?:Topics;onTopic?:(id:string)=>void}
const INITIAL_NODE_TOPICS=5;const NODE_TOPICS_PAGE=50;
export function topicNamingNotice(topic:{naming_status:string|null}):string|null{
  if(topic.naming_status==='naming_pending')return 'Название ожидает проверки. Показан полный вопрос.';
  if(topic.naming_status==='legacy_unreviewed')return 'Название источника без редакционной проверки.';
  return null;
}
export function relationLabel(kind:string):string{return RELATIONS[kind]??`Связь: ${kind}`;}
export function sourceTopicMemberships(card:Pick<NodeCard,'revision'|'topic_ids'>,topics?:{revision:string;topics:readonly Pick<Topic,'id'|'full_title'>[]}):{id:string;title:string|null}[]{
  const titles=new Map(topics?.revision===card.revision?topics.topics.map(topic=>[topic.id,topic.full_title]):[]);
  return card.topic_ids.map(id=>({id,title:titles.get(id)??null}));
}
export function incidentEdgeLabel(edge:Pick<NodeCard['incident_edges']['items'][number],'from_id'|'to_id'|'kind'>,nodeId:string|null):string{
  const direction=edge.from_id===nodeId?(edge.to_id===nodeId?'С собой':'Исходящая'):'Входящая';
  const other=edge.from_id===nodeId?edge.to_id:edge.from_id;
  return `${direction} · ${relationLabel(edge.kind)} · ${other}`;
}

export class Reader {
  private intent=0;private sourceContext:ReaderSourceContext|null=null;private root:HTMLElement;private actions:ReaderActions;private pin:NodePin|null=null;private request:AbortController|null=null;
  private availability=element('p','','notice pinned-availability');private liveStatus=element('div','','live-card-status');private pending=element('div','','reader-pending');private scroll:HTMLElement;private header:HTMLElement;private content:HTMLElement;private currentId:string|null=null;
  constructor(root:HTMLElement,actions:ReaderActions){this.root=root;this.actions=actions;this.header=element('header','','reader-header');this.scroll=element('div','','reader-scroll');this.content=element('div');this.scroll.append(this.content);this.availability.hidden=true;this.availability.setAttribute('role','status');this.availability.setAttribute('aria-live','polite');this.liveStatus.hidden=true;this.liveStatus.setAttribute('aria-live','polite');this.pending.hidden=true;this.pending.setAttribute('role','status');root.append(this.header,this.availability,this.liveStatus,this.pending,this.scroll);let scrollAt=0;this.scroll.addEventListener('scroll',()=>{const now=performance.now();if(now-scrollAt>250){scrollAt=now;lifecycle('reader-scroll',{offset:Math.round(this.scroll.scrollTop)});}},{passive:true});root.setAttribute('role','region');root.setAttribute('aria-label','Карточка');}
  get intentGeneration():number{return this.intent;}
  get pinnedAvailability():Readonly<PinAvailability>|null{return this.pin?.availability??null;}
  get selectedId():string|null{return this.pin?.card.id??null;}
  get pinnedNodeId():string|null{return this.pin?.card.id??null;}
  get hasLoadedContent():boolean{return !this.root.hidden&&this.root.dataset.kind!=='loading'&&this.content.childNodes.length>0;}
  finishLoading(abortTemporary=false):void{this.pending.hidden=true;if(abortTemporary)this.request?.abort();}
  loading(title='Открываем утверждение…'):void{this.intent++;
    this.request?.abort();
    if(this.hasLoadedContent){this.pending.textContent=title;this.pending.hidden=false;return;}
    this.liveStatus.hidden=true;this.cancel();this.currentId=null;this.pin=null;this.root.hidden=false;this.root.dataset.kind='loading';delete this.root.dataset.nodeId;this.shell(title);this.content.replaceChildren(note('Загружаем сохранённый текст…'));this.scroll.scrollTop=0;
  }
  error(message:string):void{this.finishLoading();if(this.root.dataset.kind!=='loading'&&this.content.childNodes.length){this.actions.report(message);return;}this.content.replaceChildren(element('p',message,'notice'),button('Закрыть',()=>this.actions.close()));}
  close():void{this.finishLoading();this.liveStatus.hidden=true;this.cancel();this.root.hidden=true;this.currentId=null;this.pin=null;}
  clear():void{this.close();this.header.replaceChildren();this.liveStatus.replaceChildren();this.pending.replaceChildren();this.content.replaceChildren();delete this.root.dataset.nodeId;delete this.root.dataset.edgeId;delete this.root.dataset.topicId;}
  private cancel():void{this.intent++;this.availability.hidden=true;delete this.root.dataset.availability;delete this.availability.dataset.checkedRevision;this.request?.abort();this.pin?.request.abort();}
  private shell(title:string):void{const heading=element('h2',title);this.header.replaceChildren(heading,button('↕',()=>{this.root.classList.toggle('expanded');},'icon expand-sheet'),button('×',()=>this.actions.close(),'icon close-reader'));const close=this.header.lastElementChild;close?.setAttribute('aria-label','Закрыть карточку');this.header.querySelector('.expand-sheet')?.setAttribute('aria-label','Развернуть карточку');}
  private run(task:()=>Promise<void>):void{task().catch(error=>{this.finishLoading();if(this.actions.failed)this.actions.failed(error);else{const message=humanError(error);if(message)this.actions.report(message);}});}
  private nodeAction(id:string):()=>void{const intent=this.intent,context=this.sourceContext?{...this.sourceContext}:undefined;return()=>{if(this.intent===intent)this.actions.node(id,context,intent);};}
  private edgeAction(id:string):()=>void{const intent=this.intent,context=this.sourceContext?{...this.sourceContext}:undefined;return()=>{if(this.intent===intent)this.actions.edge(id,context,intent);};}
  showNode(card:NodeCard,topics?:Topics,onTopic?:(id:string)=>void,context?:ReaderSourceContext):void {
    this.finishLoading();this.liveStatus.hidden=true;this.cancel();this.sourceContext=context?{...context,revision:card.revision}:null;this.currentId=card.id;this.root.hidden=false;this.root.dataset.kind='node';this.root.dataset.nodeId=card.id;this.shell('Утверждение');this.content.replaceChildren();this.scroll.scrollTop=0;
    const status=element('div','','status-badges');const prose=element('div',card.text,'prose assertion-text');prose.dataset.textRevision=card.text_revision;prose.setAttribute('role','article');prose.setAttribute('aria-label','Текст утверждения');
    const connections=element('div','','node-connections');connections.textContent='Связи в кадре: уточняем';
    const pin:NodePin={view:this.content,nodeScrollTop:0,availability:{state:'available',checkedRevision:card.revision},card,statusRevision:card.revision,body:prose,status,connections,request:new AbortController(),topics,onTopic};this.pin=pin;this.renderAvailability();
    const compact=element('div','','attribution');compact.append(element('div',`${card.actor?.display_name||card.actor?.id||'Автор неизвестен'} · ${this.sourceLabel(card.source)}`),element('div',`Дата события: ${formatTime(card.time.valid_at,card.time.valid_precision)}`),element('div',`Записано: ${formatTime(card.time.recorded_at,'instant')}`));
    compact.append(connections);const initialConnections=this.actions.connectionInfo?.(card.id);if(initialConnections)this.updateConnectivity(card.id,initialConnections.external,initialConnections.self);
    const tools=element('div','','reader-tools');tools.append(button('☆ В закладки',()=>this.run(()=>this.actions.bookmark('node',card.id,Array.from(card.text).slice(0,90).join('')))));
    if(this.actions.canOverview?.()&&this.actions.overview)tools.append(button('Материал выбранной темы',this.actions.overview));this.content.append(status,compact,tools,prose);this.renderStatus(status,card.status);
    if(card.next_text_offset!==null)this.addTextContinuation(pin);
    this.appendNodeTopics(pin);
    const meta=element('dl','','metadata');meta.append(detail('Кто',card.actor?.display_name||card.actor?.id||'Неизвестно'),detail('Откуда',this.sourceLabel(card.source)),detail('Эпизод',card.episode||'Неизвестно'),detail('Область',card.scope||'Неизвестно'),detail('Дата события',formatTime(card.time.valid_at,card.time.valid_precision)),detail('Записано системой',formatTime(card.time.recorded_at,'instant')),detail('Записано в источнике',formatTime(card.time.source_recorded_at,'instant')),detail('Источник стал доступен',formatTime(card.time.source_available_at,'instant')),detail('Осмыслено системой',formatTime(card.time.interpreted_at,'instant')));this.content.append(meta);
    if(card.sources.length){this.content.append(element('h3','Источники'));card.sources.forEach((source,index)=>{const sourceButton=button(`Исходный фрагмент ${index+1}`,()=>this.run(()=>this.openSource(source.record_id,card.revision,source.start,source.end)),'source-link');sourceButton.disabled=!source.available;this.content.append(sourceButton);if(!source.available)this.content.append(note('Сохранённый источник недоступен'));if(source.context_truncated)this.content.append(note('Контекст сохранён частично'));});}else this.content.append(note('Сохранённый исходный фрагмент отсутствует'));
    this.content.append(element('h3',`Связи сохранённой версии · ${card.incident_edges.visible_total||card.incident_edges.total}`));const list=element('div','','edge-list');this.content.append(list);this.appendEdges(list,card.incident_edges.items);
    if(card.incident_edges.next_cursor!==null)this.addEdgeContinuation(pin,list,card.incident_edges.next_cursor);
    for(const unknown of card.unknowns)this.content.append(note(this.unknownLabel(unknown)));
    this.content.append(note(`Текст закреплён за полученной версией · ${formatTime(card.time.recorded_at,'instant')}`));
  }
  private appendNodeTopics(pin:NodePin):void{
    const memberships=sourceTopicMemberships(pin.card,pin.topics);const section=element('section','','node-topics');section.append(element('h3','Темы с этим утверждением'));this.content.append(section);
    if(!memberships.length){section.append(note('Источник не включил это утверждение в темы.'));return;}
    const append=(target:HTMLElement,start:number,end:number):void=>{for(const membership of memberships.slice(start,end)){
      if(membership.title===null){target.append(note('Материал источника недоступен в версии карточки.'));continue;}
      const open=button(membership.title,()=>pin.onTopic?.(membership.id),'topic-choice');open.disabled=!pin.onTopic;target.append(open);
    }};
    const first=element('div','','topic-members');section.append(first);append(first,0,INITIAL_NODE_TOPICS);
    if(memberships.length<=INITIAL_NODE_TOPICS)return;
    const details=element('details','','topic-members-details');details.append(element('summary',`Ещё темы · ${memberships.length-INITIAL_NODE_TOPICS}`));const list=element('div','','topic-members');let shown=INITIAL_NODE_TOPICS;
    const appendMore=():void=>{const end=Math.min(memberships.length,shown+NODE_TOPICS_PAGE);append(list,shown,end);shown=end;more.hidden=shown>=memberships.length;};
    const more=button('Ещё темы с этим утверждением',appendMore);details.append(list,more);details.addEventListener('toggle',()=>{if(details.open&&shown===INITIAL_NODE_TOPICS)appendMore();});section.append(details);
  }
  private addTextContinuation(pin:NodePin):void {const next=pin.card.next_text_offset;if(next===null)return;const more=button('Продолжить текст',()=>this.run(async()=>{setBusy(more,true,'Продолжить текст');try{const card=await get(apiUrl(`/nodes/${encodeURIComponent(pin.card.id)}`,{revision:pin.card.revision,offset:next,limit:12000}),nodeSchema,pin.request.signal);if(this.pin!==pin)return;if(card.id!==pin.card.id||card.revision!==pin.card.revision||card.text_revision!==pin.card.text_revision||card.text_offset!==next)throw new Error('Версия текста изменилась. Уже открытый текст сохранён.');pin.body.append(document.createTextNode(card.text));pin.card={...pin.card,next_text_offset:card.next_text_offset};more.remove();this.addTextContinuation(pin);}finally{if(more.isConnected)setBusy(more,false,'Продолжить текст');}}),'continue-text');pin.body.after(more);}
  private appendEdges(container:HTMLElement,items:NodeCard['incident_edges']['items']):void {for(const edge of items)container.append(button(incidentEdgeLabel(edge,this.currentId),this.edgeAction(edge.id),'edge-link'));}
  private addEdgeContinuation(pin:NodePin,list:HTMLElement,cursor:string):void {const more=button('Ещё связи',()=>this.run(async()=>{setBusy(more,true,'Ещё связи');try{const page=await get(apiUrl(`/nodes/${encodeURIComponent(pin.card.id)}/edges`,{revision:pin.card.revision,cursor,visible:1,limit:200}),incidentPageSchema,pin.request.signal);if(this.pin!==pin)return;if(page.revision!==pin.card.revision||page.node_id!==pin.card.id)throw new Error('Список связей относится к другой версии');this.appendEdges(list,page.items);more.remove();if(page.next_cursor!==null)this.addEdgeContinuation(pin,list,page.next_cursor);}finally{if(more.isConnected)setBusy(more,false,'Ещё связи');}}));list.after(more);}
  updateAvailability(id:string,state:PinAvailability['state'],checkedRevision:string):void{
    const pin=this.pin;if(!pin||pin.card.id!==id)return;const scrollTop=this.scroll.scrollTop;
    pin.availability={state,checkedRevision};this.renderAvailability();this.renderStatus(pin.status,pin.card.status,state==='unavailable');this.updateConnectivity(id,null);this.scroll.scrollTop=scrollTop;
  }
  private renderAvailability():void{
    const pin=this.pin;this.availability.hidden=pin?.availability.state!=='unavailable';
    if(!pin)return;this.root.dataset.availability=pin.availability.state;this.availability.dataset.checkedRevision=pin.availability.checkedRevision;
    if(this.availability.hidden)return;
    this.availability.textContent=this.root.dataset.kind==='source'?'Показан источник ранее открытой версии; связанное утверждение недоступно в текущей версии графа.':'Утверждение недоступно в текущей версии графа. Показан ранее открытый текст.';
  }
  updateConnectivity(id:string,external:number|null,self=0):void{if(!this.pin||this.pin.card.id!==id)return;this.pin.connections.textContent=this.pin.availability.state==='unavailable'?'Связи в кадре недоступны для ранее открытого утверждения.':external===null?'Связи в кадре: уточняем':`${external.toLocaleString('ru')} связей с другими в кадре${self?` · ${self} с собой`:''}`;}
  updateStatus(id:string,status:NodeStatus,revision:string):void {if(!this.pin||this.pin.card.id!==id)return;const changed=JSON.stringify(this.pin.card.status)!==JSON.stringify(status);this.pin.statusRevision=revision;const kept=pinCard({...this.pin.card,scrollTop:this.scroll.scrollTop},{...this.pin.card,status});this.pin.card=kept;this.renderStatus(this.pin.status,status,this.pin.availability.state==='unavailable');if(changed)this.renderLiveStatus(status);this.scroll.scrollTop=kept.scrollTop;}
  private renderLiveStatus(status:NodeStatus):void{
    this.liveStatus.replaceChildren(element('strong',`Статус обновлён: ${STATUS[status.disposition]??'неизвестен'}`),button('×',()=>{this.liveStatus.hidden=true;},'icon'));
    this.liveStatus.querySelector('button')?.setAttribute('aria-label','Скрыть обновление статуса');
    this.liveStatus.append(note('Открытый текст и место чтения сохранены.'));
    for(const id of status.replacements)this.liveStatus.append(button('Перейти к замене',this.nodeAction(id),'replacement-link'));
    if(status.unresolved_conflict)this.liveStatus.append(note('В источнике есть неразрешённое противоречие.'));
    this.liveStatus.hidden=false;
  }
  private renderStatus(container:HTMLElement,status:NodeStatus,previous=false):void {container.replaceChildren();if(previous)container.append(note('Статус и связи показаны по ранее полученным данным.'));container.append(element('span',STATUS[status.disposition]??'Статус из источника','badge'));if(status.effectiveness==='future')container.append(element('span','Относится к будущему','badge'));if(status.unresolved_conflict)container.append(element('p','В источнике есть неразрешённое противоречие.','notice'));if(status.disposition==='superseded'||status.disposition==='cancelled')container.append(note('Открытый текст сохранён; положение чтения не изменилось.'));for(const id of status.replacements)container.append(button('Перейти к замене',this.nodeAction(id),'replacement-link'));}
  async showEdge(card:EdgeCard,context?:ReaderSourceContext):Promise<void> {
    this.finishLoading();this.liveStatus.hidden=true;this.cancel();this.sourceContext=context?{...context,revision:card.revision}:null;this.currentId=null;this.pin=null;this.root.hidden=false;this.root.dataset.kind='edge';delete this.root.dataset.nodeId;this.root.dataset.edgeId=card.id;this.shell(relationLabel(card.kind));this.content.replaceChildren();this.scroll.scrollTop=0;
    if(card.condition)this.content.append(note(`Условие из источника: ${card.condition}`));
    for(const [label,endpoint] of [['Откуда',card.from],['Куда',card.to]] as const){const section=element('section','','endpoint');section.append(element('h3',label));const text=element('div','','prose endpoint-text');this.renderFragment(text,endpoint.text,endpoint.fragment.spans,endpoint.fragment.status);section.append(text);if(endpoint.fragment.status!=='exact')section.append(note(endpoint.fragment.status==='ambiguous'?'Фрагмент встречается несколько раз. Точное место не задано источником.':endpoint.fragment.status==='invalid'?'Границы исходного фрагмента недоступны.':'Связанный фрагмент не указан.'));section.append(button('Открыть утверждение целиком',this.nodeAction(endpoint.id)));this.content.append(section);}
    this.content.append(note(`Тип связи из источника: ${card.kind}`),note(`Связь записана ${formatTime(card.recorded_at,'instant')}`));for(const unknown of card.unknowns)this.content.append(note(this.unknownLabel(unknown)));
    // Full endpoints are read from the same immutable revision, with continuations.
    const controller=new AbortController();this.request=controller;
    const targets=Array.from(this.content.querySelectorAll<HTMLElement>('.endpoint-text'));
    await Promise.all([card.from,card.to].map(async(endpoint,index)=>{const target=targets[index];if(!target)return;let offset=0;let full='';let expected:string|null=null;do{const page=await get(apiUrl(`/nodes/${encodeURIComponent(endpoint.id)}`,{revision:card.revision,offset,limit:12000}),nodeSchema,controller.signal);if(page.id!==endpoint.id||page.revision!==card.revision||expected!==null&&expected!==page.text_revision)throw new Error('Версия связанного текста изменилась');expected=page.text_revision;full+=page.text;if(page.next_text_offset===null)break;if(page.next_text_offset<=offset)throw new Error('Продолжение текста недоступно');offset=page.next_text_offset;}while(!controller.signal.aborted);if(controller.signal.aborted)return;this.renderFragment(target,full,endpoint.fragment.spans,endpoint.fragment.status);}));
  }
  private renderFragment(target:HTMLElement,text:string,spans:readonly Span[],status:string,offset=0):void{target.replaceChildren();let segments;try{segments=codepointSegments(text,spans,status,offset);}catch{segments=[{text,marked:false}];}for(const segment of segments)target.append(segment.marked?element('mark',segment.text):document.createTextNode(segment.text));}
  private showPinnedNode():void{
    const pin=this.pin;if(!pin)return;this.finishLoading();this.request?.abort();this.request=null;this.liveStatus.hidden=true;this.currentId=pin.card.id;this.root.dataset.kind='node';this.root.dataset.nodeId=pin.card.id;this.shell('Утверждение');this.content=pin.view;this.scroll.replaceChildren(pin.view);this.renderAvailability();this.scroll.scrollTop=pin.nodeScrollTop;
  }
  private async openSource(recordId:string,revision:string,start:number|null,end:number|null,offset?:number):Promise<void> {
    this.request?.abort();const request=new AbortController();this.request=request;
    const path=apiUrl(`/sources/${encodeURIComponent(recordId)}`,{revision,context:1,limit:12000,...(offset!==undefined?{offset}:start!==null&&end!==null?{focus_start:start,focus_end:end}:{offset:0})});
    const page=await get(path,sourceSchema,request.signal);if(request.signal.aborted)return;this.renderSource(page,revision,start,end);
  }
  private renderSource(page:SourcePage,revision:string,start:number|null,end:number|null):void {
    this.finishLoading();const back=this.pin?.card;if(this.pin&&this.root.dataset.kind==='node')this.pin.nodeScrollTop=this.scroll.scrollTop;this.root.dataset.kind='source';this.shell('Сохранённый источник');this.content=element('div');this.scroll.replaceChildren(this.content);this.renderAvailability();this.scroll.scrollTop=0;
    if(back)this.content.append(button('← К утверждению',()=>this.showPinnedNode()));
    this.content.append(note(`${page.offset.toLocaleString('ru')}–${(page.offset+Array.from(page.text).length).toLocaleString('ru')} из ${page.total_characters.toLocaleString('ru')} знаков`));
    if(!page.saved_text_complete)this.content.append(element('p','Сохранена только часть исходного материала.','notice'));
    if(page.external_source_complete!==true)this.content.append(note(page.external_source_complete===false?'Внешний источник сохранён не целиком.':'Полнота внешнего источника неизвестна.'));
    const controls=element('div','','source-controls');
    if(page.before_available&&page.previous_offset!==null)controls.append(button('Предыдущая часть',()=>this.run(()=>this.openSource(page.record_id,revision,start,end,page.previous_offset??0))));
    controls.append(button('Весь сохранённый источник',()=>this.run(()=>this.openSource(page.record_id,revision,null,null,0))));this.content.append(controls);
    const text=element('div','','prose source-text');text.setAttribute('role','article');text.setAttribute('aria-label','Текст источника');this.renderFragment(text,page.text,page.highlights,'exact',page.offset);this.content.append(text);
    if(page.next_offset!==null)this.content.append(button('Следующая часть источника',()=>this.run(()=>this.openSource(page.record_id,revision,start,end,page.next_offset??0)),'source-next'));
    if(page.context_truncated)this.content.append(note('Контекст сохранён частично. Несохранённые места не восстановлены.'));
    if(page.context.length){this.content.append(element('h3','Сохранённый контекст'));for(const item of page.context){if(typeof item==='string')this.content.append(element('p',item,'prose'));else if(typeof item==='object'&&item!==null&&'text' in item&&typeof item.text==='string')this.content.append(element('p',item.text,'prose'));}}
    for(const unknown of page.unknowns)this.content.append(note(this.unknownLabel(unknown)));
    requestAnimationFrame(()=>text.querySelector('mark')?.scrollIntoView({block:'center',behavior:'instant'}));
  }
  showTopic(topic:Topic,members:readonly number[],openOrdinal:(ordinal:number)=>void,representation?:{represented:number;visibleMembers:number;internalEdges:number;edgeCounts?:ProxyEdgeCounts;externalVisible?:number;sharedMembers?:number;visibleElsewhere?:number},visibleInSlice:(ordinal:number)=>boolean=()=>true,context?:{revision:string;provider_id:string;selector?:Selector;session_filter?:Manifest['session_filter']}):void{
    this.finishLoading();this.liveStatus.hidden=true;this.cancel();this.sourceContext=context?{...context}:null;this.pin=null;this.currentId=topic.id;this.root.hidden=false;this.root.dataset.kind='topic';this.root.dataset.topicId=topic.id;this.shell(topicRole(topic));this.content.replaceChildren();
    const heading=element('h3',topic.naming_status==='naming_pending'?topic.full_title:topic.display_title??topic.full_title,'topic-heading');this.content.append(heading);
    const namingNotice=topicNamingNotice(topic);if(namingNotice)this.content.append(note(namingNotice));
    if(context?.session_filter?.overview_history?.history_complete===false)this.content.append(note('Полнота истории результатов этой сессии не подтверждена. Сохранённые версии доступны; связь с другими результатами могла быть не записана.'));
    if(topic.session_result){
      const result=topic.session_result,kind=result.role==='input'?'Сохранённое основание':result.cause_status==='caller_context_only'?'Результат запроса сессии':result.outcome==='reused'?'Ранее созданная версия, использованная при обработке':'Результат обработки';
      this.content.append(note(`${kind} · версия ${result.version_id}.`));
      if(result.cause_status==='caller_context_only')this.content.append(note('Привязка к сессии указана в контексте запроса. Это не делает сессию источником утверждений материала.'));
      if(result.cause_sessions.length>1)this.content.append(note(`Общая причина: ${result.cause_sessions.length} сессии. Результат не приписан только одной из них.`));
      if(result.role==='input')this.content.append(note('Эта версия использована как основание и не объявляется новым результатом выбранной сессии.'));
      if(result.upstream.length&&context){const links=element('div','','topic-inputs');links.append(element('h4','Основания этой версии'));const intent=this.intent;for(const ref of result.upstream){const title=this.actions.materialLabel?.(ref.topic_id)??'Сохранённый материал';const link=button(`${title} · версия ${ref.version_id}`,()=>{if(this.intent===intent)this.actions.material?.(ref.topic_id,ref.version_id,context,intent);});link.disabled=!this.actions.material;links.append(link);}this.content.append(links);}
    }
    if(topic.unavailable_member_count)this.content.append(note(`Для ${topic.unavailable_member_count.toLocaleString('ru')} участников этого материала исходные записи недоступны в текущем срезе.`));
    if(topic.session_filter_applied){if(topic.slice_member_count!==null)this.content.append(note(`В выбранной сессии: ${topic.slice_member_count.toLocaleString('ru')} из ${topic.member_count.toLocaleString('ru')} утверждений темы.`));this.content.append(note('Сохранённый текст темы не изменён фильтром и может включать сведения из других сессий.'));}
    if(context){
      const summary=element('section','','topic-overview');summary.append(note(topic.purpose==='routing_only'?'Открываем карту записей…':'Открываем сохранённый материал…'));this.content.append(summary);const request=new AbortController();this.request=request;this.run(()=>this.loadOverview(topic,context,summary,request));
    }else this.content.append(note('Сохранённый текст пока недоступен. Ниже можно открыть связанные утверждения.'));
    const bookmarkContext=context?{provider_id:context.provider_id,selector:context.selector&&topic.session_result?{...context.selector,session_id:topic.session_result.session_id,run_id:topic.session_result.run_id??context.selector.run_id,output_snapshot_id:context.session_filter?.overview_history?.snapshot_id??context.selector.output_snapshot_id}:context.selector}:undefined;const actions=element('div','','topic-overview-actions');actions.append(button('☆ В закладки',()=>this.run(()=>this.actions.bookmark('topic',topic.id,topic.full_title,bookmarkContext))));this.content.append(actions);
    const details=element('details','','topic-details');details.append(element('summary','Полное название и сведения'),element('p',topic.full_title,'prose topic-full-title'),note(`${topic.member_count.toLocaleString('ru')} утверждений по данным источника`));
    const nameState=topic.naming_status==='accepted'?'Принятое название':topic.naming_status==='fallback'?'Название источника':topic.naming_status==='naming_pending'?'Название готовится':'Статус названия неизвестен';details.append(detail('Название',nameState));
    if(topic.name_record_id!==null)details.append(detail('Запись названия',String(topic.name_record_id)));if(topic.name_recorded_at)details.append(detail('Название записано',formatTime(topic.name_recorded_at,'instant')));
    if(topic.definition_title&&topic.definition_title!==topic.full_title)details.append(detail('Исходный вопрос',topic.definition_title));if(topic.query&&topic.query!==topic.definition_title)details.append(detail('Запрос источника',topic.query));
    if(representation){if(representation.edgeCounts){const c=representation.edgeCounts;details.append(note(`Входящие: ${c.incoming_external.toLocaleString('ru')}. Исходящие: ${c.outgoing_external.toLocaleString('ru')}. Внутри: ${c.internal.toLocaleString('ru')}. Всего связей: ${c.total_unique.toLocaleString('ru')}.`));}details.append(note(`В выбранной точке при открытии: ${representation.represented.toLocaleString('ru')} уникальных утверждений. Видимых участников темы: ${representation.visibleMembers.toLocaleString('ru')}. Темы могут пересекаться; каждое утверждение на карте представлено один раз.`),note(`${representation.externalVisible!==undefined?representation.externalVisible.toLocaleString('ru'):'Пока неизвестно'} связей с другими представителями в кадре. Внутри выбранной свёрнутой группы: ${representation.internalEdges.toLocaleString('ru')}.`));}
    if(representation?.sharedMembers!==undefined)details.append(note(`Общих с другими темами утверждений: ${representation.sharedMembers.toLocaleString('ru')}. Уже показаны в других раскрытых темах: ${(representation.visibleElsewhere??0).toLocaleString('ru')}. Это те же утверждения, а не дополнительные связи между ними.`));
    if(topic.session_result?.check_id!==null&&topic.session_result?.check_id!==undefined)details.append(note(`Сохранённая проверка результата: ${topic.session_result.check_id}${topic.session_result.review_status?` · ${topic.session_result.review_status}`:''}. Это метка источника, а не подтверждение истинности.`));
    details.append(note(`Источник темы: ${topic.source_ref.view_id} · версия ${topic.source_ref.version_id}`));this.content.append(details);
    const memberDetails=element('details','','topic-members-details');memberDetails.append(element('summary',`Утверждения · ${members.length.toLocaleString('ru')}`));const list=element('div','','topic-members');let shown=0;
    const append=():void=>{const end=Math.min(members.length,shown+50);for(;shown<end;shown++){const ordinal=members[shown];if(ordinal!==undefined)list.append(button(memberVisibilityLabel(shown,visibleInSlice(ordinal)),()=>openOrdinal(ordinal)));}more.hidden=shown>=members.length;};
    const more=button('Ещё утверждения темы',append);memberDetails.append(list,more);this.content.append(memberDetails);append();this.scroll.scrollTop=0;
  }
  private renderOverviewText(target:HTMLElement,text:string,context:{revision:string;provider_id:string},request:AbortController):void{
    const intent=this.intent;target.replaceChildren();
    for(const segment of overviewSegments(text)){
      if(!segment.citationId){target.append(document.createTextNode(segment.text));continue;}
      const id=segment.citationId,number=segment.citationNumber;
      const link=button(`[${number}]`,()=>{if(!request.signal.aborted&&this.intent===intent)this.actions.citation?.(id,context,intent);},'source-citation');
      link.dataset.nodeId=id;link.setAttribute('aria-label',`Открыть цитируемое утверждение ${number}`);link.title='Открыть цитируемое утверждение';link.disabled=!this.actions.citation;target.append(link);
    }
  }
  private async loadOverview(topic:Topic,context:{revision:string;provider_id:string},target:HTMLElement,request:AbortController,offset=0,textRevision?:string,body?:{element:HTMLElement;text:string}):Promise<void>{
    try{
      const page=await get(apiUrl(`/overviews/${encodeURIComponent(topic.id)}`,{revision:context.revision,offset,limit:window.innerWidth<700?1200:2000}),overviewSchema,request.signal);if(request.signal.aborted)return;
      if(page.topic_id!==topic.id||page.revision!==context.revision||page.provider_id!==context.provider_id||page.source_ref.view_id!==topic.source_ref.view_id||page.source_ref.version_id!==topic.source_ref.version_id||page.offset!==offset||(textRevision!==undefined&&page.text_revision!==textRevision))throw new Error('Версия сохранённого материала изменилась. Уже открытый текст сохранён.');
      if(topic.session_result&&(!page.session_result||(['receipt_id','run_id','event_id','check_id','version_id','role'] as const).some(key=>page.session_result![key]!==topic.session_result![key])))throw new Error('Сохранённый результат относится к другой обработке. Уже открытый текст сохранён.');
      let content=body;if(!content){const text=element('div','','prose');text.setAttribute('role','article');text.setAttribute('aria-label',topicRole(topic));content={element:text,text:''};const state=page.freshness==='current'?'метка обновления источника: текущий':page.freshness==='stale'?'может требовать обновления':'давность обновления неизвестна';const acceptance=page.accepted_at_that_snapshot===true?'Принят в сохранённом срезе.':page.accepted_at_that_snapshot===false?'Не принят в сохранённом срезе.':'Принятие в сохранённом срезе не указано.';target.replaceChildren(note(`${topicRole(topic)} · ${state}`),note(acceptance),note(page.domain_answer_current===false?'Не является подтверждённым актуальным ответом.':'Проверка по текущим данным не подтверждена.'),text);}
      if(!body&&page.overview_failure===true)target.append(note('Источник сообщает об ошибке подготовки материала.'));
      content.text+=page.text;
      if(content.text.length===0&&page.next_offset===null)content.element.replaceChildren(note('В этом представлении нет сохранённого текста. Ниже доступны связанные утверждения.'));
      else this.renderOverviewText(content.element,content.text,context,request);
      target.querySelector('.overview-next')?.remove();if(page.next_offset!==null){const next=page.next_offset,saved=content;const more=button('Продолжить чтение',()=>this.run(async()=>{setBusy(more,true,'Продолжить чтение');try{await this.loadOverview(topic,context,target,request,next,page.text_revision,saved);}finally{if(more.isConnected)setBusy(more,false,'Продолжить чтение');}}),'overview-next');target.append(more);}
    }catch(error){if(request.signal.aborted)return;if(error instanceof ApiError&&error.status===404&&offset===0){target.replaceChildren(note('Сохранённый текст пока недоступен. Ниже можно открыть связанные утверждения.'));return;}throw error;}
  }
  showTopicChoices(topics:readonly Topic[],choose:(topic:Topic)=>void,title='Материалы источника',description='Каждая карта или материал относится к своему представлению источника.'):void{
    this.finishLoading();this.cancel();this.pin=null;this.currentId=null;this.liveStatus.hidden=true;this.root.hidden=false;this.root.dataset.kind='topic';this.shell(title);this.content.replaceChildren(note(description));
    for(const group of [{title:'Карты выбранного реестра',topics:topics.filter(topic=>topic.navigation_membership==='selected_registry')},{title:'Исторические материалы',topics:topics.filter(topic=>topic.navigation_membership==='historical_registry')},{title:'Другие карты записей',topics:topics.filter(topic=>topic.purpose==='routing_only'&&!topic.navigation_membership)},{title:'Другие сохранённые материалы',topics:topics.filter(topic=>topic.purpose!=='routing_only'&&!topic.navigation_membership)}]){
      if(!group.topics.length)continue;this.content.append(element('h3',`${group.title} · ${group.topics.length.toLocaleString('ru')}`));
      for(const topic of group.topics){const choice=button(`${topic.display_title??topic.full_title}${topic.session_result?` · версия ${topic.source_ref.version_id}${topic.session_result.role==='input'?' · основание':''}`:''} · ${topic.member_count.toLocaleString('ru')} утверждений`,()=>choose(topic),'topic-choice');choice.append(element('small',topicRole(topic)));this.content.append(choice);}
    }
    this.scroll.scrollTop=0;
  }
  showCellMembers(total:number,ordinals:Uint32Array,open:(ordinal:number)=>void,next?:()=>void,counts?:ProxyEdgeCounts):void{this.finishLoading();this.cancel();this.pin=null;this.currentId=null;this.liveStatus.hidden=true;this.root.hidden=false;this.root.dataset.kind='cell';this.shell('Участок карты');this.content.replaceChildren(note(`${total.toLocaleString('ru')} утверждений во временной пространственной группе карты. Это не отдельная тема источника.`));if(counts)this.content.append(note(`Входящие: ${counts.incoming_external.toLocaleString('ru')}. Исходящие: ${counts.outgoing_external.toLocaleString('ru')}. Внутри: ${counts.internal.toLocaleString('ru')}. Всего связей: ${counts.total_unique.toLocaleString('ru')}.`));for(const ordinal of ordinals)this.content.append(button(`Открыть утверждение ${ordinal+1}`,()=>open(ordinal),'topic-choice'));if(next)this.content.append(button('Следующие утверждения',next));this.scroll.scrollTop=0;}
  markTopicContextChanged(id:string):void{if(this.root.hidden||this.root.dataset.kind!=='topic'||this.currentId!==id)return;this.liveStatus.replaceChildren(note('Вселенная обновилась. Показан обзор ранее открытой версии; его состав и актуальность могли измениться.'),button('Открыть текущий обзор',()=>this.actions.overview?.()));this.liveStatus.hidden=false;}
  markTopicUnavailable():void{if(this.root.dataset.kind!=='topic')return;this.liveStatus.replaceChildren(note('Выбранное созвездие недоступно в новой версии. Показан ранее полученный обзор.'));this.liveStatus.hidden=false;}
  showEdgeGroup(title:string,ordinals:Uint32Array,open:(ordinal:number)=>void):void{
    this.finishLoading();this.cancel();this.pin=null;this.currentId=null;this.liveStatus.hidden=true;this.root.hidden=false;this.root.dataset.kind='edge_bundle';this.shell(title);this.content.replaceChildren(note(`${ordinals.length.toLocaleString('ru')} исходных связей. У каждой связи свой тип и собственные фрагменты источников.`));
    const list=element('div','','edge-list');let shown=0;const more=button('Ещё исходные связи',append);const self=this;
    function append():void{const end=Math.min(ordinals.length,shown+50);for(;shown<end;shown++){const ordinal=ordinals[shown];if(ordinal===undefined)continue;list.append(button(`Открыть исходную связь ${ordinal+1}`,()=>open(ordinal),'edge-link'));}more.hidden=shown>=ordinals.length;}
    self.content.append(list,more);append();this.scroll.scrollTop=0;
  }
  private sourceLabel(source:string|null):string{const labels:Record<string,string>={unknown:'Источник неизвестен',event:'Сохранённый эпизод',episode:'Сохранённый эпизод',memory_raw_event:'Сохранённый эпизод',user_message:'Сообщение пользователя',assistant_message:'Сообщение ассистента',graph_assertion:'Утверждение графа'};return source?(labels[source]??source):'Источник неизвестен';}
  private unknownLabel(code:string):string{const known:Record<string,string>={missing_provenance:'Происхождение сведений не сохранено',missing_source:'Исходный материал не сохранён',unknown_precision:'Точность даты неизвестна',ambiguous_fragment:'Точное положение фрагмента не задано',partial_source:'Источник сохранён частично'};return known[code]??'Часть сведений об источнике неизвестна';}
}
