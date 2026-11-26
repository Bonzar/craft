import {sourceReadinessNotice} from './source-access.ts';
import {applyMembershipPatch,checkedDeltaManifest} from './membership-patch.ts';
import {deriveVisualSceneData} from './visual-scene-data.ts';
import './style.css';
import { ApiError, apiUrl, get, parsedEvent, response, write } from './api.ts';
import { availabilitySchema, bookmarksSchema, capabilitiesSchema, deltaSchema, edgeSchema, manifestSchema, nodeSchema, resetSchema, searchSchema, sessionSchema, topicsSchema } from './contracts.ts';
import type { Bookmark, Capabilities, Delta, Manifest, NodeCard, SearchPage, Topic, TopicHit, Topics } from './contracts.ts';
import type { GraphData } from './binary.ts';
import type { ScreenConnectivityResult } from './viewport-connections.ts';
import {proxyEdgeCounts} from './lod.ts';
import type { LodResult, ProxyEdgeCounts } from './lod.ts';
import { canReuseSnapshot } from './reset-policy.ts';
import { LifecycleTrace, lifecycle, opaqueTag } from './lifecycle.ts';
import type { LifecycleFields } from './lifecycle.ts';
import { sourceObjectAccess, sourceTransport } from './source-access.ts';
import { sameSliceContext, contextForSelector } from './slice-context.ts';
import type { SliceContext } from './slice-context.ts';
import {LiveFallback,POLL_INTERVAL_MS,SSE_START_MS,SSE_IDLE_MS} from './live-fallback.mjs';
import { SnapshotRecovery } from './recovery.ts';
import { sourceResponseIsCurrent, pinSnapshotCallback, pinRevisionCallback } from './interaction.ts';
import { button, element, note, setBusy } from './dom.ts';
import { canApplyDelta, codepointSegments, formatTime, humanError, temporalSelector, sourceHierarchy } from './logic.ts';
import type { Mode, Selector, Undated } from './logic.ts';
import { Reader } from './reader.ts';
import {SessionFilter} from './session-filter.ts';
import {approvedOverviewPath} from './topic-content.ts';
import { UniverseScene } from './scene.ts';
import type { MapTapSelection, Pick } from './scene.ts';
import { GraphWorker } from './worker-client.ts';
import { bool, object, optional, str, uint } from './schema.ts';
function byId<T extends HTMLElement>(id:string,ctor:{new():T}):T {const found=document.getElementById(id);if(!(found instanceof ctor))throw new Error(`Missing interface element: ${id}`);return found;}
const prepSchema=object({status:str,retry_after_ms:optional(uint,400)});
const helloSchema=object({cursor:str,revision:str,captured_at:optional(str,'')});
const okSchema=object({ok:optional(bool,true)});
// Explicit navigation panels sit above the persistent Reader (layer8).
const TRANSIENT_PANEL_LAYER='9';
function pause(ms:number,signal:AbortSignal):Promise<void>{return new Promise((resolve,reject)=>{const timer=setTimeout(resolve,ms);signal.addEventListener('abort',()=>{clearTimeout(timer);reject(new DOMException('Aborted','AbortError'));},{once:true});});}

type PinnedStatusResult={ok:true;card:NodeCard}|{ok:false;error:unknown};
interface ReadNavigation {manifest:Manifest;current:()=>boolean}
interface LivePinStatusPrefetch {id:string;pin:NonNullable<Reader['pinnedAvailability']>;cardRequest:AbortController|null;result:Promise<PinnedStatusResult>;current:()=>boolean;cancel:()=>void}

