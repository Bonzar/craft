import { AdditiveBlending, BufferAttribute, BufferGeometry, Color, DataTexture, FloatType, InstancedBufferAttribute, InstancedBufferGeometry, LineBasicMaterial, LineSegments, NearestFilter, NoBlending, NormalBlending, Matrix4, PerspectiveCamera, Points, RGBAFormat, Scene, ShaderMaterial, Vector3, WebGLRenderer, WebGLRenderTarget } from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import type { GraphData } from './binary.ts';
import { apiUrl, get, ApiError } from './api.ts';
import { nodeSchema } from './contracts.ts';
import type { NodeCard } from './contracts.ts';
import { previewFromCard } from './preview-cache.ts';
import { reboundNodeOrdinal, visualStageMatches } from './presentation-stage.ts';
import { sourceHierarchy } from './logic.ts';
import { createMapTap, moveMapTap, mapTapIsCurrent, groupIncidentRepresentatives } from './interaction.ts';
import type { MapTap, MapGestureStamp } from './interaction.ts';
import { pickShell, readingPolicy, OccupiedRects } from './ux-policy.ts';
import { lifecycle, opaqueTag } from './lifecycle.ts';
import { captionBox, captionHasNativeOverflow } from './caption-layout.ts';
import { ScreenMetricQueue } from './screen-queue.ts';
import type { ScreenConnectivityResult } from './viewport-connections.ts';
import { NearText } from './near-text.ts';
import { Shells } from './shells.ts';
import { nearPreviewActive, viewBindingMatches } from './focus.ts';
import type { NearRequest, ViewBinding } from './focus.ts';
import type { EnvelopeRequest } from './envelopes.ts';
import type { PreviewEntry } from './preview-cache.ts';
import { LodController } from './lod-controller.ts';
import type { LodResult } from './lod.ts';
import type { GraphWorker } from './worker-client.ts';
import type { Manifest, Topic, Topics } from './contracts.ts';
const STAR_MIN_CSS_PX=12;
const STAR_MAX_CSS_PX=20;
const SPARSE_EDGE_LIMIT=200;
const SPARSE_EDGE_OPACITY=.72;
const DENSE_EDGE_MIN_OPACITY=.012;
const SELECTED_EDGE_OPACITY_BOOST=.16;
function edgeOpacity(drawnLines:number):number{return Math.max(DENSE_EDGE_MIN_OPACITY,SPARSE_EDGE_OPACITY*Math.min(1,Math.sqrt(SPARSE_EDGE_LIMIT/Math.max(1,drawnLines))));}
const starVertex=`attribute vec3 tint; attribute float visible; attribute float ordinal; attribute float previousDegree; attribute float degree; varying float vGlow; varying vec3 vTint; varying float vOrdinal; uniform float scale; uniform float dpr; uniform float height; uniform float picking; uniform float minSize; uniform float maxSize; uniform float metricMix; void main(){vec4 p=modelViewMatrix*vec4(position,1.);gl_Position=visible<.5?vec4(2.,2.,2.,1.):projectionMatrix*p;float level=mix(previousDegree,degree,metricMix);float s=mix(minSize,maxSize,level);vGlow=.82+.18*level;gl_PointSize=visible*(picking>0.5?max(12.,s):s)*dpr;vTint=tint;vOrdinal=ordinal;}`;
const starFragment=`varying float vGlow;varying vec3 vTint; varying float vOrdinal; uniform float picking; vec3 encode(float n){return vec3(mod(n,256.),mod(floor(n/256.),256.),floor(n/65536.))/255.;}void main(){float r=length(gl_PointCoord-vec2(.5));if(r>.5)discard;if(picking>.5){gl_FragColor=vec4(encode(vOrdinal+1.),1.);return;}float glow=exp(-r*r*12.);float core=1.-smoothstep(.23,.34,r);gl_FragColor=vec4(vTint*(.85+core*.65)*vGlow,glow*.62+core*.38);}`;
const proxyVertex=`attribute vec3 tint;attribute float ordinal;attribute float markerSize;attribute float previousDegree;attribute float degree;uniform float metricMix;varying float vGlow;varying vec3 vTint;varying float vOrdinal;uniform float dpr;void main(){gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.);float level=mix(previousDegree,degree,metricMix);gl_PointSize=(24.+12.*level)*dpr;vGlow=.82+.18*level;vTint=tint;vOrdinal=ordinal;}`;
const proxyFragment=`varying float vGlow;varying vec3 vTint;varying float vOrdinal;uniform float picking;void main(){float r=length(gl_PointCoord-.5);if(r>.5)discard;if(picking>.5){float n=vOrdinal+1.;gl_FragColor=vec4(vec3(mod(n,256.),mod(floor(n/256.),256.),floor(n/65536.))/255.,1.);return;}float ring=exp(-pow((r-.34)*32.,2.));float core=exp(-r*r*38.)*.24;gl_FragColor=vec4(vTint*vGlow,ring*.85+core);}`;
const pickEdgeVertex=`attribute float edgeFrom;attribute float edgeTo;attribute float edgeID;uniform sampler2D coordinates;uniform float textureWidth;uniform float textureHeight;varying float vID;vec3 at(float n){return texture2D(coordinates,(vec2(mod(n,textureWidth),floor(n/textureWidth))+.5)/vec2(textureWidth,textureHeight)).xyz;}void main(){float n=position.x>.5?edgeTo:edgeFrom;gl_Position=projectionMatrix*modelViewMatrix*vec4(at(n),1.);vID=edgeID;}`;
const pickEdgeFragment=`varying float vID;void main(){gl_FragColor=vec4(vec3(mod(vID,256.),mod(floor(vID/256.),256.),floor(vID/65536.))/255.,1.);}`;
export type Pick = ({kind:'node'|'edge';ordinal:number}|{kind:'topic_proxy';topicId:string;topicIndex:number}|{kind:'edge_bundle';bundleIndex:number}|{kind:'topic_shell';topicId:string;topicIndex:number}|{kind:'shell_choices';topicIds:string[]})&{generation:number;revision:string};
export interface Projection {x:number;y:number;visible:boolean}
interface Flight {start:number;duration:number;from:Vector3;to:Vector3;targetFrom:Vector3;targetTo:Vector3}
interface Label {topic:Topic|null;element:HTMLButtonElement}
type CaptionAction={kind:'topic';topicId:string}|{kind:'pick';pick:Pick};
interface MapPress extends MapTap {pointerId:number;button:HTMLButtonElement|null;action:CaptionAction|null}
const PALETTE=['#9eb8ff','#a6d0ce','#c3b5ed','#d8c393','#9abce5','#b8c99b'];