class Viewer {
  private trace=new LifecycleTrace();private selectedTopicId:string|null=null;private recovery=new SnapshotRecovery();private scene:UniverseScene|null=null;private graph:GraphData|null=null;private manifest:Manifest|null=null;private topics:Topics|null=null;
  private worker:GraphWorker|null=null;private capabilities:Capabilities|null=null;private csrf='';private stream:EventSource|null=null;
  private liveFallback=new LiveFallback();private streamDeadline:number|undefined;private pollOnly=false;
  private graphRequest:AbortController|null=null;private cardRequest:AbortController|null=null;private searchRequest:AbortController|null=null;
  private selector:Selector={mode:'current',at:null,history:false,undated:'separate'};private acceptedContext:SliceContext|null=null;private changingContext=false;private contextTicket=0;private acceptedTicket=0;private contextPending:{context:SliceContext;ticket:number}|null=null;private contextPump:Promise<void>|null=null;private graphLoading:Promise<void>|null=null;private cursor='';private liveQueue=Promise.resolve();private searchPage:SearchPage|null=null;
  private reader:Reader;private selectedOrdinal:number|null=null;private selectedNodeId:string|null=null;private authenticated=false;private feedbackTimer=0;private capabilityTimer=0;private capabilityNotice='';
  private canvas=byId('space',HTMLCanvasElement);private loading=byId('loading',HTMLElement);private login=byId('login',HTMLElement);private counts=byId('universe-counts',HTMLElement);
  private historyPanel=byId('history-panel',HTMLElement);private bookmarksPanel=byId('bookmarks-panel',HTMLElement);private searchPanel=byId('search-results',HTMLElement);
  private modeInput=byId('history-mode',HTMLSelectElement);private dateInput=byId('history-date',HTMLInputElement);private zoneInput=byId('history-zone',HTMLSelectElement);private oldInput=byId('show-history',HTMLInputElement);private undatedInput=byId('undated-mode',HTMLSelectElement);
  private runInput=byId('session-run',HTMLSelectElement);private outputSnapshot:number|null=null;
  private sessionFilter=new SessionFilter(byId('session-filter',HTMLSelectElement),byId('session-filter-status',HTMLElement),byId('session-filter-more',HTMLButtonElement),byId('session-filter-query',HTMLInputElement),undefined,byId('session-filter-selected',HTMLElement),()=>{if(this.manifest)this.updateCounts();});
  constructor(){
    window.addEventListener('graph-viewer-lifecycle',event=>{if(!(event instanceof CustomEvent))return;const detail:unknown=event.detail;if(typeof detail!=='object'||detail===null||!('kind'in detail)||typeof detail.kind!=='string'||!('fields'in detail)||typeof detail.fields!=='object'||detail.fields===null)return;const fields:LifecycleFields={};for(const [key,value]of Object.entries(detail.fields))if(typeof value==='number'||typeof value==='boolean'||value===null||(typeof value==='string'&&value.length<=80))fields[key]=value;this.trace.record(detail.kind,fields);});
    const navigation=performance.getEntriesByType('navigation')[0];this.trace.record('viewer-create',{navigation:navigation&&'type'in navigation&&typeof navigation.type==='string'?navigation.type:'unknown'});window.addEventListener('beforeunload',()=>this.trace.record('beforeunload'));window.addEventListener('pagehide',event=>this.trace.record('pagehide',{persisted:event.persisted}));window.addEventListener('pageshow',event=>this.trace.record('pageshow',{persisted:event.persisted}));
    this.reader=new Reader(byId('reader',HTMLElement),{materialLabel:id=>{const topic=this.topics?.topics.find(value=>value.id===id);return topic?.display_title??topic?.full_title??'Сохранённый материал';},material:(id,version,context,intent)=>{if(this.reader.intentGeneration!==intent)return;if(this.manifest?.revision!==context.revision||this.manifest.provider_id!==context.provider_id){this.notice('Срез изменился. Откройте материал снова, чтобы перейти к его основанию.');return;}const topic=this.topics?.topics.find(value=>value.id===id);if(!topic||topic.source_ref.version_id!==version){this.notice('Точная версия основания недоступна в этом срезе.');return;}this.openTopic(topic);},citation:(id,context,intent)=>this.run(()=>this.openCitedNode(id,context,intent)),close:()=>this.closeReader(),node:(id,context,intent)=>this.run(()=>context&&intent!==undefined?this.openCitedNode(id,context,intent):this.openNode(id)),edge:(id,context,intent)=>this.run(()=>context&&intent!==undefined?this.openReaderEdge(id,context,intent):this.openEdge(id)),bookmark:(kind,id,label,context)=>this.saveBookmark(kind,id,label,context),report:message=>this.notice(message),failed:error=>this.handleError(error),connectionInfo:id=>this.readerConnections(id),canOverview:()=>this.selectedTopicId!==null,overview:()=>{const topic=this.topics?.topics.find(value=>value.id===this.selectedTopicId);if(topic)this.openTopic(topic);}});
    const initial=this.readUrl();this.selector=initial.selector;this.acceptedContext=initial;this.applyContextInputs(initial);this.bind();this.updateHistoryCopy();this.run(()=>this.session());
    const diagnostics=Object.freeze({snapshot:()=>({...this.scene?.diagnostics(),selectedNodeId:this.reader.selectedId,selector:{...this.selector},sourceAvailable:byId('live-dot',HTMLElement).classList.contains('offline')===false}),projectNode:(ordinal:number)=>this.scene?.projectNode(ordinal)??{x:0,y:0,visible:false},projectEdge:(ordinal:number)=>this.scene?.projectEdge(ordinal)??{x:0,y:0,visible:false},projectProxy:(topicIndex:number)=>this.scene?.projectProxy(topicIndex)??{x:0,y:0,visible:false},lodAuditData:()=>this.scene?.lodAuditData()??null,screenConnectivityAuditData:()=>this.scene?.screenConnectivityAuditData()??null,envelopeAuditData:()=>this.scene?.envelopeAuditData()??null,lifecycle:()=>this.trace.snapshot()});
    Object.defineProperty(window,'__graphViewer',{value:diagnostics,writable:false,configurable:false});
  }
  private bind():void {
    byId('login-form',HTMLFormElement).addEventListener('submit',event=>{event.preventDefault();this.run(()=>this.authenticate());});
    byId('search-form',HTMLFormElement).addEventListener('submit',event=>{event.preventDefault();this.run(()=>this.search());});
    byId('history-form',HTMLFormElement).addEventListener('submit',event=>{event.preventDefault();this.run(async()=>{const selector={...temporalSelector(this.modeInput.value as Mode,this.dateInput.value,this.zoneInput.value,this.oldInput.checked,this.undatedInput.value as Undated),session_id:this.sessionFilter.value,run_id:this.sessionFilter.value?(this.runInput.value||null):null,output_snapshot_id:this.sessionFilter.value?this.outputSnapshot:null};const context={selector,day:this.dateInput.value,zone:this.zoneInput.value};this.hidePanel('history');await this.changeSliceContext(context);});});
    byId('session-filter',HTMLSelectElement).addEventListener('change',()=>{this.outputSnapshot=null;this.renderRuns('');});
    this.runInput.addEventListener('change',()=>{this.outputSnapshot=this.runInput.value?(this.manifest?.session_filter?.overview_history?.snapshot_id??null):null;this.renderRuns();});
    this.modeInput.addEventListener('change',()=>{if(this.modeInput.value!=='current')this.oldInput.checked=true;this.updateHistoryCopy();});
    byId('history-toggle',HTMLButtonElement).addEventListener('click',()=>{this.togglePanel('history');if(!this.historyPanel.hidden)void this.sessionFilter.open();});
    byId('filters-reset',HTMLButtonElement).addEventListener('click',()=>this.run(async()=>{const previous=this.currentContext();this.hidePanel('history');await this.changeSliceContext({...previous,selector:{...temporalSelector('current','',previous.zone,false,'separate'),session_id:null}});}));
    byId('history-close',HTMLButtonElement).addEventListener('click',()=>this.hidePanel('history'));
    byId('bookmarks-toggle',HTMLButtonElement).addEventListener('click',()=>{this.togglePanel('bookmarks');if(!this.bookmarksPanel.hidden)this.run(()=>this.showBookmarks());});
    byId('bookmarks-close',HTMLButtonElement).addEventListener('click',()=>this.hidePanel('bookmarks'));
    byId('search-close',HTMLButtonElement).addEventListener('click',()=>this.hideSearch());
    byId('reset',HTMLButtonElement).addEventListener('click',()=>{this.closeReader();this.scene?.home();});
    byId('layout-toggle',HTMLButtonElement).addEventListener('click',()=>this.run(()=>this.toggleLayout()));
    byId('topics-toggle',HTMLButtonElement).addEventListener('click',()=>this.showTopicCatalogue());
    byId('expand-all',HTMLButtonElement).addEventListener('click',()=>this.run(async()=>{this.closeReader();await this.scene?.expandAll();}));
    byId('zoom-in',HTMLButtonElement).addEventListener('click',()=>this.scene?.zoom(.75));byId('zoom-out',HTMLButtonElement).addEventListener('click',()=>this.scene?.zoom(1.33));
    byId('undated-open',HTMLButtonElement).addEventListener('click',()=>this.run(async()=>{const previous=this.currentContext();await this.changeSliceContext({...previous,selector:{...previous.selector,undated:previous.selector.undated==='only'?'separate':'only'}});}));
    byId('load-retry',HTMLButtonElement).addEventListener('click',()=>this.run(()=>this.authenticated?this.loadGraph():this.session()));
    byId('notice-close',HTMLButtonElement).addEventListener('click',()=>{byId('notice',HTMLElement).hidden=true;});
    byId('logout',HTMLButtonElement).addEventListener('click',()=>this.run(()=>this.logout()));
    window.addEventListener('keydown',event=>{if(event.key==='Escape'){this.hidePanel('history');this.hidePanel('bookmarks');this.hideSearch();this.closeReader();}});
    window.addEventListener('popstate',()=>{const query=new URLSearchParams(location.search);if(this.manifest&&!this.objectSourceReady(query.get('provider_id')))return;const context=this.readUrl(query);this.run(async()=>{if(await this.changeSliceContext(context)){if(query.get('id'))await this.openDeepLink(query);else this.closeReader();}});});
  }
  private currentContext():SliceContext{return this.acceptedContext??{selector:{...this.selector},day:this.dateInput.value,zone:this.zoneInput.value};}
  private applyContextInputs(context:SliceContext):void{this.modeInput.value=context.selector.mode;this.dateInput.value=context.day;this.zoneInput.value=context.zone;this.oldInput.checked=context.selector.history;this.undatedInput.value=context.selector.undated;this.sessionFilter?.setSelected(context.selector.session_id);this.outputSnapshot=context.selector.output_snapshot_id??null;this.renderRuns(context.selector.run_id??'');this.updateHistoryCopy();}
  private renderRuns(selected=this.runInput.value):void{
    const history=this.manifest?.selector.session_id===this.sessionFilter?.value?this.manifest?.session_filter?.overview_history:null;
    const option=(value:string,text:string,disabled=false):HTMLOptionElement=>{const node=document.createElement('option');node.value=value;node.textContent=text;node.disabled=disabled;return node;};
    this.runInput.replaceChildren(option('','Последние завершённые результаты'));
    const states:Record<string,string>={completed:'завершена',failed:'ошибка',yielded:'продолжается',waiting_model:'ожидает обработки',started:'начата',resumed:'продолжается',superseded:'заменена'};
    for(const run of history?.runs??[]){const date=run.recorded_at?formatTime(run.recorded_at,'instant'):run.run_id.slice(0,8),allowed=run.lifecycle==='completed';this.runInput.append(option(run.run_id,`${date} · ${states[run.lifecycle]??'состояние неизвестно'} · ${run.output_count} результатов${run.cause_status==='caller_context_only'?' · контекст запроса':''}`,!allowed));}
    if(selected&&![...this.runInput.options].some(item=>item.value===selected))this.runInput.append(option(selected,`Сохранённая обработка · ${selected.slice(0,8)}`));
    this.runInput.value=selected;this.runInput.disabled=!this.sessionFilter?.value||!history?.runs.length;
    const note=byId('session-run-note',HTMLElement);note.textContent=!this.sessionFilter?.value?'Выберите и примените сессию, чтобы увидеть её обработки.':!history?.runs.length?'Записанных результатов обработок пока нет. Это не означает, что обзоры раньше не создавались.':selected?'Показаны версии одной сохранённой обработки и её точные основания.':'Последние результаты объединяют завершённые обработки. Историю можно выбрать в списке.';
  }
  private clearContextSelection():void{this.selectedTopicId=null;this.scene?.setSelectedTopic(null);this.scene?.focusSource(null);this.cardRequest?.abort();this.searchRequest?.abort();this.reader.clear();this.selectedNodeId=null;this.selectedOrdinal=null;this.searchPage=null;this.searchPanel.hidden=true;byId('search-list',HTMLElement).replaceChildren();this.scene?.clearSelection();this.scene?.clearTapSelection();this.scene?.highlightSearch([]);}
  private async changeSliceContext(context:SliceContext):Promise<boolean>{
    if(!this.authenticated&&!this.manifest){this.selector={...context.selector};this.acceptedContext=context;this.applyContextInputs(context);return true;}
    if(!this.changingContext&&sameSliceContext(this.currentContext(),context)){this.applyContextInputs(this.currentContext());return true;}
    const ticket=++this.contextTicket;this.contextPending={context:{selector:{...context.selector},day:context.day,zone:context.zone},ticket};this.changingContext=true;
    this.stopLive();this.clearContextSelection();this.applyContextInputs(context);this.setConnection('Загружаем новый срез · на карте предыдущий',false);
    if(!this.contextPump){const pump=this.consumeSliceContexts();this.contextPump=pump;try{await pump;}finally{if(this.contextPump===pump)this.contextPump=null;}}
    else await this.contextPump;
    return ticket===this.acceptedTicket&&sameSliceContext(this.currentContext(),context);
  }
  private async consumeSliceContexts():Promise<void>{
    let lastError:unknown=null;
    try{
      // Finish already accepted same-context work before a different temporal slice can be drawn.
      await this.liveQueue.catch(()=>undefined);if(this.graphLoading)await this.graphLoading.catch(()=>undefined);
      while(this.contextPending){
        const target=this.contextPending;this.contextPending=null;lastError=null;
        if(sameSliceContext(this.currentContext(),target.context)){this.acceptedTicket=target.ticket;continue;}
        try{await this.loadGraph(target.context);this.acceptedTicket=target.ticket;}catch(error){lastError=error;}
      }
    }finally{
      this.changingContext=false;this.applyContextInputs(this.currentContext());this.updateUrl();this.updateCounts();
      if(this.manifest)this.startLive();
    }
    if(lastError!==null){this.notice('Новый срез не загружен. Показан предыдущий принятый срез.');throw lastError;}
  }
  private contextReady():boolean{if(this.manifest&&this.capabilities&&this.manifest.provider_id!==this.capabilities.provider_id){this.notice('Источник изменился. Дождитесь загрузки новой вселенной.');return false;}if(!this.changingContext)return true;this.notice('Новый срез ещё загружается. На карте показан предыдущий.');return false;}
  private objectSourceReady(provider:string|null,availability?:string):boolean{
    const manifest=this.manifest;const active=manifest&&(!this.capabilities||this.capabilities.provider_id===manifest.provider_id)?manifest.provider_id:null;
    const access=sourceObjectAccess(active,provider,availability);if(access==='available')return true;if(access==='foreign_source')this.clearContextSelection();
    this.notice(access==='foreign_source'?'Этот объект относится к другому источнику. Его источник сейчас недоступен.':'Источник этого объекта сейчас недоступен.');return false;
  }
  private closeReader():void{this.selectedTopicId=null;this.scene?.setSelectedTopic(null);this.cardRequest?.abort();this.reader.close();this.selectedOrdinal=null;this.selectedNodeId=null;this.scene?.clearSelection();this.updateUrl();}
  private run(task:()=>Promise<void>):void{task().catch(error=>this.handleError(error));}
  private handleError(error:unknown):void{
    if(error instanceof DOMException&&error.name==='AbortError')return;
    if(error instanceof ApiError&&error.status===401)this.showLogin();
    if(error instanceof ApiError&&['run_unavailable','output_snapshot_unavailable','causal_history_unavailable'].includes(error.code)){this.notice('Эта обработка недоступна в выбранной истории. Откройте фильтры и выберите последние результаты.');return;}
    if(error instanceof ApiError&&error.code==='session_unavailable'){this.notice('Сессия недоступна в этом срезе. Откройте фильтры и выберите другую сессию или все сессии.');return;}
    if(error instanceof ApiError&&error.status===409){if(this.changingContext)return;
      this.cardRequest?.abort();this.searchRequest?.abort();this.reader.finishLoading(true);const pinned=this.reader.pinnedNodeId;if(pinned){this.selectedNodeId=pinned;this.selectedOrdinal=null;}
      const saved=this.reader.hasLoadedContent;this.notice(saved?'Срез устарел. Получаем свежую версию; открытый текст остаётся на месте.':'Срез устарел. Получаем свежую версию.');
      this.recovery.run(async()=>{const loading=this.loadGraph();const request=this.graphRequest;await loading;if(request===this.graphRequest&&!request?.signal.aborted)this.notice(saved?'Карта обновлена. Повторите выбор; открытый текст сохранён.':'Карта обновлена. Выберите звезду снова.');}).catch(failure=>this.notice(humanError(failure)));return;
    }
    const message=humanError(error);if(message)this.notice(message);
  }
  private async session():Promise<void>{try{const state=await get('/session',sessionSchema);if(!state.authenticated){this.showLogin();return;}this.csrf=state.csrf_token;await this.start();}catch(error){this.failLoad('Сервер недоступен. Запустите источник и повторите подключение.');throw error;}}
  private showLogin():void{this.authenticated=false;byId('universe',HTMLElement).inert=true;clearTimeout(this.capabilityTimer);this.login.hidden=false;this.loading.hidden=true;this.stopLive();byId('logout',HTMLButtonElement).hidden=true;requestAnimationFrame(()=>byId('password',HTMLInputElement).focus());}
  private async authenticate():Promise<void>{const password=byId('password',HTMLInputElement);const submit=byId('login-button',HTMLButtonElement);setBusy(submit,true,'Войти');byId('login-error',HTMLElement).textContent='';try{const state=await write('/session','POST',{password:password.value},'',sessionSchema);if(!state.owner&&!state.authenticated)throw new Error('Не удалось войти');this.csrf=state.csrf_token;password.value='';await this.start();}catch(error){byId('login-error',HTMLElement).textContent=error instanceof ApiError&&error.status===401?'Пароль не подошёл.':humanError(error);}finally{password.value='';setBusy(submit,false,'Войти');}}
  private async start():Promise<void>{this.authenticated=true;byId('universe',HTMLElement).inert=false;this.login.hidden=true;byId('logout',HTMLButtonElement).hidden=false;if(!this.scene){try{this.scene=new UniverseScene(this.canvas,byId('topic-labels',HTMLElement),picked=>this.run(()=>this.picked(picked)),topic=>this.openTopic(topic),(x,y)=>this.feedback(x,y),error=>this.handleError(error),lod=>this.updateRepresentation(lod),entry=>this.run(()=>this.openNode(entry.id)),metrics=>this.updateReaderConnections(metrics));}catch{throw new Error('Для вселенной нужен браузер с WebGL 2. Проверьте аппаратное ускорение.');}}
    this.capabilities=await get('/capabilities',capabilitiesSchema);const query=new URLSearchParams(location.search);const foreign=sourceObjectAccess(this.capabilities.provider_id,query.get('provider_id'))==='foreign_source';if(foreign){this.clearContextSelection();const previous=this.currentContext();this.selector=temporalSelector('current','',previous.zone,false,'separate');this.acceptedContext={...previous,selector:{...this.selector}};this.applyContextInputs(this.acceptedContext);}await this.loadGraph();this.renderCapabilities();if(foreign){this.objectSourceReady(query.get('provider_id'));return;}await this.openDeepLink(query);}
  private async manifestFor(signal:AbortSignal,selector:Selector):Promise<Manifest>{while(!signal.aborted){const reply=await response(apiUrl('/graph',{mode:selector.mode,at:selector.at,history:selector.history?1:0,undated:selector.undated,session_id:selector.session_id,run_id:selector.run_id,output_snapshot_id:selector.output_snapshot_id}),signal);const body:unknown=await reply.json();if(reply.status===202){const preparation=prepSchema.parse(body);byId('loading-detail',HTMLElement).textContent='Источник готовит полную вселенную…';await pause(Math.min(3000,Math.max(100,preparation.retry_after_ms)),signal);continue;}const manifest=manifestSchema.parse(body);if((manifest.selector.session_id??null)!==(selector.session_id??null))throw new ApiError(409,'session_unavailable','Сессия недоступна в этом срезе.');if((manifest.selector.run_id??null)!==(selector.run_id??null)||(selector.output_snapshot_id!==null&&selector.output_snapshot_id!==undefined&&manifest.session_filter?.overview_history?.snapshot_id!==selector.output_snapshot_id))throw new ApiError(409,'run_unavailable','Обработка недоступна в сохранённой истории.');return manifest;}throw new DOMException('Aborted','AbortError');}
  private loadGraph(context?:SliceContext):Promise<void>{lifecycle('load-graph',{manual:context!==undefined});
    if(this.changingContext&&!context)return Promise.resolve();
    if(!context&&this.graphLoading)return this.graphLoading;
    const task=this.fetchGraph(context??this.currentContext());this.graphLoading=task;
    const settled=task.finally(()=>{if(this.graphLoading===task||this.graphLoading===settled)this.graphLoading=null;});this.graphLoading=settled;return settled;
  }
  private async fetchGraph(context:SliceContext):Promise<void>{
    if(!this.authenticated)return;this.cardRequest?.abort();this.reader.finishLoading();this.graphRequest?.abort();const request=new AbortController();this.graphRequest=request;this.stopLive();this.setConnection(this.changingContext?'Загружаем новый срез · на карте предыдущий':'Получаем вселенную',false);if(!this.graph){this.loading.hidden=false;byId('load-retry',HTMLButtonElement).hidden=true;}
    let nextWorker:GraphWorker|null=null;
    try{const manifest=await this.manifestFor(request.signal,context.selector);if(!manifest.complete_graph||manifest.schema!==1||manifest.nodes.stride!==16||manifest.edges.stride!==8||manifest.nodes.byte_length!==manifest.counts.nodes*16||manifest.edges.byte_length!==manifest.counts.edges*8)throw new Error('Источник не предоставил полный граф согласованного формата.');if(!this.capabilities||this.capabilities.provider_id!==manifest.provider_id)this.capabilities=await get('/capabilities',capabilitiesSchema,request.signal);if(this.capabilities.provider_id!==manifest.provider_id)throw new ApiError(409,'source_changed','Источник изменился во время загрузки.');if(this.manifest&&canReuseSnapshot(this.manifest,manifest)&&sameSliceContext(this.currentContext(),context)){this.cursor=this.cursor||manifest.live_cursor;this.loading.hidden=true;lifecycle('graph-same-revision-reused',{revision:opaqueTag(manifest.revision)});if(!this.changingContext)this.startLive();return;}const sourceChanged=this.manifest!==null&&this.manifest.provider_id!==manifest.provider_id;if(sourceChanged){this.clearContextSelection();this.setConnection('Источник сменился · загружаем текущий срез',false);}byId('loading-detail',HTMLElement).textContent=`Загружаем ${manifest.counts.eligible_before_status_filter.toLocaleString('ru')} звёзд и ${(manifest.counts.source_eligible_edges??manifest.counts.edges).toLocaleString('ru')} связей`;
      const [nodes,edges,topics]=await Promise.all([response(manifest.nodes.url,request.signal).then(reply=>reply.arrayBuffer()),response(manifest.edges.url,request.signal).then(reply=>reply.arrayBuffer()),get(manifest.topics_url,topicsSchema,request.signal)]);
      if(request.signal.aborted)return;if(topics.revision!==manifest.revision)throw new Error('Данные тем относятся к другой версии вселенной.');nextWorker=new GraphWorker();const graph=await nextWorker.load(nodes,edges,manifest.counts.nodes,manifest.counts.edges,manifest.selector.history,topics,manifest.revision);if(request.signal.aborted){nextWorker.dispose();return;}const visible=graph.visible.reduce((sum,value)=>sum+value,0);if(visible!==manifest.counts.visible_nodes||graph.visibleEdges.length/2!==manifest.counts.visible_edges)throw new Error('Источник и видимая вселенная расходятся по составу.');
      const first=!this.graph;const preparedWorker=nextWorker;const reboundOrdinal=await this.presentGraph(graph,manifest,topics,preparedWorker,first,()=>this.authenticated&&this.graphRequest===request&&this.capabilities?.provider_id===manifest.provider_id,request.signal);if(request.signal.aborted){preparedWorker.dispose();return;}this.worker?.dispose();this.worker=preparedWorker;nextWorker=null;const previousManifest=this.manifest;this.graph=graph;this.manifest=manifest;this.topics=topics;this.cursor=manifest.live_cursor;this.sessionFilter?.bind(manifest);
      if(this.selectedTopicId){
        if(!topics.topics.some(topic=>topic.id===this.selectedTopicId))this.reader.markTopicUnavailable();
        else if(previousManifest?.provider_id===manifest.provider_id&&previousManifest.revision!==manifest.revision)this.reader.markTopicContextChanged(this.selectedTopicId);
      }
      lifecycle('graph-accepted',{revision:opaqueTag(manifest.revision),nodes:graph.flags.length,edges:graph.edgeActive.length});this.selector={...manifest.selector};this.acceptedContext={selector:{...manifest.selector},day:context.day,zone:context.zone};if(!this.changingContext)this.applyContextInputs(this.acceptedContext);if(this.capabilities?.capabilities)this.renderCapabilities();if(sourceChanged)this.updateUrl();this.loading.hidden=true;this.updateCounts();this.searchRequest?.abort();this.searchPage=null;this.scene?.highlightSearch([]);this.searchPanel.hidden=true;this.selectedOrdinal=reboundOrdinal;this.updateReaderConnections();if(this.selectedNodeId)this.run(()=>this.refreshSelectedStatus());if(!this.changingContext)this.startLive();
      if(!manifest.counts.visible_nodes)this.notice('В этом срезе нет видимых утверждений. Измените дату или включите историю.');
    }catch(error){nextWorker?.dispose();if(request.signal.aborted||(error instanceof DOMException&&error.name==='AbortError'))return;this.setConnection(this.manifest?`Нет связи · данные от ${formatTime(this.manifest.captured_at,'instant')}`:'Источник недоступен',false);if(!this.graph)this.failLoad(humanError(error));throw error;}
  }
  private layoutMode:'topics'|'edges'='topics';
  private async toggleLayout():Promise<void>{
    const graph=this.graph,manifest=this.manifest,topics=this.topics,origin=this.worker;
    if(!this.contextReady()||!graph||!manifest||!topics||!origin||this.graphLoading)return;
    const button=byId('layout-toggle',HTMLButtonElement);if(button.disabled)return;
    const nextMode=this.layoutMode==='topics'?'edges':'topics',worker=new GraphWorker();
    let accepted=false;button.disabled=true;
    const current=()=>this.contextReady()&&this.graph===graph&&this.manifest===manifest&&this.worker===origin&&!this.graphLoading;
    try{
      const rebound=await this.presentGraph(graph,manifest,topics,worker,true,current,undefined,true,nextMode);
      if(!current())return;
      this.worker=worker;accepted=true;origin.dispose();this.selectedOrdinal=rebound;this.layoutMode=nextMode;
      button.setAttribute('aria-pressed',String(nextMode==='edges'));this.updateReaderConnections();
    }finally{button.disabled=false;if(!accepted)worker.dispose();}
  }
  private async presentGraph(graph:GraphData,manifest:Manifest,topics:Topics,worker:GraphWorker,resetCamera:boolean,current:()=>boolean,signal?:AbortSignal,preserveSearch=false,mode=this.layoutMode):Promise<number|null>{
    if(!current()||signal?.aborted)throw new DOMException('Aborted','AbortError');
    // Keep Source graph/topics/manifest in Viewer for deltas and Reader provenance.
    // A fresh worker bind discards Source-coordinate spatial caches, even at the
    // same revision; Scene and all worker geometry now share this visual layout.
    const visual=deriveVisualSceneData(graph,topics,{history:manifest.selector.history,sessionId:manifest.selector.session_id,mode});
    const visualGraph=await worker.loadPatchedGraph(visual.graph,{upsert_nodes:[],upsert_edges:[],remove_node_ordinals:[],remove_edge_ordinals:[],status_changes:[]},manifest.selector.history,visual.topics,manifest.revision);
    if(!current()||signal?.aborted)throw new DOMException('Aborted','AbortError');
    const visualManifest={...manifest,bounds:visual.layout.bounds};
    for(let attempt=0;attempt<4;attempt++){
      const node=this.selectedNodeId,topic=this.selectedTopicId,search=this.searchPage;
      const selectionCurrent=():boolean=>this.selectedNodeId===node&&this.selectedTopicId===topic&&this.searchPage===search;
      try{return await this.scene?.setGraph(visualGraph,visualManifest,visual.topics,worker,resetCamera,preserveSearch,{calibration:visual.calibration,selectedNodeId:node,searchNodeIds:preserveSearch?search?.hits.map(hit=>hit.id):undefined,signal,isCurrent:()=>current()&&selectionCurrent()})??null;}
      catch(error){if(!(error instanceof DOMException&&error.name==='AbortError')||!current()||signal?.aborted||selectionCurrent())throw error;if(attempt===3){window.setTimeout(()=>this.run(()=>this.loadGraph()),0);throw error;}}
    }
    throw new DOMException('Aborted','AbortError');
  }
  private failLoad(message:string):void {this.loading.hidden=false;byId('loading-detail',HTMLElement).textContent=message;byId('load-retry',HTMLButtonElement).hidden=false;this.setConnection('Нет связи с источником',false);}
  private readerConnections(id:string):{external:number;self:number}|null{const metrics=this.scene?.screenConnectivity;if(!metrics||metrics.revision!==this.manifest?.revision||this.reader.pinnedAvailability?.state==='unavailable'||this.selectedNodeId!==id||this.selectedOrdinal===null)return null;return{external:metrics.external_visible[this.selectedOrdinal]??0,self:metrics.canonical_self[this.selectedOrdinal]??0};}
  private updateReaderConnections(_metrics?:ScreenConnectivityResult):void{if(!this.selectedNodeId)return;const counts=this.readerConnections(this.selectedNodeId);this.reader.updateConnectivity(this.selectedNodeId,counts?.external??null,counts?.self??0);}
  private updateRepresentation(lod:LodResult):void{
    byId('expand-all',HTMLButtonElement).setAttribute('aria-pressed',String(!lod.navigation&&!lod.spatial_lod&&!lod.map_lod));if(this.selectedNodeId)this.reader.updateConnectivity(this.selectedNodeId,null);
    const sources=lod.proxies.filter(proxy=>proxy.kind!=='cell').length;
    byId('representation-counts',HTMLElement).textContent=`На карте: ${lod.coverage.canonical_nodes.toLocaleString('ru')} утверждений · ${sources} сфер`;
    const topicsButton=byId('topics-toggle',HTMLButtonElement),topicCount=this.topics?.topics.length??0,topicsCaption=topicsButton.querySelector('span');if(topicsCaption)topicsCaption.textContent=`Темы (${topicCount})`;topicsButton.setAttribute('aria-label',`Темы источника (${topicCount})`);
    const active=this.topics?.topics.find(topic=>topic.index===lod.navigation?.topic_path.at(-1));if(active&&this.topics&&!sourceHierarchy(this.topics.topics,this.topics.links).some(link=>link.parent===active.id))byId('representation-counts',HTMLElement).textContent+=' · Подтемы не заданы';
  }
  private showTopicCatalogue():void{if(!this.contextReady()||!this.topics)return;this.cardRequest?.abort();this.reader.showTopicChoices(this.topics.topics,topic=>this.openTopic(topic),'Материалы источника','Карты помогают найти исходные записи. Другие сохранённые материалы показаны отдельно; у каждого свой состав.');}
  private updateCounts():void{if(!this.manifest||!this.graph)return;this.renderRuns();const m=this.manifest;const visible=this.graph.visible.reduce((sum,value)=>sum+value,0);this.counts.textContent=`${visible.toLocaleString('ru')} утверждений · ${(this.graph.visibleEdges.length/2).toLocaleString('ru')} связей`;this.counts.title=`Всего доступно в срезе: ${m.counts.eligible_before_status_filter.toLocaleString('ru')} записей и ${(m.counts.source_eligible_edges??m.counts.edges).toLocaleString('ru')} связей. Заменённые и отменённые скрыты, если история выключена.`;byId('slice-label',HTMLElement).textContent=this.selector.mode==='current'?(this.selector.history?'Сейчас · с историей':'Сейчас'):this.selector.mode==='known_then'?`Известно тогда · ${this.currentContext().day}`:`О той дате · ${this.currentContext().day}`;if(this.selector.session_id)byId('slice-label',HTMLElement).textContent+=` · ${this.sessionFilter?.labelFor(this.selector.session_id)??'выбранная сессия'}`;const originNote=document.getElementById('session-origin-note'),unknown=m.session_filter?.unknown_origin_assertion_count??0;if(originNote){originNote.textContent=[this.selector.session_id&&visible===0?'По текущим фильтрам утверждений нет.':null,unknown?`Для ${unknown.toLocaleString('ru')} утверждений сессия неизвестна. Они доступны в режиме «Все сессии».`:null,this.selector.session_id&&m.session_filter?.semantic_search_available!==true?'В этой сессии доступен поиск по словам.':null,(m.session_filter?.overview_history?.pending_output_count??0)>0?`Незавершённых результатов: ${m.session_filter!.overview_history!.pending_output_count}. Они не показаны как готовые.`:null,(m.session_filter?.overview_history?.caller_context_output_count??0)>0?`Результатов в контексте запроса: ${m.session_filter!.overview_history!.caller_context_output_count}. Они не приписаны исходным сообщениям сессии.`:null,m.session_filter?.overview_history?.history_complete===false?`Полнота истории результатов не подтверждена. Сохранённых материалов: ${m.session_filter.overview_history.known_results.toLocaleString('ru')}; связь с другими результатами могла быть не записана.`:null].filter(Boolean).join(' ');originNote.hidden=!originNote.textContent;}byId('undated-count',HTMLElement).textContent=`Без даты события: ${m.counts.undated_known_count.toLocaleString('ru')}. Известных будущих сведений: ${m.counts.future_valid_known_count.toLocaleString('ru')}.`;const undated=byId('undated-open',HTMLButtonElement);undated.hidden=this.selector.mode!=='known_now'||m.counts.undated_known_count===0;undated.textContent=this.selector.undated==='only'?'К датированному срезу →':`Без даты: ${m.counts.undated_known_count.toLocaleString('ru')} →`;undated.setAttribute('aria-label',this.selector.undated==='only'?'Вернуться к датированному срезу':'Показать сведения без даты события');}
  private renderCapabilities():void{if(!this.capabilities)return;clearTimeout(this.capabilityTimer);const caps=this.capabilities,semantic=caps.capabilities.semantic_search&&(!this.selector.session_id||this.manifest?.session_filter?.semantic_search_available===true);this.sessionFilter?.setEnabled(caps.capabilities.session_filter);if(!caps.capabilities.semantic_search&&this.authenticated)this.capabilityTimer=window.setTimeout(()=>this.run(async()=>{this.capabilities=await get('/capabilities',capabilitiesSchema);this.renderCapabilities();}),15000);byId('semantic-mode',HTMLInputElement).disabled=!semantic;byId('semantic-mode',HTMLInputElement).closest('label')?.setAttribute('title',semantic?'Поиск по смыслу':this.selector.session_id?'В этой сессии доступен поиск по словам':'Поиск по смыслу недоступен: модель или индекс не готовы');if(!semantic&&document.querySelector<HTMLInputElement>('input[name="search-mode"]:checked')?.value==='semantic'){const keyword=document.querySelector<HTMLInputElement>('input[name="search-mode"][value="keyword"]');if(keyword)keyword.checked=true;}
    if(this.manifest&&caps.provider_id===this.manifest.provider_id&&!caps.capabilities.live)this.startLive();
    const fixture=caps.readiness.find(item=>item.code==='synthetic_fixture');const fixtureLabel=byId('fixture-label',HTMLElement);fixtureLabel.hidden=!fixture;if(fixture)fixtureLabel.textContent='Синтетический контрольный набор';
    const labels:Record<string,string>={semantic_unavailable:'Поиск по смыслу пока недоступен.',semantic_preparing:'Поиск по смыслу готовится.',global_cancellation:'Отмена утверждений показана только по явным отметкам источника.'};const cancellation=byId('source-status-limitation',HTMLElement);cancellation.textContent=caps.readiness.some(item=>item.code==='global_cancellation')?labels.global_cancellation!:'';cancellation.hidden=!cancellation.textContent;const limitations=caps.readiness.filter(item=>item.code!=='synthetic_fixture'&&item.code!=='global_cancellation');const message=sourceReadinessNotice(limitations);if(message&&message!==this.capabilityNotice){this.capabilityNotice=message;this.notice(message);}if(!caps.capabilities.topics&&this.capabilityNotice!=='no-topics'){this.capabilityNotice='no-topics';this.notice('Источник пока не предоставляет темы. Все утверждения показаны без подписей созвездий.');}}
  private async picked(picked:Pick|null):Promise<void>{
    const manifest=this.manifest,scene=this.scene;
    if(!this.contextReady()||!scene||!picked||!manifest||picked.revision!==manifest.revision||picked.generation!==scene.projection?.generation)return;
    this.closeReader();const topics=this.topics,graph=this.graph;
    const snapshot=scene.selectTap(picked,selection=>this.run(()=>this.readTapSelection(selection,manifest,topics,graph)));
    if(!snapshot||picked.kind!=='node')return;
    // Only the caption is enriched: a tap never opens or navigates the Reader.
    try{const card=await get(apiUrl(`/graph/${encodeURIComponent(snapshot.revision)}/nodes/${picked.ordinal}`,{offset:0,limit:480}),nodeSchema);
      if(!scene.tapCurrent(snapshot.token)||!sourceResponseIsCurrent(manifest,this.manifest,false))return;
      if(card.revision!==snapshot.revision||card.ordinal!==picked.ordinal)throw new Error('Источник вернул текст другой звезды');
      scene.updateTapLabel(snapshot.token,card.text);
    }catch(error){if(scene.tapCurrent(snapshot.token)&&sourceResponseIsCurrent(manifest,this.manifest,false))throw error;}
  }
  private async readTapSelection(snapshot:MapTapSelection,manifest:Manifest,topics:Topics|null,graph:GraphData|null):Promise<void>{
    const navigation:ReadNavigation={manifest,current:()=>this.manifest?.provider_id===manifest.provider_id};
    if(!this.scene?.tapCurrent(snapshot.token)||!sourceResponseIsCurrent(manifest,this.manifest,false)||snapshot.revision!==manifest.revision||!this.contextReady())return;
    const pick=snapshot.pick,stale=():void=>this.notice('Источник или открытый текст изменился. Откройте объект снова.');
    // Each rendered page owns its Reader intent, independently of the map camera and LOD.
    const view=(render:(node:(ordinal:number)=>void,edge:(ordinal:number)=>void,current:()=>boolean)=>void):void=>{
      let intent=-1;const current=():boolean=>navigation.current()&&this.reader.intentGeneration===intent;
      const node=(ordinal:number):void=>{if(!current()){stale();return;}this.run(()=>this.openOrdinal(ordinal,navigation));};
      const edge=(ordinal:number):void=>{if(!current()){stale();return;}this.run(()=>this.openEdgeOrdinal(ordinal,navigation));};
      render(node,edge,current);intent=this.reader.intentGeneration;
    };
    if(pick.kind==='node'){await this.openOrdinal(pick.ordinal,navigation);return;}
    if(pick.kind==='edge'){await this.openEdgeOrdinal(pick.ordinal,navigation);return;}
    this.cardRequest?.abort();this.selectedNodeId=null;this.selectedOrdinal=null;
    const showTopic=(topic:Topic,members:readonly number[],edges:Uint32Array,counts?:ProxyEdgeCounts):void=>view(node=>{
      this.selectedTopicId=topic.id;
      const projection=this.scene?.projection,summary=projection?.revision===manifest.revision?projection.map_lod?.projection?.topic_summaries.find(value=>value.topic_index===topic.index):undefined;
      this.reader.showTopic(topic,members,node,edges.length||counts||summary?{represented:summary?.owned_node_count??members.length,visibleMembers:summary?.visible_source_member_count??members.length,internalEdges:edges.length,edgeCounts:counts,sharedMembers:summary?.shared_member_count,visibleElsewhere:summary?.visible_elsewhere_count}:undefined,ordinal=>Boolean(graph?.visible[ordinal]),manifest);this.updateUrl('topic',topic.id);
    });
    if(pick.kind==='cell'){
      const page=(offset:number):void=>view((node,_edge,current)=>{const end=Math.min(snapshot.members.length,offset+50);this.reader.showCellMembers(snapshot.members.length,snapshot.members.subarray(offset,end),node,end<snapshot.members.length?()=>{if(!current()){stale();return;}page(end);}:undefined,snapshot.counts);});page(0);return;
    }
    if(pick.kind==='shell_choices'){
      view((_node,_edge,current)=>this.reader.showTopicChoices(topics?.topics.filter(topic=>pick.topicIds.includes(topic.id))??[],topic=>{if(!current()){stale();return;}showTopic(topic,[...new Set(topics?.memberships.filter(item=>item.topic_index===topic.index).flatMap(item=>item.node_indices)??[])],new Uint32Array());}));return;
    }
    if(pick.kind==='topic_proxy'||pick.kind==='topic_shell'){
      const topic=topics?.topics.find(item=>item.id===pick.topicId);if(topic){const members=topic.session_result?[...new Set(topics?.memberships.filter(row=>row.topic_index===topic.index).flatMap(row=>row.node_indices)??[])]:Array.from(snapshot.members);showTopic(topic,members,snapshot.edges,snapshot.counts);}return;
    }
    if(pick.kind==='edge_bundle')view((_node,edge)=>this.reader.showEdgeGroup('Связи между созвездиями',snapshot.edges,edge));
  }
  private async highlightSelection(ordinal:number):Promise<void>{const worker=this.worker;if(!worker)return;const indices=await worker.incident(ordinal);if(this.worker===worker&&this.selectedOrdinal===ordinal)this.scene?.highlightIncident(indices);}
  private async openOrdinal(ordinal:number,navigation?:ReadNavigation):Promise<void>{
    const manifest=navigation?.manifest??this.manifest;if(!manifest||!this.contextReady()||navigation&&!navigation.current())return;this.cardRequest?.abort();const request=new AbortController();this.cardRequest=request;this.reader.loading();const intent=this.reader.intentGeneration;
    const current=():boolean=>navigation?!request.signal.aborted&&this.cardRequest===request&&navigation.current()&&this.reader.intentGeneration===intent:this.cardResponseCurrent(manifest,request);
    try{const [card]=await Promise.all([get(`/graph/${encodeURIComponent(manifest.revision)}/nodes/${ordinal}`,nodeSchema,request.signal),navigation?undefined:this.scene?.revealNodes([ordinal])]);if(!current())return;if(card.revision!==manifest.revision||card.ordinal!==ordinal)throw new Error('Источник вернул утверждение другой версии');this.selectedOrdinal=ordinal;if(!navigation){this.scene?.flyNode(ordinal);this.run(()=>this.highlightSelection(ordinal));}this.selectedNodeId=card.id;lifecycle('node-selection',{identity:opaqueTag(card.id)});this.showNodeCard(card,manifest);this.updateUrl('node',card.id);}
    catch(error){if(!current())return;this.reader.error(humanError(error));throw error;}
  }
  private async openCitedNode(id:string,context:{revision:string;provider_id:string},readerIntent:number):Promise<void>{
    if(this.reader.intentGeneration!==readerIntent||this.manifest?.provider_id!==context.provider_id)return;
    this.cardRequest?.abort();const request=new AbortController();this.cardRequest=request;this.reader.loading('Открываем цитируемое утверждение…');const intent=this.reader.intentGeneration;
    const current=():boolean=>!request.signal.aborted&&this.cardRequest===request&&this.reader.intentGeneration===intent&&this.manifest?.provider_id===context.provider_id;
    try{
      const card=await get(apiUrl(`/nodes/${encodeURIComponent(id)}`,{revision:context.revision}),nodeSchema,request.signal);if(!current())return;
      if(card.id!==id||card.revision!==context.revision)throw new Error('Источник вернул другое цитируемое утверждение.');
      this.selectedNodeId=card.id;this.selectedOrdinal=null;
      const topics=this.topics?.revision===context.revision?this.topics:undefined;this.reader.showNode(card,topics,undefined,context);this.updateUrl('node',id);
    }catch(error){if(!current())return;this.reader.error(error instanceof ApiError&&(error.status===404||error.status===409)?'Цитируемое утверждение недоступно в сохранённой версии.':humanError(error));}
  }
  private async openReaderEdge(id:string,context:{revision:string;provider_id:string},readerIntent:number):Promise<void>{
    if(this.reader.intentGeneration!==readerIntent||this.manifest?.provider_id!==context.provider_id)return;
    this.cardRequest?.abort();const request=new AbortController();this.cardRequest=request;this.reader.loading('Открываем связь сохранённой версии…');const intent=this.reader.intentGeneration;
    const current=():boolean=>!request.signal.aborted&&this.cardRequest===request&&this.reader.intentGeneration===intent&&this.manifest?.provider_id===context.provider_id;
    try{const card=await get(apiUrl(`/edges/${encodeURIComponent(id)}`,{revision:context.revision}),edgeSchema,request.signal);if(!current())return;
      if(card.id!==id||card.revision!==context.revision)throw new Error('Источник вернул другую связь.');
      this.selectedNodeId=null;this.selectedOrdinal=null;this.updateUrl('edge',id);await this.reader.showEdge(card,context);
    }catch(error){if(!current())return;this.reader.error(error instanceof ApiError&&(error.status===404||error.status===409)?'Связь недоступна в сохранённой версии.':humanError(error));}
  }
  private async openNode(id:string):Promise<void>{
    const manifest=this.manifest;if(!manifest||!this.contextReady())return;this.reader.loading();this.cardRequest?.abort();const request=new AbortController();this.cardRequest=request;
    try{const card=await get(apiUrl(`/nodes/${encodeURIComponent(id)}`,{revision:manifest.revision}),nodeSchema,request.signal);if(!this.cardResponseCurrent(manifest,request))return;this.selectedOrdinal=card.ordinal;await this.scene?.revealNodes([card.ordinal]);if(!this.cardResponseCurrent(manifest,request))return;this.scene?.flyNode(card.ordinal);this.run(()=>this.highlightSelection(card.ordinal));this.selectedNodeId=card.id;lifecycle('node-selection',{identity:opaqueTag(card.id)});this.showNodeCard(card,manifest);this.updateUrl('node',id);}
    catch(error){if(!this.cardResponseCurrent(manifest,request))return;this.reader.error(error instanceof ApiError&&error.status===404?'Утверждение недоступно в выбранном срезе. Включите историю или измените дату.':humanError(error));throw error;}
  }
  private cardResponseCurrent(manifest:Manifest,request:AbortController):boolean{
    const current=this.manifest&&this.capabilities?.provider_id===this.manifest.provider_id?this.manifest:null;
    const accepted=sourceResponseIsCurrent(manifest,current,request.signal.aborted||this.cardRequest!==request);
    if(!accepted&&this.cardRequest===request)this.reader.finishLoading();return accepted;
  }
  private showNodeCard(card:NodeCard,manifest:Manifest):void{
    this.reader.showNode(card,this.topics??undefined,pinSnapshotCallback({provider_id:manifest.provider_id,revision:card.revision},()=>this.manifest,id=>{const topic=this.topics?.topics.find(value=>value.id===id);if(topic)this.openTopic(topic);else this.notice('Тема недоступна в текущей версии.');},()=>this.notice('Срез изменился. Откройте утверждение снова, чтобы перейти к теме.')),{revision:card.revision,provider_id:manifest.provider_id});
  }
  private async refreshSelectedStatus(prefetch?:LivePinStatusPrefetch):Promise<void>{
    const manifest=this.manifest,id=this.selectedNodeId,cardRequest=this.cardRequest;let pin=this.reader.pinnedAvailability;if(!manifest||!id||!pin){prefetch?.cancel();return;}if(prefetch&&(prefetch.id!==id||prefetch.pin!==pin||prefetch.cardRequest!==cardRequest||!prefetch.current())){prefetch.cancel();return;}
    const current=():boolean=>this.manifest===manifest&&this.cardRequest===cardRequest&&this.capabilities?.provider_id===manifest.provider_id&&this.selectedNodeId===id&&this.reader.pinnedNodeId===id&&this.reader.pinnedAvailability===pin&&(!prefetch||prefetch.current());
    try{
      const result=prefetch?await prefetch.result:null;if(!current())return;if(result&&!result.ok)throw result.error;const card=result&&result.ok?result.card:await get(apiUrl(`/nodes/${encodeURIComponent(id)}`,{revision:manifest.revision}),nodeSchema);if(!current())return;
      if(card.id!==id||card.revision!==manifest.revision)throw new Error('Утверждение относится к другой версии графа.');
      await this.scene?.revealNodes([card.ordinal]);if(!current())return;
      this.selectedOrdinal=card.ordinal;this.reader.updateAvailability(id,'available',manifest.revision);pin=this.reader.pinnedAvailability;this.reader.updateStatus(id,card.status,manifest.revision);this.updateReaderConnections();await this.highlightSelection(card.ordinal);
    }catch(error){if(!current())return;if(error instanceof ApiError&&error.status===404){this.selectedOrdinal=null;this.scene?.clearSelection();this.reader.updateAvailability(id,'unavailable',manifest.revision);return;}throw error;}finally{prefetch?.cancel();}
  }
  private async openEdgeOrdinal(ordinal:number,navigation?:ReadNavigation):Promise<void>{const manifest=navigation?.manifest??this.manifest;if(!manifest||!this.contextReady()||navigation&&!navigation.current())return;await this.fetchEdge(`/graph/${encodeURIComponent(manifest.revision)}/edges/${ordinal}`,navigation);}
  private async openEdge(id:string):Promise<void>{if(!this.manifest||!this.contextReady())return;await this.fetchEdge(apiUrl(`/edges/${encodeURIComponent(id)}`,{revision:this.manifest.revision}));}
  private async fetchEdge(path:string,navigation?:ReadNavigation):Promise<void>{
    const manifest=navigation?.manifest??this.manifest;if(!manifest||navigation&&!navigation.current())return;this.reader.loading('Открываем связь…');this.cardRequest?.abort();const request=new AbortController();this.cardRequest=request;const intent=this.reader.intentGeneration;
    const current=():boolean=>navigation?!request.signal.aborted&&this.cardRequest===request&&navigation.current()&&this.reader.intentGeneration===intent:this.cardResponseCurrent(manifest,request);
    try{const card=await get(path,edgeSchema,request.signal);if(!current())return;if(card.revision!==manifest.revision)throw new Error('Источник вернул связь другой версии');
      if(!navigation){const endpoints=await Promise.all([card.from.id,card.to.id].map(id=>get(apiUrl(`/nodes/${encodeURIComponent(id)}`,{revision:card.revision}),nodeSchema,request.signal)));
        if(!current())return;await this.scene?.revealNodes(endpoints.map(node=>node.ordinal));if(!current())return;
        if(endpoints[0])this.scene?.flyNode(endpoints[0].ordinal);this.scene?.highlightIncident(new Uint32Array(endpoints.map(node=>node.ordinal)));}
      this.selectedNodeId=null;this.selectedOrdinal=null;this.updateUrl('edge',card.id);await this.reader.showEdge(card,{revision:card.revision,provider_id:manifest.provider_id});
    }catch(error){if(!current())return;this.reader.error(humanError(error));throw error;}
  }
  private openTopic(requested:Topic):void{
    if(!this.topics||!this.contextReady())return;const topic=this.topics.topics.find(value=>value.id===requested.id);if(!topic){this.notice('Тема недоступна в выбранном срезе.');return;}this.cardRequest?.abort();this.selectedNodeId=null;this.selectedOrdinal=null;this.selectedTopicId=topic.id;const manifestSnapshot=this.manifest,topicsSnapshot=this.topics,graphSnapshot=this.graph;this.scene?.clearSelection();if(manifestSnapshot)this.scene?.showTopicContext(topic.id,snapshot=>this.run(()=>this.readTapSelection(snapshot,manifestSnapshot,topicsSnapshot,graphSnapshot)));
    const members=this.topics.memberships.find(item=>item.topic_index===topic.index)?.node_indices??[];const lod=this.scene?.projection;const proxyIndex=lod?.proxies.findIndex(proxy=>proxy.kind!=='cell'&&proxy.topic_id===topic.id)??-1;const proxy=proxyIndex>=0?lod?.proxies[proxyIndex]:undefined;
    const revision=this.manifest?.revision??'';const snapshotGraph=this.graph;const stale=():void=>this.notice('Срез изменился. Откройте тему снова, чтобы выбрать объект в текущей версии.');const memberAction=pinRevisionCallback(revision,()=>this.manifest?.revision??null,ordinal=>this.run(()=>this.openOrdinal(ordinal)),stale);
    const summary=lod?.revision===revision?lod.map_lod?.projection?.topic_summaries.find(value=>value.topic_index===topic.index):undefined;
    this.reader.showTopic(topic,members,memberAction,proxy&&proxy.kind!=='cell'?{represented:proxy.represented_count,visibleMembers:proxy.visible_source_member_count,internalEdges:proxy.internal_edge_count,edgeCounts:lod?proxyEdgeCounts(lod)[proxyIndex]:undefined,externalVisible:this.scene?.screenConnectivity?.external_visible[(this.graph?.flags.length??0)+proxyIndex],sharedMembers:summary?.shared_member_count,visibleElsewhere:summary?.visible_elsewhere_count}:summary?{represented:summary.owned_node_count,visibleMembers:summary.visible_source_member_count,internalEdges:0,sharedMembers:summary.shared_member_count,visibleElsewhere:summary.visible_elsewhere_count}:undefined,ordinal=>Boolean(snapshotGraph?.visible[ordinal]),this.manifest?{revision:this.manifest.revision,provider_id:this.manifest.provider_id}:undefined);this.updateUrl('topic',topic.id);
  }
  private openPinnedTopic(topic:Topic,topics:Topics,manifest:Manifest,graph:GraphData|null):void{
    if(this.manifest?.provider_id!==manifest.provider_id||topics.revision!==manifest.revision)return;
    this.cardRequest?.abort();this.selectedNodeId=null;this.selectedOrdinal=null;this.selectedTopicId=topic.id;
    let intent=-1;const current=():boolean=>this.manifest?.provider_id===manifest.provider_id&&this.reader.intentGeneration===intent;
    const navigation:ReadNavigation={manifest,current:()=>this.manifest?.provider_id===manifest.provider_id};
    this.reader.showTopic(topic,topics.memberships.find(item=>item.topic_index===topic.index)?.node_indices??[],ordinal=>{if(!current())return;this.run(()=>this.openOrdinal(ordinal,navigation));},undefined,ordinal=>Boolean(graph?.visible[ordinal]),manifest);intent=this.reader.intentGeneration;this.updateUrl('topic',topic.id);
  }
  private openTopicSearchHit(hit:TopicHit,revision:string,manifest:Manifest|null,topics:Topics|null,graph:GraphData|null):void{
    if(!manifest||!topics||manifest.revision!==revision||topics.revision!==revision||this.manifest?.provider_id!==manifest.provider_id)return;
    approvedOverviewPath(hit.overview_url,hit.topic_id,window.location.origin);
    const topic=topics.topics.find(value=>value.id===hit.topic_id);
    if(!topic||topic.index!==hit.topic_index||topic.source_ref.view_id!==hit.view_id){this.notice('Материал недоступен в сохранённой версии поиска.');return;}
    this.hideSearch();if(this.manifest.revision===manifest.revision){this.scene?.clearSelection();this.scene?.showTopicContext(topic.id,snapshot=>this.run(()=>this.readTapSelection(snapshot,manifest,topics,graph)));}this.openPinnedTopic(topic,topics,manifest,graph);
  }
  private async search(cursor?:string):Promise<void>{const manifest=this.manifest;if(!manifest||!this.contextReady())return;const query=byId('search',HTMLInputElement).value.trim();if(!query){this.hideSearch();return;}this.hidePanel('history');this.hidePanel('bookmarks');byId('notice',HTMLElement).hidden=true;const mode=document.querySelector<HTMLInputElement>('input[name="search-mode"]:checked')?.value==='semantic'?'semantic':'keyword';if(mode==='semantic'&&(!this.capabilities?.capabilities.semantic_search||this.selector.session_id&&manifest.session_filter?.semantic_search_available!==true)){this.notice('Поиск по смыслу недоступен. Модель или индекс не готовы.');return;}this.searchRequest?.abort();const request=new AbortController();this.searchRequest=request;this.searchPanel.querySelector('[data-panel-feedback]')?.remove();this.searchPanel.style.zIndex=TRANSIENT_PANEL_LAYER;this.searchPanel.hidden=false;const state=byId('search-state',HTMLElement);state.textContent='Ищем в выбранной вселенной…';if(!cursor){byId('search-list',HTMLElement).replaceChildren();this.scene?.highlightSearch([]);}
    try{const page=await get(apiUrl('/search',{q:query,mode,revision:manifest.revision,limit:50,cursor}),searchSchema,request.signal);if(request.signal.aborted||this.searchRequest!==request||this.searchPanel.hidden||this.manifest?.revision!==manifest.revision)return;if(page.revision!==manifest.revision||page.mode!==mode||page.query!==query)throw new Error('Результаты относятся к другой версии поиска');this.searchPage=cursor&&this.searchPage?{...page,topic_hits:[...(this.searchPage.topic_hits??[]),...(page.topic_hits??[])],hits:[...this.searchPage.hits,...page.hits]}:page;state.textContent=this.searchPage.hits.length||(this.searchPage.topic_hits?.length??0)?'':'В выбранном срезе совпадений нет.';byId('search-heading',HTMLElement).textContent=mode==='semantic'?'По смыслу':'По словам';this.renderSearch(this.searchPage);}
    catch(error){if(request.signal.aborted||this.searchRequest!==request||this.searchPanel.hidden)return;state.textContent=mode==='semantic'?'Поиск по смыслу недоступен. Проверьте готовность модели и индекса.':humanError(error);throw error;}}
  private renderSearch(page:SearchPage):void{
    const list=byId('search-list',HTMLElement),manifest=this.manifest,topics=this.topics,graph=this.graph;list.replaceChildren();
    const snippet=(row:HTMLElement,text:string,highlights:{start:number;end:number}[]):void=>{let segments;try{segments=codepointSegments(text,highlights);}catch{segments=[{text,marked:false}];}for(const segment of segments)row.append(segment.marked?element('mark',segment.text):document.createTextNode(segment.text));};
    for(const hit of page.topic_hits??[]){
      const row=button('',()=>{try{this.openTopicSearchHit(hit,page.revision,manifest,topics,graph);}catch(error){this.notice(humanError(error));}},'result-row topic-result');
      row.append(element('strong',hit.label),element('small',`${hit.result_kind==='routing_map'?'Карта записей':'Сохранённый материал'} · ${hit.member_count.toLocaleString('ru')} утверждений`));snippet(row,hit.snippet,hit.highlights);list.append(row);
    }
    for(const hit of page.hits){const row=button('',()=>{this.hideSearch();this.run(()=>this.openNode(hit.id));},'result-row');snippet(row,hit.snippet,hit.highlights);row.append(element('small','Перейти к звезде →'));list.append(row);}
    if(page.next_cursor!==null)list.append(button('Ещё результаты',()=>this.run(()=>this.search(page.next_cursor??undefined))));this.scene?.highlightSearch(page.hits.map(hit=>hit.ordinal));
  }
  private async saveBookmark(kind:'node'|'topic',id:string,label:string,context?:{provider_id:string;selector?:Selector}):Promise<void>{if(!this.manifest)return;await write(`/bookmarks/${crypto.randomUUID()}`,'PUT',{object_kind:kind,object_id:id,provider_id:context?.provider_id??this.manifest.provider_id,label:Array.from(label).slice(0,180).join(''),selector:context?.selector??this.selector},this.csrf,okSchema);this.notice('Сохранено в личные закладки');if(!this.bookmarksPanel.hidden)await this.showBookmarks();}
  private async showBookmarks():Promise<void>{this.bookmarksPanel.querySelector('[data-panel-feedback]')?.remove();const list=byId('bookmarks-list',HTMLElement);list.replaceChildren(note('Загружаем закладки…'));const page=await get('/bookmarks',bookmarksSchema).catch(error=>{list.replaceChildren();throw error;});list.replaceChildren();if(!page.items.length)list.append(note('Сохранённых узлов и тем пока нет. Откройте звезду или тему и добавьте её в закладки.'));for(const bookmark of page.items){const row=element('div','','bookmark-row');const open=button(`${bookmark.object_kind==='topic'?'✦':'·'} ${bookmark.label||'Сохранённый объект'}`,()=>this.run(()=>this.openBookmark(bookmark)),'bookmark-open');const remove=button('×',()=>this.run(async()=>{await response(`/bookmarks/${encodeURIComponent(bookmark.id)}`,undefined,{method:'DELETE',headers:{'X-CSRF-Token':this.csrf}});await this.showBookmarks();}),'icon');remove.setAttribute('aria-label','Удалить закладку');row.append(open,remove);list.append(row);if(bookmark.availability!=='available')list.append(note(bookmark.availability==='hidden_in_slice'?'Объект скрыт в текущем срезе':bookmark.availability==='provider_unavailable'?'Источник закладки недоступен':'Объект сейчас не найден'));}}
  private async openBookmark(bookmark:Bookmark):Promise<void>{if(!this.objectSourceReady(bookmark.provider_id,bookmark.availability))return;this.hidePanel('bookmarks');if(bookmark.selector&&!await this.changeSliceContext(contextForSelector(this.currentContext(),bookmark.selector)))return;if(bookmark.object_kind==='node')await this.openNode(bookmark.object_id);else{const topic=this.topics?.topics.find(item=>item.id===bookmark.object_id);if(topic)this.openTopic(topic);else this.notice('Тема недоступна в выбранной версии.');}}
  private stopLive():void{
    this.stream?.close();this.stream=null;
    clearTimeout(this.streamDeadline);this.streamDeadline=undefined;
    this.liveFallback.stop();
  }
  private startManifestPolling():void{
    this.setConnection('Проверяем обновления графа…',false);
    this.liveFallback.start(async signal=>{
      const current=this.manifest, selector=this.selector;
      if(!this.authenticated||this.changingContext||!current||selector.mode==='known_then')return;
      const reply=await response(apiUrl('/graph',{mode:selector.mode,at:selector.at,history:selector.history?1:0,
        undated:selector.undated,session_id:selector.session_id,run_id:selector.run_id,output_snapshot_id:selector.output_snapshot_id}),signal);
      if(signal.aborted||!this.authenticated||document.hidden||this.changingContext||
         this.manifest!==current||this.selector!==selector)return;
      if(reply.status===202){this.setConnection('Граф готовит новый срез · идёт проверка',false);return;}
      const latest=manifestSchema.parse(await reply.json());
      if(signal.aborted||!this.authenticated||document.hidden||this.changingContext||
         this.manifest!==current||this.selector!==selector)return;
      if(latest.provider_id!==current.provider_id||
         (latest.selector.session_id??null)!==(selector.session_id??null)||
         (latest.selector.run_id??null)!==(selector.run_id??null)||
         (selector.output_snapshot_id!=null&&latest.session_filter?.overview_history?.snapshot_id!==selector.output_snapshot_id))
        throw new Error('Источник или выбранный срез изменился');
      this.setConnection(`Проверка обновлений каждые ${POLL_INTERVAL_MS/1000} с`,true);
      byId('live-dot',HTMLElement).dataset.transport='poll';
      if(latest.revision!==current.revision)await this.loadGraph();
    },error=>{
      if(error instanceof ApiError&&(error.status===401||error.status===403)){this.showLogin();return;}
      this.setConnection(`Обновления недоступны · данные от ${formatTime(this.manifest?.captured_at??null,'instant')}`,false);
    });
  }
  private startLive():void{if(this.changingContext)return;this.stopLive();if(!this.manifest||!this.capabilities)return;if(this.capabilities.provider_id!==this.manifest.provider_id){this.setConnection('Источник изменился · показан предыдущий срез',false);return;}const policy=sourceTransport(this.capabilities.capabilities.live);if(!policy.subscribe){this.setConnection(`${policy.label} · получен ${formatTime(this.manifest.captured_at,'instant')}`,true);const dot=byId('live-dot',HTMLElement);dot.dataset.transport='snapshot';dot.style.background='#8da6c9';dot.style.boxShadow='none';return;}if(this.pollOnly||typeof EventSource==='undefined'){if(this.selector.mode!=='known_then')this.startManifestPolling();else this.setConnection('Срез закреплён',true);return;}const stream=new EventSource(apiUrl('/events',{after:this.cursor,revision:this.manifest.revision}));this.stream=stream;const fallback=():void=>{if(this.stream!==stream)return;this.stopLive();this.pollOnly=true;if(this.selector.mode!=='known_then')this.startManifestPolling();else this.setConnection('Срез закреплён',true);};const arm=(ms:number):void=>{clearTimeout(this.streamDeadline);this.streamDeadline=window.setTimeout(fallback,ms);};arm(SSE_START_MS);const handle=(action:()=>void):void=>{if(this.stream===stream&&!this.changingContext){arm(SSE_IDLE_MS);this.event(action);}};stream.addEventListener('hello',event=>handle(()=>{const hello=parsedEvent(event as MessageEvent<string>,helloSchema);lifecycle('sse-hello',{revision:opaqueTag(hello.revision)});this.cursor=hello.cursor;this.setConnection(this.selector.mode==='known_then'?'Срез закреплён · связь есть':'Live · подключено',true);}));stream.addEventListener('heartbeat',event=>handle(()=>{const heartbeat=parsedEvent(event as MessageEvent<string>,availabilitySchema);this.setConnection(heartbeat.source_available?(this.selector.mode==='known_then'?'Срез закреплён · связь есть':'Live · обновляется'):'Источник недоступен',heartbeat.source_available);}));stream.addEventListener('unavailable',event=>handle(()=>{parsedEvent(event as MessageEvent<string>,availabilitySchema);this.setConnection(`Источник недоступен · данные от ${formatTime(this.manifest?.captured_at??null,'instant')}`,false);}));stream.addEventListener('delta',event=>handle(()=>{const delta=parsedEvent(event as MessageEvent<string>,deltaSchema);lifecycle('sse-delta',{revision:opaqueTag(delta.revision),nodes:delta.upsert_nodes.length,edges:delta.upsert_edges.length,status:delta.status_changes.length});this.liveQueue=this.liveQueue.then(async()=>{if(this.stream!==stream||this.changingContext)return;await this.applyDelta(delta);}).catch(error=>{if(this.stream!==stream||this.changingContext)return;this.notice(humanError(error));this.run(()=>this.loadGraph());});}));stream.addEventListener('reset',event=>handle(()=>{const reset=parsedEvent(event as MessageEvent<string>,resetSchema);lifecycle('sse-reset',{revision:opaqueTag(reset.revision),reason:['history_changed','large_batch','cursor_expired','schema_changed'].includes(reset.reason)?reset.reason:'other'});this.cursor=reset.cursor;if(this.selector.mode!=='known_then')this.run(()=>this.loadGraph());}));stream.onerror=fallback;}
  private event(action:()=>void):void{try{action();}catch(error){this.notice(humanError(error));this.run(()=>this.loadGraph());}}
  private async applyDelta(delta:Delta):Promise<void>{
    if(this.changingContext)return;const transport=this.stream;const manifest=this.manifest;const worker=this.worker;const acceptedGraph=this.graph;
    if(!manifest||!worker||!acceptedGraph)return;if(this.selector.mode==='known_then'){this.cursor=delta.cursor;return;}
    const membership=delta.topic_changes.length&&this.topics?applyMembershipPatch(delta,manifest,this.topics):null;if(!canApplyDelta(manifest.revision,delta,this.selector.mode)||(delta.topic_changes.length&&!membership)){await this.loadGraph();return;}
    const nextWorker=new GraphWorker();const graphRequest=this.graphRequest;let prefetch:LivePinStatusPrefetch|undefined;let consumedPrefetch=false;
    const current=():boolean=>this.authenticated&&!this.changingContext&&this.stream===transport&&this.manifest===manifest&&this.worker===worker&&this.graphRequest===graphRequest&&this.capabilities?.provider_id===manifest.provider_id;
    try{
      const updatedTopics=membership?.topics??(this.topics?{...this.topics,revision:delta.revision}:null);if(!updatedTopics){nextWorker.dispose();await this.loadGraph();return;}
      const graph=await nextWorker.loadPatchedGraph(acceptedGraph,delta,this.selector.history,updatedTopics,delta.revision);if(!current()){nextWorker.dispose();return;}
      const supplied=membership?.manifest??checkedDeltaManifest(delta,manifest);if(delta.manifest&&!supplied){nextWorker.dispose();await this.loadGraph();return;}const updated:Manifest=supplied??{...manifest,revision:delta.revision,source_revision:delta.source_revision,captured_at:delta.captured_at,live_cursor:delta.cursor,counts:{...manifest.counts,nodes:graph.flags.length,edges:graph.edgeActive.length,visible_nodes:graph.visible.reduce((sum,value)=>sum+value,0),visible_edges:graph.visibleEdges.length/2}};
      if(updated.counts.nodes!==graph.flags.length||updated.counts.edges!==graph.edgeActive.length||updated.counts.visible_nodes!==graph.visible.reduce((n,v)=>n+v,0)||updated.counts.visible_edges!==graph.visibleEdges.length/2){nextWorker.dispose();await this.loadGraph();return;}
      const id=this.selectedNodeId,pin=this.reader.pinnedAvailability,cardRequest=this.cardRequest,context=this.acceptedContext,contextTicket=this.contextTicket,selector=this.selector;
      if(id&&pin&&this.reader.pinnedNodeId===id&&!cardRequest?.signal.aborted){
        const request=new AbortController(),abort=():void=>request.abort(),signals=[cardRequest?.signal,graphRequest?.signal].filter((value):value is AbortSignal=>value!==undefined);
        for(const signal of signals){if(signal.aborted)abort();else signal.addEventListener('abort',abort,{once:true});}
        const result:Promise<PinnedStatusResult>=get(apiUrl(`/nodes/${encodeURIComponent(id)}`,{revision:updated.revision}),nodeSchema,request.signal).then(card=>({ok:true as const,card}),error=>({ok:false as const,error}));
        prefetch={id,pin,cardRequest,result,current:()=>!request.signal.aborted&&this.authenticated&&!this.changingContext&&this.stream===transport&&this.graphRequest===graphRequest&&this.cardRequest===cardRequest&&this.acceptedContext===context&&this.contextTicket===contextTicket&&this.selector===selector&&this.manifest===updated&&this.worker===nextWorker&&this.capabilities?.provider_id===updated.provider_id,cancel:()=>{abort();for(const signal of signals)signal.removeEventListener('abort',abort);}};
      }
      const reboundOrdinal=await this.presentGraph(graph,updated,updatedTopics,nextWorker,false,current,undefined,true);
      if(!current()){nextWorker.dispose();return;}
      this.worker=nextWorker;worker.dispose();this.graph=graph;this.manifest=updated;this.topics=updatedTopics;this.cursor=delta.cursor;this.selectedOrdinal=reboundOrdinal??null;this.updateReaderConnections();
      for(const change of delta.status_changes)this.reader.updateStatus(change.id,change.status,delta.revision);if(this.selectedTopicId&&membership?.changedTopicIds.includes(this.selectedTopicId))this.reader.markTopicContextChanged(this.selectedTopicId);this.updateCounts();if(this.selectedNodeId){
        if(prefetch&&prefetch.id===this.selectedNodeId&&prefetch.pin===this.reader.pinnedAvailability&&prefetch.cardRequest===this.cardRequest&&prefetch.current()){consumedPrefetch=true;const acceptedPrefetch=prefetch;this.run(()=>this.refreshSelectedStatus(acceptedPrefetch));}
        else{prefetch?.cancel();this.run(()=>this.refreshSelectedStatus());}
      }
      this.setConnection('Live · обновлено',true);
    }catch(error){nextWorker.dispose();if(!current()||(error instanceof DOMException&&error.name==='AbortError'))return;throw error;}finally{if(!consumedPrefetch)prefetch?.cancel();}
  }
  private setConnection(text:string,online:boolean):void{byId('connection-status',HTMLElement).textContent=text;const dot=byId('live-dot',HTMLElement);dot.classList.toggle('offline',!online);dot.dataset.transport='network';dot.style.background='';dot.style.boxShadow='';}
  private feedback(x:number,y:number):void{const feedback=byId('feedback',HTMLElement);clearTimeout(this.feedbackTimer);feedback.hidden=true;feedback.style.left=`${x}px`;feedback.style.top=`${y}px`;requestAnimationFrame(()=>{feedback.hidden=false;this.feedbackTimer=window.setTimeout(()=>{feedback.hidden=true;},700);});}
  private notice(message:string):void{
    if(!message)return;const panel=[this.historyPanel,this.bookmarksPanel,this.searchPanel].find(value=>!value.hidden);const globalNotice=byId('notice',HTMLElement);
    if(panel){globalNotice.hidden=true;let feedback=panel.querySelector<HTMLElement>('[data-panel-feedback]');if(!feedback){feedback=element('p','','note');feedback.dataset.panelFeedback='true';feedback.setAttribute('role','status');feedback.setAttribute('aria-live','polite');panel.prepend(feedback);}feedback.textContent=message;feedback.hidden=false;panel.scrollTop=0;return;}
    byId('notice-text',HTMLElement).textContent=message;globalNotice.hidden=false;
  }
  private hideSearch():void{this.searchPanel.querySelector('[data-panel-feedback]')?.remove();this.searchRequest?.abort();this.searchRequest=null;this.searchPanel.hidden=true;this.searchPage=null;this.scene?.highlightSearch([]);}
  private togglePanel(kind:'history'|'bookmarks'):void{const panel=kind==='history'?this.historyPanel:this.bookmarksPanel;const show=panel.hidden;panel.querySelector('[data-panel-feedback]')?.remove();this.hideSearch();byId('notice',HTMLElement).hidden=true;panel.style.zIndex=TRANSIENT_PANEL_LAYER;this.hidePanel(kind==='history'?'bookmarks':'history');panel.hidden=!show;byId(`${kind}-toggle`,HTMLButtonElement).setAttribute('aria-expanded',String(show));}
  private hidePanel(kind:'history'|'bookmarks'):void{const panel=kind==='history'?this.historyPanel:this.bookmarksPanel;panel.querySelector('[data-panel-feedback]')?.remove();panel.hidden=true;byId(`${kind}-toggle`,HTMLButtonElement).setAttribute('aria-expanded','false');}
  private updateHistoryCopy():void{const mode=this.modeInput.value;this.dateInput.disabled=mode==='current';this.zoneInput.disabled=mode==='current';byId('undated-label',HTMLElement).hidden=mode!=='known_now';byId('history-explanation',HTMLElement).textContent=mode==='known_then'?'Только знания, доступные к этой дате, включая уже известные планы и сведения без даты события. Поздние уточнения не подмешиваются.':mode==='known_now'?'Последние знания о выбранной дате. Сведения без даты события доступны отдельным срезом.':'Всё, что известно сейчас, включая планы и сведения без даты события. Дата события не ограничивает этот режим.';}
  private readUrl(query=new URLSearchParams(location.search)):SliceContext{
    const zone=query.get('tz')==='UTC'?'UTC':'Europe/Moscow';let day=query.get('day')||new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());const rawMode=query.get('mode');const mode:Mode=rawMode==='known_then'||rawMode==='known_now'?rawMode:'current';const rawUndated=query.get('undated');const undated:Undated=rawUndated==='include'||rawUndated==='only'?rawUndated:'separate';let selector:Selector;
    try{selector={...temporalSelector(mode,day,zone,query.get('history')==='1',undated),session_id:query.get('session_id')||null,run_id:query.get('run_id')||null,output_snapshot_id:query.has('output_snapshot_id')?Number(query.get('output_snapshot_id')):null};const at=query.get('at');if(mode!=='current'&&at&&Number.isFinite(Date.parse(at))){selector={...selector,at:new Date(at).toISOString()};if(!query.get('day'))day=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date(at));}}
    catch{selector={mode:'current',at:null,history:false,undated:'separate'};}
    return{selector,day,zone};
  }
  private updateUrl(kind?:'node'|'edge'|'topic',id?:string):void{const url=new URL(location.href);url.search='';if(kind&&id){url.searchParams.set('kind',kind);url.searchParams.set('id',id);if(this.manifest)url.searchParams.set('provider_id',this.manifest.provider_id);}url.searchParams.set('mode',this.selector.mode);if(this.selector.mode!=='current'){url.searchParams.set('at',this.selector.at??'');url.searchParams.set('day',this.currentContext().day);url.searchParams.set('tz',this.currentContext().zone);url.searchParams.set('undated',this.selector.undated);}if(this.selector.history)url.searchParams.set('history','1');if(this.selector.session_id)url.searchParams.set('session_id',this.selector.session_id);if(this.selector.run_id)url.searchParams.set('run_id',this.selector.run_id);if(this.selector.output_snapshot_id!==null&&this.selector.output_snapshot_id!==undefined)url.searchParams.set('output_snapshot_id',String(this.selector.output_snapshot_id));history.replaceState(null,'',url);}
  private async openDeepLink(query=new URLSearchParams(location.search)):Promise<void>{const kind=query.get('kind');const id=query.get('id');if(!id||id.length>2000||!this.objectSourceReady(query.get('provider_id')))return;if(kind==='node')await this.openNode(id);else if(kind==='edge')await this.openEdge(id);else if(kind==='topic'){const topic=this.topics?.topics.find(item=>item.id===id);if(topic)this.openTopic(topic);else this.notice('Тема недоступна в этой версии.');}}
  private async logout():Promise<void>{clearTimeout(this.capabilityTimer);await response('/session',undefined,{method:'DELETE',headers:{'X-CSRF-Token':this.csrf}});this.graphRequest?.abort();this.cardRequest?.abort();this.searchRequest?.abort();this.stopLive();this.worker?.dispose();this.worker=null;this.sessionFilter?.clear();this.scene?.dispose();this.scene=null;this.graph=null;this.manifest=null;this.topics=null;this.capabilities=null;this.searchPage=null;this.selectedTopicId=null;this.selectedOrdinal=null;this.selectedNodeId=null;this.reader.clear();this.searchPanel.hidden=true;this.historyPanel.hidden=true;this.bookmarksPanel.hidden=true;byId('bookmarks-list',HTMLElement).replaceChildren();byId('search-list',HTMLElement).replaceChildren();byId('search',HTMLInputElement).value='';byId('undated-count',HTMLElement).textContent='';byId('notice-text',HTMLElement).textContent='';byId('notice',HTMLElement).hidden=true;byId('fixture-label',HTMLElement).hidden=true;this.counts.textContent='Вся доступная вселенная';this.setConnection('Требуется вход',false);this.csrf='';this.showLogin();}
}
new Viewer();