export class UniverseScene {
  readonly renderer:WebGLRenderer;
  readonly camera=new PerspectiveCamera(48,1,.01,1000000);
  readonly controls:OrbitControls;
  private scene=new Scene(); private pickScene=new Scene(); private edgePickScene=new Scene();
  private metrics:(ScreenConnectivityResult&{view_projection:Float32Array;viewport:readonly[number,number]})|null=null;private metricAt=0;private metricSignature='';private metricCamera=0;private metricApplied=-1;private metricStarted=0;private metricQueue:ScreenMetricQueue;
  private starDegree:BufferAttribute|null=null;private previousStarDegree:BufferAttribute|null=null;private proxyDegree:BufferAttribute|null=null;private previousProxyDegree:BufferAttribute|null=null;private metricStarLabels:{position:[number,number,number];text:string;pick:Pick|null}[]=[];private onMetrics:(metrics:ScreenConnectivityResult)=>void;
  private traceAt=0;private cameraTag='';private selectedTopicId:string|null=null;private envelopeSlots=new Map<number,number>();private occupancy=new OccupiedRects();private placedLabels=0;private labelSignature='';private near:NearText;private shells:Shells;private viewWorker:GraphWorker|null=null;private topicByIndex=new Map<number,Topic>();private proxyByTopic=new Map<number,number>();private visibleTopicIndices:Uint32Array=new Uint32Array();
  private viewMatrix=new Matrix4();private viewAt=0;private viewSignature='';private cameraGeneration=0;private viewExpected:ViewBinding|null=null;private viewRunning=false;private viewPending:{near:NearRequest;envelopes:EnvelopeRequest&{label_topic_indices:Uint32Array;selected_ordinals:Uint32Array};show:boolean;blocked:readonly (readonly[number,number,number,number])[];worker:GraphWorker}|null=null;
  private nearActive=false;private focal:[number,number]|null=null;private explicitOrdinal:number|null=null;private focusTopic:number|null=null;private pointerPositions=new Map<number,[number,number]>();private queryError:(error:unknown)=>void;
  private controller:LodController;private lod:LodResult|null=null;private graphToken=0;private bindingPending=false;private cutAt=0;private overviewDistance=0;private searchNodes:number[]=[];private selectedIndices:Uint32Array|null=null;private visibilityAttribute:BufferAttribute|null=null;
  private proxyPoints:Points<BufferGeometry,ShaderMaterial>|null=null;private pickProxies:Points<BufferGeometry,ShaderMaterial>|null=null;private proxyLabels:{index:number;element:HTMLButtonElement}[]=[];
  private graph:GraphData|null=null;private manifest:Manifest|null=null;private topics:Topics|null=null;
  private points:Points<BufferGeometry,ShaderMaterial>|null=null;
  private lines:LineSegments<BufferGeometry,LineBasicMaterial>|null=null;
  private selected:LineSegments<BufferGeometry,LineBasicMaterial>|null=null;
  private found:Points<BufferGeometry,ShaderMaterial>|null=null;
  private pickPoints:Points<BufferGeometry,ShaderMaterial>|null=null;
  private edgePicker:LineSegments<InstancedBufferGeometry,ShaderMaterial>|null=null;
  private coordinateTexture:DataTexture|null=null;
  private pickTarget=new WebGLRenderTarget(13,13,{depthBuffer:true});
  private labels:Label[]=[]; private labelsAt=0;private frame=0;private frameTimes:number[]=[];
  private radius=100;private overview=true;private flight:Flight|null=null;private disposed=false;
  private inputEvents=new AbortController();private resizeObserver:ResizeObserver;private pointers=new Set<number>();private surface:HTMLElement;private activePointers=new Set<number>();private readingPointers=new Set<number>();private mapEndEvents=new WeakSet<Event>();private mapPress:MapPress|null=null;private pendingTap:MapPress|null=null;private keyboardPress:{button:HTMLButtonElement;action:CaptionAction;tap:MapTap;key:string}|null=null;private captionActions=new WeakMap<HTMLButtonElement,()=>CaptionAction|null>();private reconnectingControls=false;private countLabels:{position:[number,number,number];text:string;pick:Pick|null}[]=[];private countLabelPool:{element:HTMLButtonElement;index:number}[]=[];
  private onProjection:(lod:LodResult)=>void;private onPick:(picked:Pick|null)=>void;private onTopic:(topic:Topic)=>void;private feedback:(x:number,y:number)=>void;
  constructor(canvas:HTMLCanvasElement,labels:HTMLElement,onPick:(picked:Pick|null)=>void,onTopic:(topic:Topic)=>void,feedback:(x:number,y:number)=>void,onError:(reason:unknown)=>void,onProjection:(lod:LodResult)=>void,onReadPreview:(entry:PreviewEntry)=>void,onMetrics:(metrics:ScreenConnectivityResult)=>void){
    this.onMetrics=onMetrics;this.metricQueue=new ScreenMetricQueue(result=>{if(!this.bindingPending&&this.lod?.revision===result.revision&&this.lod.generation===result.lod_generation)this.applyConnectivity(result);},onError);this.queryError=onError;this.near=new NearText(labels,ordinal=>this.projectNode(ordinal),entry=>{if(entry.revision===this.manifest?.revision&&entry.provider_id===this.manifest.provider_id)onReadPreview(entry);},onError,ordinal=>{const metrics=this.metrics;if(!metrics||!metrics.onscreen[ordinal])return null;const self=metrics.canonical_self[ordinal]??0;return `${(metrics.external_visible[ordinal]??0).toLocaleString('ru')} связей с другими в кадре${self?` · ${self} с собой`: ''}`;});this.shells=new Shells(this.scene);this.onProjection=onProjection;this.controller=new LodController(lod=>this.applyProjection(lod),onError);this.onPick=onPick;this.onTopic=onTopic;this.feedback=feedback;this.labelRoot=labels;
    lifecycle('renderer-create');this.renderer=new WebGLRenderer({canvas,antialias:false,alpha:true,powerPreference:'high-performance'});
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio,window.innerWidth<700?1.35:1.6));
    this.renderer.setClearColor(0x030611,0);
    const surface=canvas.closest<HTMLElement>('#map-surface');if(!surface||!surface.contains(labels))throw new Error('Область карты недоступна.');this.surface=surface;this.bindMapInput();
    this.controls=new OrbitControls(this.camera,surface);this.surface.style.touchAction='auto';this.controls.enableDamping=true;this.controls.dampingFactor=.085;this.controls.rotateSpeed=.5;this.controls.zoomSpeed=.8;this.controls.zoomToCursor=true;this.controls.screenSpacePanning=true;
    this.controls.addEventListener('start',()=>{this.flight=null;});
    canvas.addEventListener('keydown',event=>this.keyboard(event),{signal:this.inputEvents.signal});
    canvas.addEventListener('webglcontextlost',()=>lifecycle('webgl-context-lost'),{signal:this.inputEvents.signal});canvas.addEventListener('webglcontextrestored',()=>lifecycle('webgl-context-restored'),{signal:this.inputEvents.signal});
    this.resizeObserver=new ResizeObserver(()=>this.resize());this.resizeObserver.observe(canvas);this.resize();this.render(0);
  }
  private mapStamp():MapGestureStamp|null{const manifest=this.manifest,lod=this.lod;return manifest&&lod?{provider_id:manifest.provider_id,revision:lod.revision,generation:lod.generation}:null;}
  private cancelMapTaps():void{if(this.mapPress)this.mapPress.cancelled=true;if(this.pendingTap)this.pendingTap.cancelled=true;this.pendingTap=null;if(this.keyboardPress)this.keyboardPress.tap.cancelled=true;this.keyboardPress=null;}
  private captionButton(target:EventTarget|null):HTMLButtonElement|null{const button=target instanceof Element?target.closest('button'):null;return button instanceof HTMLButtonElement&&this.captionActions.has(button)?button:null;}
  private readingTarget(target:EventTarget|null):boolean{return target instanceof Element&&target.closest('.near-preview,.caption-scroll')!==null;}
  private captionKey(action:CaptionAction):string{return JSON.stringify(action);}
  private captionStillMatches(button:HTMLButtonElement,action:CaptionAction):boolean{const current=this.captionActions.get(button)?.();return button.isConnected&&!button.hidden&&current!==undefined&&current!==null&&this.captionKey(current)===this.captionKey(action);}
  private activateCaption(button:HTMLButtonElement,action:CaptionAction,tap:MapTap):void{
    const stamp=this.mapStamp();if(!stamp||!mapTapIsCurrent(tap,stamp)||!this.captionStillMatches(button,action))return;
    if(action.kind==='pick')this.onPick(action.pick);else{const topic=this.topics?.topics.find(value=>value.id===action.topicId);if(topic)this.onTopic(topic);}
  }
  private bindCaption(button:HTMLButtonElement,action:()=>CaptionAction|null):void{
    button.type='button';this.captionActions.set(button,action);
    button.addEventListener('keydown',event=>{if(event.key!=='Enter'&&event.key!==' ')return;event.preventDefault();if(event.repeat)return;const selected=action(),stamp=this.mapStamp();this.keyboardPress=selected&&stamp?{button,action:selected,tap:createMapTap(stamp,0,0),key:event.key}:null;});
    button.addEventListener('keyup',event=>{if(event.key!=='Enter'&&event.key!==' ')return;event.preventDefault();const pressed=this.keyboardPress;this.keyboardPress=null;if(pressed?.button===button&&pressed.key===event.key)this.activateCaption(button,pressed.action,pressed.tap);});
    button.addEventListener('blur',()=>{if(this.keyboardPress?.button===button)this.keyboardPress=null;});
    button.addEventListener('click',event=>{if(event.detail!==0||this.pendingTap?.button===button||this.mapPress?.button===button||this.keyboardPress?.button===button){event.preventDefault();return;}const selected=action(),stamp=this.mapStamp();if(selected&&stamp)this.activateCaption(button,selected,createMapTap(stamp,0,0));});
  }
  private startMapGesture():void{this.overview=false;this.flight=null;this.explicitOrdinal=null;this.viewSignature='';}
  private resetMapControls():void{
    this.cancelMapTaps();this.pointers.clear();this.readingPointers.clear();this.activePointers.clear();this.pointerPositions.clear();this.mapPress=null;
    if(this.reconnectingControls||this.disposed)return;this.reconnectingControls=true;this.controls.disconnect();
    queueMicrotask(()=>{this.reconnectingControls=false;if(this.disposed)return;this.controls.connect(this.surface);this.surface.style.touchAction='auto';});
  }
  private finishMapPointer(event:PointerEvent):void{
    const map=this.pointers.has(event.pointerId),reading=this.readingPointers.has(event.pointerId);if(!map&&!reading)return;
    if(map)this.mapEndEvents.add(event);this.pointers.delete(event.pointerId);this.readingPointers.delete(event.pointerId);this.pointerPositions.delete(event.pointerId);
    const pressed=this.mapPress;if(!pressed||pressed.pointerId!==event.pointerId)return;this.mapPress=null;moveMapTap(pressed,event.clientX,event.clientY);
    const stamp=this.mapStamp();if(this.activePointers.size||!stamp||!mapTapIsCurrent(pressed,stamp))return;
    this.pendingTap=pressed;this.feedback(event.clientX,event.clientY);
    requestAnimationFrame(()=>{setTimeout(()=>{const current=this.mapStamp();if(this.pendingTap!==pressed)return;this.pendingTap=null;if(this.disposed||!current||!mapTapIsCurrent(pressed,current))return;if(pressed.button&&pressed.action)this.activateCaption(pressed.button,pressed.action,pressed);else if(!pressed.button)this.onPick(this.pick(event.clientX,event.clientY));},0);});
  }
  private bindMapInput():void{
    const surface=this.surface,signal=this.inputEvents.signal;
    // Bubble gate runs before OrbitControls. Target button handlers and native defaults remain intact.
    surface.addEventListener('pointerdown',event=>{
      const reading=this.readingTarget(event.target),button=this.captionButton(event.target),action=button?this.captionActions.get(button)?.()??null:null,stamp=this.mapStamp();
      if(reading)this.readingPointers.add(event.pointerId);else{this.pointers.add(event.pointerId);this.pointerPositions.set(event.pointerId,[event.clientX,event.clientY]);}
      if(this.activePointers.size===1&&stamp&&(event.button===0||event.pointerType==='touch')&&(!reading||action))this.mapPress={...createMapTap(stamp,event.clientX,event.clientY),pointerId:event.pointerId,button,action};else this.cancelMapTaps();
      if(!reading&&this.pointers.size>1)this.startMapGesture();if(reading)event.stopImmediatePropagation();
    },{signal});
    surface.addEventListener('wheel',event=>{if(this.readingTarget(event.target)){event.stopImmediatePropagation();return;}this.startMapGesture();const rect=this.renderer.domElement.getBoundingClientRect();this.focal=[event.clientX-rect.left,event.clientY-rect.top];},{passive:true,signal});
    for(const type of ['pointermove','pointerup','pointercancel'] as const)surface.addEventListener(type,event=>{if(this.pointers.has(event.pointerId)||this.mapEndEvents.has(event))return;if(this.readingTarget(event.target)||this.readingPointers.has(event.pointerId))event.stopImmediatePropagation();},{signal});
    surface.addEventListener('contextmenu',event=>{if(this.readingTarget(event.target))event.stopImmediatePropagation();},{signal});
    surface.addEventListener('scroll',()=>this.cancelMapTaps(),{capture:true,passive:true,signal});
    document.addEventListener('pointerdown',event=>{this.activePointers.add(event.pointerId);if(this.pendingTap){this.pendingTap.cancelled=true;this.pendingTap=null;}if(this.activePointers.size>1)this.cancelMapTaps();},{capture:true,passive:true,signal});
    document.addEventListener('pointermove',event=>{
      if(this.mapPress?.pointerId===event.pointerId){moveMapTap(this.mapPress,event.clientX,event.clientY);if(this.pointers.has(event.pointerId)&&this.mapPress.maxDistance>6)this.startMapGesture();}
      if(!this.pointerPositions.has(event.pointerId))return;this.pointerPositions.set(event.pointerId,[event.clientX,event.clientY]);
      if(this.pointerPositions.size>1){let x=0,y=0;for(const point of this.pointerPositions.values()){x+=point[0];y+=point[1];}const rect=this.renderer.domElement.getBoundingClientRect();this.focal=[x/this.pointerPositions.size-rect.left,y/this.pointerPositions.size-rect.top];}
    },{capture:true,passive:true,signal});
    document.addEventListener('pointerup',event=>{this.activePointers.delete(event.pointerId);this.finishMapPointer(event);},{capture:true,signal});
    document.addEventListener('pointercancel',event=>{this.activePointers.delete(event.pointerId);if(this.pointers.has(event.pointerId))this.resetMapControls();else{this.readingPointers.delete(event.pointerId);this.cancelMapTaps();if(this.mapPress?.pointerId===event.pointerId)this.mapPress=null;}},{capture:true,signal});
    document.addEventListener('lostpointercapture',event=>{if(this.pointers.has(event.pointerId))this.resetMapControls();},{capture:true,signal});
    // OrbitControls uses document listeners for an uncaptured second touch. Unowned UI pointers never reach those listeners.
    for(const type of ['pointermove','pointerup'] as const)document.addEventListener(type,event=>{if(this.pointers.size&&!this.pointers.has(event.pointerId)&&!this.mapEndEvents.has(event))event.stopImmediatePropagation();},{signal});
    document.addEventListener('wheel',()=>this.cancelMapTaps(),{capture:true,passive:true,signal});
    window.addEventListener('blur',()=>{if(this.pointers.size)this.resetMapControls();else{this.cancelMapTaps();this.activePointers.clear();this.readingPointers.clear();}},{signal});
  }
  private labelRoot:HTMLElement;
  private resize():void {const rect=this.renderer.domElement.getBoundingClientRect();this.renderer.setSize(rect.width,rect.height,false);this.camera.aspect=rect.width/Math.max(1,rect.height);this.camera.updateProjectionMatrix();if(this.manifest&&this.overview)this.fitOverview(false);const height=this.points?.material.uniforms.height;if(height)height.value=rect.height;}
  async setGraph(graph:GraphData,manifest:Manifest,topics:Topics,worker:GraphWorker,resetCamera=false,_preserveForces=false,identities:{selectedNodeId?:string|null;searchNodeIds?:readonly string[];signal?:AbortSignal;isCurrent?:()=>boolean}={}):Promise<number|null>{
    const token=++this.graphToken;this.cancelMapTaps();this.bindingPending=true;this.metricQueue.bind('',-1);this.viewExpected=null;this.viewPending=null;this.near.pauseFetches();lifecycle('set-graph-begin',{revision:opaqueTag(manifest.revision)});
    // Keep the accepted picture intact while fresh identities and view data are prepared.
    const sameSource=this.manifest?.provider_id===manifest.provider_id;
    const preview=sameSource?this.near.focusedEntry:null;
    const nextController=sameSource?this.controller.fork():new LodController(value=>this.applyProjection(value),this.queryError);
    try{
    const rebound=async(id:string):Promise<NodeCard|null>=>{try{const card=await get(apiUrl(`/nodes/${encodeURIComponent(id)}`,{revision:manifest.revision,offset:0,limit:480}),nodeSchema,identities.signal);return reboundNodeOrdinal({provider_id:manifest.provider_id,revision:manifest.revision,id},manifest.provider_id,card)!==null?card:null;}catch(error){if(error instanceof ApiError&&error.status===404)return null;throw error;}};
    const ids=sameSource?Array.from(new Set([identities.selectedNodeId,preview?.id,...(identities.searchNodeIds??[])].filter((id):id is string=>Boolean(id)))):[];
    const cards=await Promise.all(ids.map(rebound));const byId=new Map(cards.filter(card=>card!==null).map(card=>[card.id,card]));
    const selectedOrdinal=identities.selectedNodeId?byId.get(identities.selectedNodeId)?.ordinal??null:null;
    const explicitOrdinal=sameSource&&this.explicitOrdinal!==null?(selectedOrdinal??(preview?byId.get(preview.id)?.ordinal??null:null)):null;
    const searchOrdinals=(identities.searchNodeIds??[]).flatMap(id=>{const card=byId.get(id);return card?[card.ordinal]:[];});
    nextController.setForces(selectedOrdinal!==null?[selectedOrdinal]:explicitOrdinal!==null?[explicitOrdinal]:[],searchOrdinals);
    const lod=await nextController.bind(manifest,topics,worker,true);
    this.camera.updateMatrixWorld();const matrix=new Float32Array(new Matrix4().multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse).elements);
    const rect=this.renderer.domElement.getBoundingClientRect();const viewport:[number,number]=[rect.width,rect.height];const position:[number,number,number]=[this.camera.position.x,this.camera.position.y,this.camera.position.z];const blocked=this.blockedRects();const focus=this.focal??[rect.width/2,rect.height/2];
    const binding:ViewBinding={revision:manifest.revision,lod_generation:lod.generation,camera_generation:++this.cameraGeneration};
    const ratio=this.camera.position.distanceTo(this.controls.target)/Math.max(1,this.overviewDistance);const show=nearPreviewActive(this.nearActive,ratio,explicitOrdinal!==null);const expanded=nextController.expandedTopicIndices;const focusTopic=this.selectedTopicId?topics.topics.find(topic=>topic.id===this.selectedTopicId)?.index:null;
    const [metrics,envelopes,near,incident]=await Promise.all([
      worker.screen({...binding,view_projection:matrix,viewport}),
      worker.envelopeView({...binding,view_projection:matrix,camera_position:position,viewport,eligible_topic_indices:expanded,label_topic_indices:new Uint32Array(new Set([...expanded,...lod.collapsed_topic_indices])),selected_ordinals:nextController.forcedOrdinals,priority_topic_indices:focusTopic!==undefined&&focusTopic!==null?new Uint32Array([focusTopic]):new Uint32Array(),limit:rect.width<700?24:48}),
      worker.near({...binding,view_projection:matrix,camera_position:position,camera_target:[this.controls.target.x,this.controls.target.y,this.controls.target.z],focus_screen:focus,viewport,activation:{near:ratio<.60||explicitOrdinal!==null,...(explicitOrdinal!==null?{explicit_ordinal:explicitOrdinal}:{})},limit:12,blocked_rects:blocked}),
      selectedOrdinal!==null?worker.incident(selectedOrdinal):Promise.resolve(null)
    ]);
    const previews=await Promise.all(near.candidates.slice(0,1).map(async candidate=>{const card=await get(apiUrl(`/graph/${encodeURIComponent(manifest.revision)}/nodes/${candidate.ordinal}`,{offset:0,limit:480}),nodeSchema,identities.signal);if(card.revision!==manifest.revision||card.ordinal!==candidate.ordinal)throw new Error('Источник вернул текст другой звезды');return previewFromCard(card,manifest.provider_id,manifest.revision);}));
    identities.signal?.throwIfAborted();if(token!==this.graphToken||identities.isCurrent?.()===false){lifecycle('set-graph-rejected');throw new DOMException('Aborted','AbortError');}
    if(!visualStageMatches({revision:manifest.revision,generation:lod.generation},{lod,metrics,envelopes})||worker.envelopes?.revision!==manifest.revision)throw new Error('Подготовленная карта относится к другой версии');
    this.controller.dispose();this.controller=nextController;this.explicitOrdinal=explicitOrdinal;this.searchNodes=searchOrdinals;this.selectedIndices=incident;
    this.clearGeometry();
    this.graph=graph;this.manifest=manifest;this.topics=topics;this.viewWorker=worker;this.viewExpected=null;this.viewPending=null;this.viewSignature='';this.topicByIndex=new Map(topics.topics.map(topic=>[topic.index,topic]));this.near.setContext(manifest);this.shells.setBounds(worker.envelopes);this.envelopeSlots=new Map(worker.envelopes?Array.from(worker.envelopes.indices,(index,slot)=>[index,slot]):[]);this.setSelectedTopic(this.selectedTopicId);
    this.radius=Math.max(1,new Vector3(...manifest.bounds.max).distanceTo(new Vector3(...manifest.bounds.min))*.5);
    this.camera.near=Math.max(.001,this.radius/100000);this.camera.far=this.radius*1000;this.camera.updateProjectionMatrix();this.controls.minDistance=this.radius*.001;this.controls.maxDistance=this.radius*100;
    const geometry=new BufferGeometry();geometry.setAttribute('position',new BufferAttribute(graph.positions,3));this.visibilityAttribute=new BufferAttribute(new Float32Array(lod.node_render_visible),1);geometry.setAttribute('visible',this.visibilityAttribute);
    const ordinals=new Float32Array(graph.flags.length);for(let i=0;i<ordinals.length;i++)ordinals[i]=i;
    geometry.setAttribute('ordinal',new BufferAttribute(ordinals,1));geometry.setAttribute('tint',new BufferAttribute(this.topicColors(graph.flags.length,topics),3));this.starDegree=new BufferAttribute(new Float32Array(graph.flags.length),1);this.previousStarDegree=new BufferAttribute(new Float32Array(graph.flags.length),1);geometry.setAttribute('degree',this.starDegree);geometry.setAttribute('previousDegree',this.previousStarDegree);geometry.computeBoundingSphere();
    const uniforms={scale:{value:this.radius*.012},dpr:{value:this.renderer.getPixelRatio()},height:{value:this.renderer.domElement.clientHeight},picking:{value:0},metricMix:{value:1},minSize:{value:STAR_MIN_CSS_PX},maxSize:{value:STAR_MAX_CSS_PX}};
    const material=new ShaderMaterial({vertexShader:starVertex,fragmentShader:starFragment,uniforms,transparent:true,depthWrite:false,blending:NormalBlending});
    this.points=new Points(geometry,material);this.scene.add(this.points);
    const pickMaterial=material.clone();pickMaterial.uniforms.picking={value:1};pickMaterial.uniforms.dpr={value:1};pickMaterial.blending=NoBlending;pickMaterial.transparent=false;pickMaterial.depthWrite=true;pickMaterial.toneMapped=false;
    this.pickPoints=new Points(geometry,pickMaterial);this.pickScene.add(this.pickPoints);
    this.setLabels(topics);this.applyProjection(lod);this.metricCamera=Math.max(this.metricCamera,binding.camera_generation);this.applyConnectivity(metrics,true);this.near.prime(previews);this.viewExpected=binding;this.near.expect(binding);this.nearActive=show;this.near.setFrame(near,near.candidates,show,blocked);this.shells.show(envelopes.shell_indices);this.shells.settle();this.visibleTopicIndices=envelopes.label_indices;this.labelsAt=0;this.updateLabels(performance.now());
    lifecycle('set-graph-accepted',{revision:opaqueTag(manifest.revision)});this.controller.activate();this.bindingPending=false;this.near.resumeFetches();
    if(resetCamera)this.reset();return selectedOrdinal;
    }catch(error){nextController.dispose();if(token===this.graphToken){this.bindingPending=false;if(this.lod)this.metricQueue.bind(this.lod.revision,this.lod.generation);this.near.resumeFetches();}throw error;}
  }
  private applyProjection(lod:LodResult):void{
    if(!this.graph||!this.manifest||lod.revision!==this.manifest.revision)return;
    this.cancelMapTaps();lifecycle('projection-accepted',{revision:opaqueTag(lod.revision),generation:lod.generation,canonical:lod.coverage.canonical_nodes,proxies:lod.proxies.length,lines:lod.render_edges.length/2});lifecycle('degree-reset',{generation:lod.generation});this.lod=lod;this.metrics=null;this.metricApplied=-1;this.metricSignature='';this.metricQueue.bind(lod.revision,lod.generation);this.metricStarLabels=[];for(const attribute of [this.starDegree,this.previousStarDegree]){if(attribute){attribute.array.fill(0);attribute.needsUpdate=true;}}this.proxyByTopic=new Map(lod.proxies.map((proxy,index)=>[proxy.topic_index,index]));this.near.invalidate();this.viewExpected=null;this.viewSignature='';if(this.visibilityAttribute){this.visibilityAttribute.array.set(lod.node_render_visible);this.visibilityAttribute.needsUpdate=true;}
    for(const object of [this.lines,this.selected,this.proxyPoints,this.edgePicker]){if(!object)continue;object.removeFromParent();object.geometry.dispose();object.material.dispose();}
    this.lines=null;this.selected=null;this.proxyPoints=null;this.proxyDegree=null;this.previousProxyDegree=null;this.edgePicker=null;
    if(this.pickProxies){this.pickProxies.removeFromParent();this.pickProxies.material.dispose();this.pickProxies=null;}
    this.coordinateTexture?.dispose();this.coordinateTexture=null;
    const positions=lod.proxies.length?new BufferAttribute(lod.render_positions,3):this.points?.geometry.getAttribute('position');
    if(!positions)return;
    const lineGeometry=new BufferGeometry();lineGeometry.setAttribute('position',positions);lineGeometry.setIndex(new BufferAttribute(lod.render_edges,1));
    this.lines=new LineSegments(lineGeometry,new LineBasicMaterial({color:0x9cbfe5,transparent:true,opacity:edgeOpacity(lod.render_edges.length/2),depthWrite:false,blending:NormalBlending}));this.lines.frustumCulled=false;this.scene.add(this.lines);
    if(lod.proxies.length){
      const count=lod.proxies.length;const geometry=new BufferGeometry();const points=new Float32Array(count*3);const colors=new Float32Array(count*3);const ids=new Float32Array(count);const sizes=new Float32Array(count);
      for(let i=0;i<count;i++){const proxy=lod.proxies[i];if(!proxy)continue;points.set(proxy.position,i*3);colors.set([.55,.72,1],i*3);ids[i]=this.graph.flags.length+i;sizes[i]=18+Math.min(12,Math.log2(proxy.represented_count+1));}
      geometry.setAttribute('position',new BufferAttribute(points,3));geometry.setAttribute('tint',new BufferAttribute(colors,3));geometry.setAttribute('ordinal',new BufferAttribute(ids,1));geometry.setAttribute('markerSize',new BufferAttribute(sizes,1));this.proxyDegree=new BufferAttribute(new Float32Array(count),1);this.previousProxyDegree=new BufferAttribute(new Float32Array(count),1);geometry.setAttribute('degree',this.proxyDegree);geometry.setAttribute('previousDegree',this.previousProxyDegree);
      const material=new ShaderMaterial({vertexShader:proxyVertex,fragmentShader:proxyFragment,uniforms:{dpr:{value:this.renderer.getPixelRatio()},picking:{value:0},metricMix:{value:1}},transparent:true,depthWrite:false,blending:NormalBlending});
      this.proxyPoints=new Points(geometry,material);this.scene.add(this.proxyPoints);
      const pickMaterial=material.clone();pickMaterial.uniforms.dpr={value:1};pickMaterial.uniforms.picking={value:1};pickMaterial.blending=NoBlending;pickMaterial.transparent=false;pickMaterial.depthWrite=true;pickMaterial.toneMapped=false;this.pickProxies=new Points(geometry,pickMaterial);this.pickScene.add(this.pickProxies);
    }
    if(this.selectedIndices)this.renderIncident(this.selectedIndices);this.renderSearch();if(!this.selectedIndices)this.prepareCountLabels(new Uint32Array(),new Uint32Array());this.labelsAt=0;this.onProjection(lod);
  }
  setSelectedTopic(topicId:string|null):void{this.selectedTopicId=topicId;const topic=topicId?this.topics?.topics.find(value=>value.id===topicId):undefined;this.focusTopic=topic?.index??null;this.shells.setSelected(topic?.index??null);lifecycle('sphere-selection',{selected:topicId!==null,identity:topicId?opaqueTag(topicId):'none'});this.viewSignature='';}
  get projection():LodResult|null{return this.lod;}
  async revealNodes(ordinals:readonly number[]):Promise<void>{await this.controller.reveal(ordinals);}
  clearSelection():void{this.explicitOrdinal=null;this.focal=null;this.focusTopic=null;this.viewSignature='';this.selectedIndices=null;this.controller.clearSelection();this.renderIncident(new Uint32Array());}
  async expandAll():Promise<void>{this.overview=false;await this.controller.expandAll();this.fitOverview(true);}
  expandTopic(topicId:string):void{this.focusTopic=this.topics?.topics.find(topic=>topic.id===topicId)?.index??null;this.overview=false;this.controller.expand(topicId);}
  private updateCut(now:number):void{
    if(now-this.cutAt<125||this.bindingPending||!this.manifest)return;this.cutAt=now;
    const distance=this.camera.position.distanceTo(this.controls.target);
    if(this.overview&&!this.flight&&this.overviewDistance>0&&Math.abs(Math.log(distance/this.overviewDistance))>.09)this.overview=false;
    const height=this.renderer.domElement.clientHeight;this.controller.update(topic=>topic.radius*height/(Math.max(.01,this.camera.position.distanceTo(new Vector3(...topic.position)))*Math.tan(this.camera.fov*Math.PI/360)),this.overview,window.innerWidth<700?72:96);
  }
  private topicColors(count:number,topics:Topics):Float32Array {
    const colors=new Float32Array(count*3);const memberships=new Uint16Array(count);
    const topicByIndex=new Map(topics.topics.map(topic=>[topic.index,topic]));
    for(const membership of topics.memberships){const topic=topicByIndex.get(membership.topic_index);if(!topic)continue;let hash=0;for(const c of topic.id)hash=(hash*31+c.charCodeAt(0))>>>0;const color=new Color(PALETTE[hash%PALETTE.length]??'#b5c6ef');for(const index of membership.node_indices){if(index>=count)throw new Error('Тема ссылается на неизвестную звезду');colors[index*3]=(colors[index*3]??0)+color.r;colors[index*3+1]=(colors[index*3+1]??0)+color.g;colors[index*3+2]=(colors[index*3+2]??0)+color.b;memberships[index]=(memberships[index]??0)+1;}}
    const neutral=new Color('#adb9d7');for(let i=0;i<count;i++){const weight=memberships[i]??0;for(let c=0;c<3;c++)colors[i*3+c]=weight?(colors[i*3+c]??0)/weight:([neutral.r,neutral.g,neutral.b][c]??.6);}
    return colors;
  }
  private setLabels(topics:Topics):void {
    for(const label of this.labels)label.element.remove();this.labels=[];for(const item of this.proxyLabels)item.element.remove();this.proxyLabels=[];for(const item of this.countLabelPool)item.element.remove();this.countLabelPool=[];for(let i=0;i<30;i++){const item:{element:HTMLButtonElement;index:number}={element:document.createElement('button'),index:i};item.element.className='edge-count-label';item.element.hidden=true;this.bindCaption(item.element,()=>{const pick=this.countLabels[item.index]?.pick;return pick?{kind:'pick',pick}:null;});this.labelRoot.append(item.element);this.countLabelPool.push(item);}
    for(let i=0;i<Math.min(32,topics.topics.length);i++){const item:{index:number;element:HTMLButtonElement}={index:0,element:document.createElement('button')};item.element.className='proxy-label';item.element.hidden=true;this.bindCaption(item.element,()=>{const pick=this.proxyPick(item.index);return pick?{kind:'pick',pick}:null;});this.labelRoot.append(item.element);this.proxyLabels.push(item);}
    for(let index=0;index<Math.min(28,topics.topics.length);index++){
      const item:Label={topic:null,element:document.createElement('button')};item.element.className='topic-label';item.element.hidden=true;
      this.bindCaption(item.element,()=>item.topic?{kind:'topic',topicId:item.topic.id}:null);this.labelRoot.append(item.element);this.labels.push(item);
    }
  }
  private proxyPick(index:number):Pick|null{const proxy=this.lod?.proxies[index];if(!proxy||!this.lod)return null;return{kind:'topic_proxy',topicId:proxy.topic_id,topicIndex:proxy.topic_index,generation:this.lod.generation,revision:this.lod.revision};}
  private updateProxyLabels():void{
    const rect=this.renderer.domElement.getBoundingClientRect();const proxies=this.lod?.proxies??[];
    for(const item of this.proxyLabels)item.element.hidden=true;
    let shown=0;const occupied:{x:number;y:number;height:number}[]=[];
    const policy=readingPolicy(!document.getElementById('reader')?.hidden,rect.width);const candidates=Array.from(this.visibleTopicIndices).filter(index=>this.proxyByTopic.has(index)).toSorted((a,b)=>{const ai=this.proxyByTopic.get(a)??0,bi=this.proxyByTopic.get(b)??0;const offset=this.graph?.flags.length??0;const activity=(index:number):number=>(this.metrics?.external_visible[offset+index]??0)+(this.metrics?.proxy_internal[offset+index]??0);return (b===this.focusTopic?1:0)-(a===this.focusTopic?1:0)||activity(bi)-activity(ai)||(proxies[bi]?.represented_count??0)-(proxies[ai]?.represented_count??0);});
    for(const topicIndex of candidates){
      if(this.placedLabels>=policy.compact_labels)break;const i=this.proxyByTopic.get(topicIndex);if(i===undefined)continue;const proxy=proxies[i];if(!proxy)continue;const at=this.project(proxy.position);if(!at.visible||at.x<15||at.x>rect.width-15||at.y<160||at.y>rect.height-65||occupied.some(item=>Math.abs(item.x-at.x)<165&&Math.abs(item.y-at.y)<Math.max(78,item.height)))continue;
      const label=this.proxyLabels[shown];if(!label)break;const topic=this.topicByIndex.get(proxy.topic_index);label.index=i;const representative=(this.graph?.flags.length??0)+i;const external=this.metrics?.external_visible[representative];const internal=this.metrics?.proxy_internal[representative]??proxy.internal_edge_count;const diameter=(topic?.radius??0)*rect.height/(Math.max(.01,this.camera.position.distanceTo(new Vector3(...proxy.position)))*Math.tan(this.camera.fov*Math.PI/360));const full=topic?.index===this.focusTopic||(!this.overview&&diameter>110);this.setCaption(label.element,topic?.label??'Тема',`${proxy.represented_count.toLocaleString('ru')} утверждений`,false);label.element.setAttribute('aria-label',`Созвездие ${topic?.full_title??'из источника'}: ${proxy.represented_count} уникальных утверждений`);label.element.hidden=false;if(!this.positionCaption(label.element,at.x,at.y+22,false)){label.element.hidden=true;continue;}shown++;this.placedLabels++;occupied.push({x:at.x,y:at.y,height:Math.min(220,label.element.offsetHeight||80)});
    }
  }
  private positionCaption(target:HTMLButtonElement,anchorX:number,anchorY:number,centerY:boolean):boolean{
    const rect=this.renderer.domElement.getBoundingClientRect();const scrollTop=target.scrollTop;const title=target.querySelector<HTMLElement>('.proxy-title,.topic-name');const titleScroll=title?.scrollTop??0;target.style.width='';target.style.maxHeight='';target.style.overflowY='';const width=target.offsetWidth||250;const height=target.offsetHeight||80;
    const top=rect.width<700?Math.max(205,(document.querySelector('.toolbar')?.getBoundingClientRect().bottom??190)+10):110;
    const box=captionBox(anchorX-width/2,anchorY-(centerY?height/2:0),width,height,rect.width,rect.height,top,120);
    const above=captionBox(anchorX-width/2,anchorY-height-22,width,height,rect.width,rect.height,top,120);const right=captionBox(anchorX+22,anchorY-height/2,width,height,rect.width,rect.height,top,120);const chosen=this.occupancy.place([[box.x,box.y,box.x+box.width,box.y+box.height],[above.x,above.y,above.x+above.width,above.y+above.height],[right.x,right.y,right.x+right.width,right.y+right.height]]);if(!chosen)return false;target.style.width=`${chosen[2]-chosen[0]}px`;target.style.maxHeight=`${chosen[3]-chosen[1]}px`;target.style.overflowY='auto';target.style.transform=`translate(${chosen[0]}px,${chosen[1]}px)`;target.scrollTop=scrollTop;if(title)title.scrollTop=titleScroll;const scrollable=[target,...target.querySelectorAll<HTMLElement>('.proxy-title,.topic-name')].some(node=>{const style=getComputedStyle(node);return captionHasNativeOverflow({scrollHeight:node.scrollHeight,clientHeight:node.clientHeight,scrollWidth:node.scrollWidth,clientWidth:node.clientWidth,overflowY:style.overflowY,overflowX:style.overflowX});});target.classList.toggle('caption-scroll',scrollable);return true;
  }
  private setCaption(target:HTMLButtonElement,nameText:string,countsText:string|null,full:boolean):void{
    let name=target.querySelector<HTMLElement>('.topic-name,.proxy-title');if(!name){name=document.createElement('span');name.className=countsText===null?'topic-name':'proxy-title';target.replaceChildren(name);}if(name.textContent!==nameText)name.textContent=nameText;
    let counts=target.querySelector<HTMLElement>('.proxy-counts');if(countsText!==null){if(!counts){counts=document.createElement('span');counts.className='proxy-counts';target.append(counts);}if(counts.textContent!==countsText)counts.textContent=countsText;}else counts?.remove();target.classList.toggle('full-title',full);
  }
  private updateLabels(now:number):void{
    if(now-this.labelsAt<100)return;this.labelsAt=now;this.occupancy=new OccupiedRects([...this.blockedRects(),...this.near.occupiedRects]);this.placedLabels=0;this.updateProxyLabels();this.updateCountLabels();for(const label of this.labels)label.element.hidden=true;
    const rect=this.renderer.domElement.getBoundingClientRect();const occupied:{x:number;y:number;width:number}[]=[];const policy=readingPolicy(!document.getElementById('reader')?.hidden,rect.width);let shown=0;
    for(const index of this.visibleTopicIndices){if(this.placedLabels>=policy.compact_labels)break;const topic=this.topicByIndex.get(index);if(!topic||this.proxyByTopic.has(index))continue;const point=this.project(topic.position);if(!point.visible||point.y<(rect.width<700?205:110)||point.y>rect.height-80)continue;const distance=this.camera.position.distanceTo(new Vector3(...topic.position));const extent=topic.radius*rect.height/(Math.max(.01,distance)*Math.tan(this.camera.fov*Math.PI/360));if(extent<18)continue;const width=Math.min(240,topic.label.length*7+36);if(occupied.some(other=>Math.abs(point.x-other.x)<(width+other.width)/2&&Math.abs(point.y-other.y)<42))continue;const label=this.labels[shown];if(!label)break;label.topic=topic;const full=topic.index===this.focusTopic||(!this.overview&&extent>110);this.setCaption(label.element,topic.label,null,false);label.element.title=topic.full_title;label.element.setAttribute('aria-label',`Тема: ${topic.full_title}`);label.element.hidden=false;if(!this.positionCaption(label.element,point.x,point.y,true)){label.element.hidden=true;continue;}shown++;this.placedLabels++;label.element.style.opacity='0.9';occupied.push({...point,width});if(this.placedLabels>=policy.compact_labels)break;}const signature=`${this.placedLabels}:${this.near.diagnostics().visible}`;if(signature!==this.labelSignature){this.labelSignature=signature;lifecycle('label-layout',{compact:this.placedLabels,near:Number(this.near.diagnostics().visible)});}
  }
  private blockedRects():readonly (readonly[number,number,number,number])[]{
    const result:[number,number,number,number][]=[];for(const node of document.querySelectorAll<HTMLElement>('.topbar,.toolbar,.reader:not([hidden]),.floating-panel:not([hidden]),#notice:not([hidden])')){const rect=node.getBoundingClientRect();if(rect.width&&rect.height)result.push([rect.left,rect.top,rect.right,rect.bottom]);}return result;
  }
  private updateView(now:number):void{
    const worker=this.viewWorker,lod=this.lod,manifest=this.manifest;if(!worker||!lod||!manifest||this.bindingPending||now-this.viewAt<125)return;this.viewAt=now;
    this.viewMatrix.multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse);const rect=this.renderer.domElement.getBoundingClientRect();const focus=this.focal??[rect.width/2,rect.height/2];const blocked=this.blockedRects();const signature=`${this.viewMatrix.elements.map(value=>value.toFixed(5)).join(',')}|${lod.generation}|${focus.join(',')}|${blocked.map(value=>value.join(',')).join(';')}|${this.explicitOrdinal}`;if(signature===this.viewSignature)return;this.viewSignature=signature;
    const binding:ViewBinding={revision:manifest.revision,lod_generation:lod.generation,camera_generation:++this.cameraGeneration};this.viewExpected=binding;this.near.expect(binding);
    const ratio=this.camera.position.distanceTo(this.controls.target)/Math.max(1,this.overviewDistance);this.nearActive=nearPreviewActive(this.nearActive,ratio,this.explicitOrdinal!==null);
    const expanded=this.controller.expandedTopicIndices;const labelTopics=new Uint32Array(new Set([...expanded,...lod.collapsed_topic_indices]));const matrix=new Float32Array(this.viewMatrix.elements);const position:[number,number,number]=[this.camera.position.x,this.camera.position.y,this.camera.position.z];const viewport:[number,number]=[rect.width,rect.height];
    this.viewPending={worker,show:this.nearActive,blocked,near:{...binding,view_projection:matrix,camera_position:position,camera_target:[this.controls.target.x,this.controls.target.y,this.controls.target.z],focus_screen:focus,viewport,activation:{near:ratio<.60||this.explicitOrdinal!==null,...(this.explicitOrdinal!==null?{explicit_ordinal:this.explicitOrdinal}:{})},limit:12,blocked_rects:blocked},envelopes:{...binding,view_projection:matrix,camera_position:position,viewport,eligible_topic_indices:expanded,label_topic_indices:labelTopics,selected_ordinals:this.controller.forcedOrdinals,priority_topic_indices:this.focusTopic!==null?new Uint32Array([this.focusTopic]):new Uint32Array(),limit:rect.width<700?24:48}};this.pumpView();
  }
  private pumpView():void{
    if(this.viewRunning||!this.viewPending)return;const task=this.viewPending;this.viewPending=null;this.viewRunning=true;
    Promise.all([task.worker.near(task.near),task.worker.envelopeView(task.envelopes)]).then(([near,envelopes])=>{
      if(this.viewWorker!==task.worker||!this.viewExpected||!viewBindingMatches(this.viewExpected,near)||!viewBindingMatches(this.viewExpected,envelopes)||this.bindingPending)return;
      this.near.setFrame(near,near.candidates,task.show,task.blocked);this.shells.show(envelopes.shell_indices);this.visibleTopicIndices=envelopes.label_indices;this.labelsAt=0;
    }).catch(error=>{if(this.viewExpected&&viewBindingMatches(this.viewExpected,task.near)&&!this.bindingPending)this.queryError(error);}).finally(()=>{this.viewRunning=false;if(this.viewPending)this.pumpView();});
  }
  private updateConnectivity(now:number):void{
    const lod=this.lod,worker=this.viewWorker;if(!lod||!worker||this.bindingPending||now-this.metricAt<250)return;this.metricAt=now;this.viewMatrix.multiplyMatrices(this.camera.projectionMatrix,this.camera.matrixWorldInverse);const signature=`${lod.generation}|${this.viewMatrix.elements.map(value=>value.toFixed(4)).join(',')}`;if(signature===this.metricSignature)return;this.metricSignature=signature;
    this.metricQueue.request(request=>worker.screen(request),{revision:lod.revision,lod_generation:lod.generation,camera_generation:++this.metricCamera,view_projection:new Float32Array(this.viewMatrix.elements),viewport:[this.renderer.domElement.clientWidth,this.renderer.domElement.clientHeight]});
  }
  private applyConnectivity(result:ScreenConnectivityResult&{view_projection:Float32Array;viewport:readonly[number,number]},immediate=false):void{
    const nodes=this.graph?.flags.length??0;const mix=Math.min(1,(performance.now()-this.metricStarted)/200);
    const update=(previous:BufferAttribute|null,current:BufferAttribute|null,next:Float32Array):void=>{if(!previous||!current)return;for(let i=0;i<current.count;i++)previous.setX(i,previous.getX(i)*(1-mix)+current.getX(i)*mix);current.array.set(next);previous.needsUpdate=true;current.needsUpdate=true;};
    update(this.previousStarDegree,this.starDegree,result.star_intensity.subarray(0,nodes));update(this.previousProxyDegree,this.proxyDegree,result.proxy_intensity.subarray(nodes));if(immediate){for(const [previous,current] of [[this.previousStarDegree,this.starDegree],[this.previousProxyDegree,this.proxyDegree]])if(previous&&current){previous.array.set(current.array);previous.needsUpdate=true;}}this.metricStarted=performance.now()-(immediate?200:0);this.metrics=result;lifecycle('metrics-accepted',{generation:result.lod_generation,camera:result.camera_generation});this.metricApplied=result.camera_generation;
    this.metricStarLabels=[];
    const indices=this.selectedIndices;if(indices&&this.lod){const groups=groupIncidentRepresentatives(indices,this.lod.representative_by_node);this.prepareCountLabels(groups.endpoints,groups.multiplicities);}else this.prepareCountLabels(new Uint32Array(),new Uint32Array());this.labelsAt=0;this.near.refreshMetrics();this.onMetrics(result);
  }
  private updateMetricTween(now:number):void{const mix=Math.max(0,Math.min(1,(now-this.metricStarted)/200));for(const object of [this.points,this.pickPoints,this.proxyPoints,this.pickProxies]){const uniform=object?.material.uniforms.metricMix;if(uniform)uniform.value=mix;}}
  get screenConnectivity():ScreenConnectivityResult|null{return this.metrics;}
  screenConnectivityAuditData():Record<string,unknown>|null{const result=this.metrics;if(!result)return null;return{revision:result.revision,lod_generation:result.lod_generation,camera_generation:result.camera_generation,view_projection:result.view_projection.slice(),viewport:[...result.viewport],onscreen:result.onscreen.slice(),external_visible:result.external_visible.slice(),canonical_self:result.canonical_self.slice(),proxy_internal:result.proxy_internal.slice(),star_intensity:result.star_intensity.slice(),proxy_intensity:result.proxy_intensity.slice(),unique_external_edges:result.unique_external_edges,stats:{...result.stats},diagnostics:{...result.diagnostics}};}
  private clearGeometry():void {lifecycle('geometry-clear');
    for(const object of [this.points,this.lines,this.selected,this.found,this.edgePicker,this.proxyPoints]){if(!object)continue;object.removeFromParent();object.geometry.dispose();object.material.dispose();}
    if(this.pickPoints){this.pickPoints.removeFromParent();this.pickPoints.material.dispose();}if(this.pickProxies){this.pickProxies.removeFromParent();this.pickProxies.material.dispose();this.pickProxies=null;}
    this.coordinateTexture?.dispose();this.coordinateTexture=null;this.edgePicker=null;this.points=null;this.lines=null;this.selected=null;this.found=null;this.pickPoints=null;this.proxyPoints=null;
  }
  highlightIncident(indices:Uint32Array):void{this.selectedIndices=indices;this.renderIncident(indices);}
  private renderIncident(indices:Uint32Array):void{
    if(this.selected){this.selected.removeFromParent();this.selected.geometry.dispose();this.selected.material.dispose();this.selected=null;}if(!this.lod||!this.lines||!indices.length){this.prepareCountLabels(new Uint32Array(),new Uint32Array());return;}
    const grouped=groupIncidentRepresentatives(indices,this.lod.representative_by_node);const endpoints=grouped.endpoints;this.prepareCountLabels(grouped.endpoints,grouped.multiplicities);
    const geometry=new BufferGeometry();geometry.setAttribute('position',this.lines.geometry.getAttribute('position'));geometry.setIndex(new BufferAttribute(endpoints,1));this.selected=new LineSegments(geometry,new LineBasicMaterial({color:0xe0edff,transparent:true,opacity:Math.min(.96,Math.max(edgeOpacity(this.lod.render_edges.length/2)+SELECTED_EDGE_OPACITY_BOOST,.72/Math.sqrt(Math.max(1,grouped.multiplicities.length/100)))),depthWrite:false,blending:NormalBlending}));this.selected.frustumCulled=false;this.scene.add(this.selected);
  }
  private prepareCountLabels(_selectedEndpoints:Uint32Array,_selectedCounts:Uint32Array):void{this.countLabels=[];for(const item of this.countLabelPool)item.element.hidden=true;}
  private updateCountLabels():void{for(const item of this.countLabelPool)item.element.hidden=true;}
  highlightSearch(ordinals:readonly number[]):void{this.searchNodes=[...ordinals];this.controller.setSearch(ordinals);this.renderSearch();}
  private renderSearch():void{
    if(this.found){this.found.removeFromParent();this.found.geometry.dispose();this.found.material.dispose();this.found=null;}if(!this.graph||!this.lod)return;
    const ordinals=this.searchNodes.filter(ordinal=>Boolean(this.lod?.node_render_visible[ordinal]));if(!ordinals.length)return;
    const geometry=new BufferGeometry();const positions=new Float32Array(ordinals.length*3);const colors=new Float32Array(ordinals.length*3);for(let i=0;i<ordinals.length;i++){const ordinal=ordinals[i]??0;positions.set(this.graph.positions.subarray(ordinal*3,ordinal*3+3),i*3);colors.set([1,.83,.5],i*3);}geometry.setAttribute('position',new BufferAttribute(positions,3));geometry.setAttribute('tint',new BufferAttribute(colors,3));geometry.setAttribute('visible',new BufferAttribute(new Float32Array(ordinals.length).fill(1),1));geometry.setAttribute('ordinal',new BufferAttribute(new Float32Array(ordinals.length),1));geometry.setAttribute('degree',new BufferAttribute(new Float32Array(ordinals.length).fill(1),1));geometry.setAttribute('previousDegree',new BufferAttribute(new Float32Array(ordinals.length).fill(1),1));const material=new ShaderMaterial({vertexShader:starVertex,fragmentShader:starFragment,uniforms:{scale:{value:this.radius*.04},dpr:{value:this.renderer.getPixelRatio()},height:{value:this.renderer.domElement.clientHeight},picking:{value:0},metricMix:{value:1},minSize:{value:15},maxSize:{value:20}},transparent:true,depthWrite:false,blending:AdditiveBlending});this.found=new Points(geometry,material);this.scene.add(this.found);
  }
  reset():void {this.explicitOrdinal=null;this.focal=null;this.focusTopic=null;this.nearActive=false;this.near.invalidate();this.overview=true;this.controller.reset();this.fitOverview(true);}
  private fitOverview(animate:boolean):void {if(!this.manifest)return;const center=new Vector3(...this.manifest.bounds.min).add(new Vector3(...this.manifest.bounds.max)).multiplyScalar(.5);const vertical=this.camera.fov*Math.PI/360;const limiting=Math.atan(Math.tan(vertical)*Math.min(1,this.camera.aspect));const distance=this.radius/Math.sin(limiting)*1.08;this.overviewDistance=distance;if(animate)this.fly(center,distance);else{this.flight=null;const direction=this.camera.position.clone().sub(this.controls.target).normalize();this.camera.position.copy(center).addScaledVector(direction.lengthSq()?direction:new Vector3(.45,.28,1).normalize(),distance);this.controls.target.copy(center);this.controls.update();}}
  flyNode(ordinal:number):void {this.explicitOrdinal=ordinal;this.focal=null;this.viewSignature='';this.overview=false;if(!this.graph||ordinal>=this.graph.flags.length)return;this.fly(new Vector3().fromArray(this.graph.positions,ordinal*3),this.radius*.13);}
  flyTopic(topic:Topic):void {this.overview=false;this.fly(new Vector3(...topic.position),Math.max(this.radius*.035,topic.radius*2.4));}
  private fly(target:Vector3,distance:number):void {const direction=this.camera.position.clone().sub(this.controls.target);if(direction.lengthSq()<1)direction.set(.45,.28,1);direction.normalize();const to=target.clone().addScaledVector(direction,distance);if(window.matchMedia('(prefers-reduced-motion: reduce)').matches){this.camera.position.copy(to);this.controls.target.copy(target);this.controls.update();return;}this.flight={start:performance.now(),duration:850,from:this.camera.position.clone(),to,targetFrom:this.controls.target.clone(),targetTo:target};}
  zoom(factor:number):void {this.overview=false;this.flight=null;this.camera.position.sub(this.controls.target).multiplyScalar(factor).add(this.controls.target);this.controls.update();}
  private keyboard(event:KeyboardEvent):void {if(!['+','=','-','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;this.cancelMapTaps();this.overview=false;if(event.key==='+'||event.key==='='){this.zoom(.8);event.preventDefault();}else if(event.key==='-'){this.zoom(1.25);event.preventDefault();}else if(event.key.startsWith('Arrow')){const delta=new Vector3().copy(this.camera.position).sub(this.controls.target);if(event.key==='ArrowLeft'||event.key==='ArrowRight')delta.applyAxisAngle(new Vector3(0,1,0),event.key==='ArrowLeft'?.12:-.12);else delta.applyAxisAngle(new Vector3(1,0,0),event.key==='ArrowUp'?.12:-.12);this.camera.position.copy(this.controls.target).add(delta);event.preventDefault();}}
  private buildEdgePicker():void {
    if(!this.graph||!this.lod||!this.lod.render_edges.length||this.edgePicker)return;
    const maxSize=Math.min(4096,this.renderer.capabilities.maxTextureSize);const width=Math.min(maxSize,Math.max(1,this.lod.render_positions.length/3));const height=Math.max(1,Math.ceil(this.lod.render_positions.length/3/width));
    const coordinates=new Float32Array(width*height*4);for(let i=0;i<this.lod.render_positions.length/3;i++)coordinates.set(this.lod.render_positions.subarray(i*3,i*3+3),i*4);
    this.coordinateTexture=new DataTexture(coordinates,width,height,RGBAFormat,FloatType);this.coordinateTexture.minFilter=NearestFilter;this.coordinateTexture.magFilter=NearestFilter;this.coordinateTexture.needsUpdate=true;
    const count=this.lod.render_edges.length/2;const from=new Float32Array(count);const to=new Float32Array(count);const ids=new Float32Array(count);
    for(let i=0;i<count;i++){from[i]=this.lod.render_edges[i*2]??0;to[i]=this.lod.render_edges[i*2+1]??0;ids[i]=i+1;}
    const geometry=new InstancedBufferGeometry();geometry.setAttribute('position',new BufferAttribute(new Float32Array([0,0,0,1,0,0]),3));geometry.setAttribute('edgeFrom',new InstancedBufferAttribute(from,1));geometry.setAttribute('edgeTo',new InstancedBufferAttribute(to,1));geometry.setAttribute('edgeID',new InstancedBufferAttribute(ids,1));geometry.instanceCount=count;
    const material=new ShaderMaterial({vertexShader:pickEdgeVertex,fragmentShader:pickEdgeFragment,uniforms:{coordinates:{value:this.coordinateTexture},textureWidth:{value:width},textureHeight:{value:height}},blending:NoBlending,depthTest:true,depthWrite:true,toneMapped:false});
    this.edgePicker=new LineSegments(geometry,material);this.edgePicker.frustumCulled=false;this.edgePickScene.add(this.edgePicker);
  }
  private readPick(scene:Scene):number|null {const bytes=new Uint8Array(13*13*4);this.renderer.render(scene,this.camera);this.renderer.readRenderTargetPixels(this.pickTarget,0,0,13,13,bytes);let best:number|null=null;let distance=Infinity;for(let y=0;y<13;y++)for(let x=0;x<13;x++){const at=(y*13+x)*4;const id=(bytes[at]??0)+256*(bytes[at+1]??0)+65536*(bytes[at+2]??0);const d=(x-6)**2+(y-6)**2;if(id&&d<distance){distance=d;best=id-1;}}return best;}
  private pick(x:number,y:number):Pick|null {
    if(!this.graph||!this.pickPoints||!this.lod||this.bindingPending)return null;
    const rect=this.renderer.domElement.getBoundingClientRect();this.camera.setViewOffset(rect.width,rect.height,x-rect.left-6,y-rect.top-6,13,13);this.renderer.setRenderTarget(this.pickTarget);this.renderer.setClearColor(0,1);this.renderer.clear();
    let ordinal=this.readPick(this.pickScene);let kind:'node'|'edge'='node';
    if(ordinal===null){this.buildEdgePicker();this.renderer.clear();ordinal=this.readPick(this.edgePickScene);kind='edge';}
    this.camera.clearViewOffset();this.renderer.setRenderTarget(null);this.renderer.setClearColor(0x030611,0);
    if(ordinal===null)return this.pickActualShell(x,y);const base={generation:this.lod.generation,revision:this.lod.revision};if(kind==='node'){if(ordinal>=this.graph.flags.length)return this.proxyPick(ordinal-this.graph.flags.length);return{...base,kind:'node',ordinal};}return this.lod.edge_pick_kind[ordinal]===1?{...base,kind:'edge_bundle',bundleIndex:this.lod.edge_pick_target[ordinal]??0}:{...base,kind:'edge',ordinal:this.lod.edge_pick_target[ordinal]??0};
  }
  private pickActualShell(x:number,y:number):Pick|null{
    const bounds=this.viewWorker?.envelopes,lod=this.lod;if(!bounds||!lod)return null;const rect=this.renderer.domElement.getBoundingClientRect();const point:[number,number]=[x-rect.left,y-rect.top];const direction=new Vector3(point[0]/rect.width*2-1,1-point[1]/rect.height*2,.5).unproject(this.camera).sub(this.camera.position).normalize();
    const candidates=[];for(const index of this.shells.indices){const slot=this.envelopeSlots.get(index),topic=this.topicByIndex.get(index);if(slot===undefined||!topic)continue;const center:[number,number,number]=[bounds.centers[slot*3]??0,bounds.centers[slot*3+1]??0,bounds.centers[slot*3+2]??0];const radius=bounds.radii[slot]??0;const distance=this.camera.position.distanceTo(new Vector3(...center));const at=this.project(center);const pixels=rect.height/(2*Math.tan(this.camera.fov*Math.PI/360))*radius/Math.sqrt(Math.max(.000001,distance*distance-radius*radius));candidates.push({topic_id:topic.id,topic_index:index,center,radius,screen:{x:at.x,y:at.y,radius:pixels}});}
    const choice=pickShell(candidates,[this.camera.position.x,this.camera.position.y,this.camera.position.z],[direction.x,direction.y,direction.z],point,Math.min(rect.width,rect.height));if(!choice)return null;const base={revision:lod.revision,generation:lod.generation};return choice.kind==='shell'?{...base,kind:'topic_shell',topicId:choice.topic_id,topicIndex:choice.topic_index}:{...base,kind:'shell_choices',topicIds:choice.topic_ids};
  }
  private project(position:ArrayLike<number>):Projection {const p=new Vector3(position[0]??0,position[1]??0,position[2]??0).project(this.camera);const rect=this.renderer.domElement.getBoundingClientRect();return{x:(p.x*.5+.5)*rect.width,y:(-.5*p.y+.5)*rect.height,visible:p.z>-1&&p.z<1&&Math.abs(p.x)<=1&&Math.abs(p.y)<=1};}
  projectNode(ordinal:number):Projection {if(!this.graph||ordinal>=this.graph.flags.length)return{x:0,y:0,visible:false};const point=this.project(this.graph.positions.subarray(ordinal*3,ordinal*3+3));const rect=this.renderer.domElement.getBoundingClientRect();return{x:point.x+rect.left,y:point.y+rect.top,visible:point.visible&&Boolean(this.lod?.node_render_visible[ordinal])};}
  projectEdge(ordinal:number):Projection {if(!this.graph)return{x:0,y:0,visible:false};const from=this.graph.edges[ordinal*2];const to=this.graph.edges[ordinal*2+1];if(from===undefined||to===undefined)return{x:0,y:0,visible:false};const a=this.graph.positions;const point=this.project([(a[from*3]??0)/2+(a[to*3]??0)/2,(a[from*3+1]??0)/2+(a[to*3+1]??0)/2,(a[from*3+2]??0)/2+(a[to*3+2]??0)/2]);const rect=this.renderer.domElement.getBoundingClientRect();return{x:point.x+rect.left,y:point.y+rect.top,visible:point.visible&&Boolean(this.lod?.node_render_visible[from])&&Boolean(this.lod?.node_render_visible[to])};}
  projectProxy(topicIndex:number):Projection{const proxy=this.lod?.proxies.find(value=>value.topic_index===topicIndex);if(!proxy)return{x:0,y:0,visible:false};const point=this.project(proxy.position);const rect=this.renderer.domElement.getBoundingClientRect();return{x:point.x+rect.left,y:point.y+rect.top,visible:point.visible};}
  /** Canonical reps are 0..N-1; proxy reps are N+proxyIndex; hidden nodes use -1. edge_pick_kind 0=original source edge ordinal, 1=bundle CSR index. Copies cannot mutate the active scene. */
  envelopeAuditData():Record<string,unknown>|null{const bounds=this.viewWorker?.envelopes;if(!bounds||bounds.revision!==this.manifest?.revision)return null;return{revision:bounds.revision,topic_revision:this.topics?.revision??bounds.revision,lod_generation:this.lod?.generation??0,camera_generation:this.viewExpected?.camera_generation??0,topic_indices:bounds.indices.slice(),centers:bounds.centers.slice(),radii:bounds.radii.slice(),direct_visible_counts:bounds.direct_visible_counts.slice(),active_shell_indices:this.shells.indices};}
  lodAuditData():Record<string,unknown>|null{
    const lod=this.lod;const graph=this.graph;if(!lod||!graph)return null;
    const canonical=new Uint32Array(lod.coverage.canonical_nodes);let at=0;for(let ordinal=0;ordinal<lod.node_render_visible.length;ordinal++)if(lod.node_render_visible[ordinal])canonical[at++]=ordinal;
    return Object.freeze({revision:lod.revision,generation:lod.generation,collapsed_topic_indices:lod.collapsed_topic_indices.slice(),force_reveal_ordinals:lod.force_reveal_ordinals.slice(),encoding:{hidden:-1,canonical:'0..node_count-1',proxy:'node_count+proxyIndex',edge_pick_kind:{original:0,bundle:1}},node_count:graph.flags.length,source_flags:graph.flags.slice(),visible_node_flags:graph.visible.slice(),source_edges:graph.edges.slice(),source_edge_active:graph.edgeActive.slice(),visible_source_edge_ordinals:graph.visibleEdgeOrdinals.slice(),representative_by_node:lod.representative_by_node.slice(),node_render_visible:lod.node_render_visible.slice(),canonical_render_ordinals:canonical,proxy_topic_indices:new Uint32Array(lod.proxies.map(proxy=>proxy.topic_index)),proxy_topic_ids:lod.proxies.map(proxy=>proxy.topic_id),proxy_represented_counts:new Uint32Array(lod.proxies.map(proxy=>proxy.represented_count)),proxy_source_member_counts:new Uint32Array(lod.proxies.map(proxy=>proxy.source_member_count)),proxy_visible_source_member_counts:new Uint32Array(lod.proxies.map(proxy=>proxy.visible_source_member_count)),render_edges:lod.render_edges.slice(),edge_pick_kind:lod.edge_pick_kind.slice(),edge_pick_target:lod.edge_pick_target.slice(),original_render_edge_ordinals:lod.edge_pick_target.slice(0,lod.coverage.original_edges),bundle_pairs:lod.bundle_pairs.slice(),bundle_offsets:lod.bundle_offsets.slice(),bundle_edge_ordinals:lod.bundle_edge_ordinals.slice(),proxy_internal_offsets:lod.proxy_internal_offsets.slice(),proxy_internal_edge_ordinals:lod.proxy_internal_edge_ordinals.slice(),coverage:{...lod.coverage}});
  }
  diagnostics():Record<string,unknown> {return {revision:this.manifest?.revision??null,nodeSlots:this.graph?.flags.length??0,edgeSlots:this.graph?.edgeActive.length??0,visibleNodes:this.graph?.visible.reduce((n,v)=>n+v,0)??0,visibleEdges:(this.graph?.visibleEdges.length??0)/2,positionBytes:this.graph?.positions.byteLength??0,indexBytes:this.graph?.visibleEdges.byteLength??0,screenConnectivity:this.metrics?{revision:this.metrics.revision,lod_generation:this.metrics.lod_generation,camera_generation:this.metrics.camera_generation,unique_external_edges:this.metrics.unique_external_edges,stats:{...this.metrics.stats},diagnostics:{...this.metrics.diagnostics}}:null,selectedTopicId:this.selectedTopicId,nearText:this.near.diagnostics(),shellInstances:this.shells.count,lod:this.lod?{generation:this.lod.generation,revision:this.lod.revision,coverage:{...this.lod.coverage},proxyCount:this.lod.proxies.length,bundleCount:this.lod.bundle_offsets.length-1,renderedLineSegments:this.lod.render_edges.length/2,workerRebuilds:this.controller.rebuildCount}:null,drawCalls:this.renderer.info.render.calls,drawnPoints:this.renderer.info.render.points,drawnLines:this.renderer.info.render.lines,renderedFrames:this.frame,frameTimes:[...this.frameTimes],camera:{position:this.camera.position.toArray(),target:this.controls.target.toArray(),perspective:true},canvas:{width:this.renderer.domElement.width,height:this.renderer.domElement.height},dpr:this.renderer.getPixelRatio()};}
  private render=(now:number):void=>{if(this.disposed)return;requestAnimationFrame(this.render);if(this.flight){const t=Math.min(1,(now-this.flight.start)/this.flight.duration);const ease=t*t*(3-2*t);this.camera.position.lerpVectors(this.flight.from,this.flight.to,ease);this.controls.target.lerpVectors(this.flight.targetFrom,this.flight.targetTo,ease);if(t>=1)this.flight=null;}this.controls.update();this.updateMetricTween(now);this.shells.update(this.camera.position);this.renderer.render(this.scene,this.camera);if(this.graph){this.frame++;this.frameTimes.push(now);if(this.frameTimes.length>600)this.frameTimes.shift();this.updateCut(now);this.updateView(now);this.updateConnectivity(now);if(now-this.traceAt>250){this.traceAt=now;const tag=opaqueTag([...this.camera.position.toArray(),...this.controls.target.toArray()].map(value=>value.toFixed(4)).join(','));if(tag!==this.cameraTag){this.cameraTag=tag;lifecycle('camera-change',{hash:tag});}}this.near.setSuppressed(!document.getElementById('reader')?.hidden);this.near.position();this.updateLabels(now);}};
  dispose():void{lifecycle('renderer-dispose');this.disposed=true;this.cancelMapTaps();this.pointers.clear();this.readingPointers.clear();this.activePointers.clear();this.pointerPositions.clear();this.metrics=null;this.metricQueue.dispose();this.viewExpected=null;this.viewPending=null;this.viewWorker=null;this.near.dispose();this.shells.dispose();this.controller.dispose();this.inputEvents.abort();this.clearGeometry();for(const label of this.labels)label.element.remove();this.labels=[];for(const item of this.proxyLabels)item.element.remove();this.proxyLabels=[];for(const item of this.countLabelPool)item.element.remove();this.countLabelPool=[];this.countLabels=[];this.lod=null;this.graph=null;this.manifest=null;this.topics=null;this.controls.dispose();this.resizeObserver.disconnect();this.pickTarget.dispose();this.renderer.setRenderTarget(null);this.renderer.setClearColor(0x030611,0);this.renderer.clear();this.renderer.dispose();}
}
