import { BufferAttribute, BufferGeometry, Group, LineBasicMaterial, LineSegments, Mesh, NormalBlending, PlaneGeometry, Points, ShaderMaterial, Vector2, Vector3 } from 'three';
import type { Camera, Scene } from 'three';
import { proxyEdgeCounts, representativeKey, representativeKeyToken } from './lod.ts';
import type { LodResult } from './lod.ts';
import { degreeIntensity } from './visual-transition.ts';

/** Capture startSpan when the adjacent preview first becomes available. No clock drives this mix. */
export function scaleLevelMix(span:number,startSpan:number):number {
  if(!Number.isFinite(span)||!Number.isFinite(startSpan)||span<=0||startSpan<=0)return 0;
  return Math.max(0,Math.min(1,Math.log(span/startSpan)/Math.log(1.5)));
}
const clampMix=(value:number):number=>Number.isFinite(value)?Math.max(0,Math.min(1,value)):0;
const apertureFragment=`uniform float apertureActive;uniform vec2 apertureCenter;uniform float apertureRadius;varying float vApertureRequired;`;
const apertureDiscard=`if(apertureActive>.5&&vApertureRequired>.0001&&distance(gl_FragCoord.xy,apertureCenter)>apertureRadius)discard;`;
const proxyVertex=`attribute vec3 tint;attribute float degree;attribute float stableProxy;attribute float contourProxy;attribute float tapRelevance;uniform float tapActive;varying float vTapDim;varying float vGlow;varying vec3 vTint;varying float vOpacity;varying float vApertureRequired;uniform float dpr;uniform float mixLevel;uniform float childLevel;uniform float glyphScale;uniform float glyphFloor;void main(){float normalSize=24.+12.*degree;vGlow=.82+.18*degree;vTint=tint;vTapDim=mix(1.,mix(.12,1.,tapRelevance),tapActive);vOpacity=(childLevel>.5?(stableProxy>.5?0.:mixLevel):(stableProxy>.5?1.:1.-mixLevel))*(1.-contourProxy);vApertureRequired=childLevel>.5&&stableProxy<.5?1.:0.;gl_Position=vOpacity<=0.?vec4(2.,2.,2.,1.):projectionMatrix*modelViewMatrix*vec4(position,1.);gl_PointSize=(normalSize*glyphScale+glyphFloor)*dpr;}`;
const proxyFragment=`${apertureFragment}varying float vTapDim;varying float vGlow;varying vec3 vTint;varying float vOpacity;void main(){if(vOpacity<=0.)discard;${apertureDiscard}float r=length(gl_PointCoord-.5);if(r>.5)discard;float ring=exp(-pow((r-.34)*32.,2.));float core=exp(-r*r*38.)*.24;gl_FragColor=vec4(vTint*vGlow*vTapDim,(ring*.85+core)*vOpacity*vTapDim);}`;
const contourVertex=`uniform vec2 centerNdc;uniform vec2 viewport;uniform float radius;varying vec2 vOffset;void main(){vOffset=position.xy*(radius+3.);gl_Position=vec4(centerNdc+vOffset*2./viewport,0.,1.);}`;
const contourFragment=`uniform float radius;uniform float dpr;uniform float emphasis;varying vec2 vOffset;void main(){float edge=(length(vOffset)-radius)/max(.7,dpr*.8);float alpha=exp(-edge*edge)*.85;if(alpha<.003)discard;gl_FragColor=vec4(vec3(.55,.72,1.)*emphasis,alpha);}`;
export interface CircleAperture {center:[number,number];radius:number;active:boolean}
interface ApertureUniforms {apertureActive:{value:number};apertureCenter:{value:Vector2};apertureRadius:{value:number}}
function apertureUniforms():ApertureUniforms{return{apertureActive:{value:0},apertureCenter:{value:new Vector2()},apertureRadius:{value:0}};}

/** One camera-facing circle, independent of the Source arrays and of any LOD snapshot. */
export class AggregateContour {
  readonly key:string;
  readonly mesh:Mesh<PlaneGeometry,ShaderMaterial>;
  readonly aperture:CircleAperture={center:[0,0],radius:0,active:false};
  private center:Vector3;
  private radius:number;
  private startRadiusCss:number;
  private dpr:number;
  private mix=0;
  private targetRadiusCss=0;
  private disposed=false;
  constructor(key:string,center:readonly[number,number,number],radius:number,startRadiusCss:number,dpr:number){
    this.key=key;this.center=new Vector3(...center);this.radius=radius;this.startRadiusCss=startRadiusCss;this.dpr=dpr;
    this.mesh=new Mesh(new PlaneGeometry(2,2),new ShaderMaterial({vertexShader:contourVertex,fragmentShader:contourFragment,uniforms:{centerNdc:{value:new Vector2()},viewport:{value:new Vector2(1,1)},radius:{value:startRadiusCss*dpr},dpr:{value:dpr},emphasis:{value:1}},transparent:true,depthWrite:false,depthTest:false,blending:NormalBlending}));
    this.mesh.position.copy(this.center);this.mesh.name=`aggregate-contour:${key}`;this.mesh.frustumCulled=false;this.mesh.renderOrder=1;this.mesh.visible=false;
  }
  /** Bounds are projected conservatively; a camera inside them sends the boundary offscreen. */
  updateView(camera:Camera,width:number,height:number):void {
    if(this.disposed||!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0)return;
    camera.updateMatrixWorld();
    const projected=this.center.clone().project(camera);
    if(![projected.x,projected.y].every(Number.isFinite)){this.aperture.active=false;this.mesh.visible=false;return;}
    const x=(projected.x+1)*width/2,y=(projected.y+1)*height/2;
    this.aperture.center[0]=x*this.dpr;this.aperture.center[1]=y*this.dpr;
    (this.mesh.material.uniforms.centerNdc!.value as Vector2).set(projected.x,projected.y);
    (this.mesh.material.uniforms.viewport!.value as Vector2).set(width*this.dpr,height*this.dpr);
    let projectedRadius=0,crossesCamera=false;
    for(const sx of [-1,1])for(const sy of [-1,1])for(const sz of [-1,1]){
      const corner=this.center.clone().add(new Vector3(sx*this.radius,sy*this.radius,sz*this.radius));
      if(corner.clone().applyMatrix4(camera.matrixWorldInverse).z>=-.00001){crossesCamera=true;continue;}
      corner.project(camera);projectedRadius=Math.max(projectedRadius,Math.hypot((corner.x+1)*width/2-x,(corner.y+1)*height/2-y));
    }
    const coverViewport=Math.max(Math.hypot(x,y),Math.hypot(width-x,y),Math.hypot(x,height-y),Math.hypot(width-x,height-y))+4;
    this.targetRadiusCss=crossesCamera||!Number.isFinite(projectedRadius)?coverViewport*2:projectedRadius;
    this.aperture.active=true;this.mesh.visible=true;this.updateRadius();
  }
  setMix(value:number):void{if(this.disposed)return;this.mix=clampMix(value);this.updateRadius();}
  /** Selection changes brightness, never the circle's opacity or zoom continuity. */
  setEmphasis(value:number):void{if(!this.disposed)this.mesh.material.uniforms.emphasis!.value=Number.isFinite(value)?Math.max(.12,Math.min(1,value)):1;}
  private updateRadius():void {
    const radius=(this.startRadiusCss*(1-this.mix)+Math.max(this.startRadiusCss,this.targetRadiusCss)*this.mix)*this.dpr;
    this.aperture.radius=radius;this.mesh.material.uniforms.radius!.value=radius;
  }
  dispose():void{if(this.disposed)return;this.disposed=true;this.aperture.active=false;this.mesh.removeFromParent();this.mesh.geometry.dispose();this.mesh.material.dispose();}
}
export interface ScaleLevelVisual {
  readonly lines:LineSegments<BufferGeometry,LineBasicMaterial>;
  readonly proxies:Points<BufferGeometry,ShaderMaterial>;
  readonly region?:Mesh<PlaneGeometry,ShaderMaterial>;
}
export interface ScaleLevelVisualOptions {
  dpr:number;
  lineOpacity?:(count:number)=>number;
  /** Real Source member envelope/cell bounds, centered on the unchanged aggregate anchor. */
  parentRegion?:{center:readonly[number,number,number];radius:number;proxyKey?:string};
  /** Ownership moves from the persistent Scene registry back into this pair on inverse zoom. */
  existingContour?:AggregateContour;
}
function defaultLineOpacity(count:number):number{return Math.max(.012,.72*Math.min(1,Math.sqrt(200/Math.max(1,count))));}
function proxyKeys(lod:LodResult):string[]{return lod.proxies.map((_,index)=>representativeKeyToken(representativeKey(lod,lod.representative_by_node.length+index)));}
function createLevel(lod:LodResult,keys:readonly string[],common:ReadonlySet<string>,child:boolean,dpr:number,lineOpacity:(count:number)=>number,contourKey:string|undefined,clip:ApertureUniforms,required:Float32Array):ScaleLevelVisual {
  // Each level owns its GPU arrays. Neither BufferAttribute updates nor disposal can alter Source data.
  const lineGeometry=new BufferGeometry();lineGeometry.setAttribute('position',new BufferAttribute(lod.render_positions.slice(),3));lineGeometry.setIndex(new BufferAttribute(lod.render_edges.slice(),1));lineGeometry.setAttribute('apertureRequired',new BufferAttribute(required,1));
  const lineMaterial=new LineBasicMaterial({color:0x9cbfe5,transparent:true,opacity:lineOpacity(lod.render_edges.length/2),depthWrite:false,blending:NormalBlending});
  if(child){lineMaterial.onBeforeCompile=shader=>{Object.assign(shader.uniforms,clip);shader.vertexShader=`attribute float apertureRequired;varying float vApertureRequired;\n${shader.vertexShader}`.replace('#include <begin_vertex>','#include <begin_vertex>\nvApertureRequired=apertureRequired;');shader.fragmentShader=`${apertureFragment}\n${shader.fragmentShader}`.replace('void main() {',`void main() {${apertureDiscard.replace('>.0001','>.99999')}`);};lineMaterial.customProgramCacheKey=()=> 'aggregate-aperture-v1';}
  const lines=new LineSegments(lineGeometry,lineMaterial);lines.frustumCulled=false;
  const count=lod.proxies.length,positions=new Float32Array(count*3),colors=new Float32Array(count*3),shared=new Float32Array(count),replaced=new Float32Array(count),degrees=Float32Array.from(proxyEdgeCounts(lod),value=>degreeIntensity(value.total_unique));
  for(let index=0;index<count;index++){const proxy=lod.proxies[index]!;positions.set(proxy.position,index*3);colors.set(proxy.kind==='cell'?[.61,.65,.71]:[.55,.72,1],index*3);shared[index]=common.has(keys[index]!)?1:0;replaced[index]=contourKey!==undefined&&keys[index]===contourKey?1:0;}
  const geometry=new BufferGeometry();geometry.setAttribute('position',new BufferAttribute(positions,3));geometry.setAttribute('tint',new BufferAttribute(colors,3));geometry.setAttribute('degree',new BufferAttribute(degrees,1));geometry.setAttribute('stableProxy',new BufferAttribute(shared,1));geometry.setAttribute('contourProxy',new BufferAttribute(replaced,1));geometry.setAttribute('tapRelevance',new BufferAttribute(new Float32Array(count).fill(1),1));
  const material=new ShaderMaterial({vertexShader:proxyVertex,fragmentShader:proxyFragment,uniforms:{...clip,tapActive:{value:0},dpr:{value:dpr},mixLevel:{value:0},childLevel:{value:child?1:0},glyphScale:{value:child?0:1},glyphFloor:{value:child?2:0}},transparent:true,depthWrite:false,blending:NormalBlending});
  const proxies=new Points(geometry,material);proxies.frustumCulled=false;
  return{lines,proxies};
}
/** Two visual cuts only. Counts, canonical points and all picking remain with the accepted cut. */
export class ScaleLevelVisualPair {
  readonly root=new Group();
  readonly parent:ScaleLevelVisual;
  readonly child:ScaleLevelVisual;
  readonly contour:AggregateContour|null;
  private scene:Scene;
  private detached=false;
  private clip=apertureUniforms();
  private parentOpacity:number;
  private childOpacity:number;
  private disposed=false;
  private currentMix=0;
  private contourProxyIndex=-1;
  private childEdgeIndices:Uint32Array;
  private childEdgeVisible:Uint8Array;
  private mixedEdges:{edge:number;anchor:number}[]=[];
  private viewCamera:Camera|null=null;
  private viewWidth=0;
  private viewHeight=0;
  private projectedAnchor=new Vector3();
  private cameraAnchor=new Vector3();
  constructor(scene:Scene,parent:LodResult,child:LodResult,options:ScaleLevelVisualOptions){
    if(parent.revision!==child.revision)throw new Error('Scale levels must share a Source revision');
    if(options.existingContour&&options.parentRegion?.proxyKey!==options.existingContour.key)throw new Error('Aggregate contour identity mismatch');
    this.scene=scene;
    const parentKeys=proxyKeys(parent),childKeys=proxyKeys(child),parentSet=new Set(parentKeys),childSet=new Set(childKeys),common=new Set(parentKeys.filter(key=>childSet.has(key)));
    const opacity=options.lineOpacity??defaultLineOpacity,bounds=options.parentRegion;
    const validBounds=bounds&&Number.isFinite(bounds.radius)&&bounds.radius>0&&bounds.center.every(Number.isFinite);
    const contourKey=validBounds?bounds.proxyKey:undefined;
    const parentLevel=createLevel(parent,parentKeys,common,false,options.dpr,opacity,contourKey,apertureUniforms(),new Float32Array(parent.render_positions.length/3));
    const required=new Float32Array(child.render_positions.length/3),nodeCount=child.representative_by_node.length;
    for(let index=0;index<required.length;index++)required[index]=index<nodeCount?(parent.node_render_visible[index]?0:1):(parentSet.has(childKeys[index-nodeCount]!)?0:1);
    this.child=createLevel(child,childKeys,common,true,options.dpr,opacity,contourKey,this.clip,required);
    this.childEdgeIndices=child.render_edges.slice();this.childEdgeVisible=new Uint8Array(child.render_edges.length/2).fill(1);
    for(let edge=0;edge<this.childEdgeVisible.length;edge++){const from=child.render_edges[edge*2]!,to=child.render_edges[edge*2+1]!;if(required[from]!==required[to])this.mixedEdges.push({edge,anchor:required[from]?from:to});}
    this.contour=null;
    if(validBounds){const index=parentKeys.indexOf(bounds.proxyKey??'');this.contourProxyIndex=index;const degree=index<0?0:degreeIntensity(proxyEdgeCounts(parent)[index]!.total_unique),startRadiusCss=.34*(24+12*degree);this.contour=options.existingContour??new AggregateContour(bounds.proxyKey??'',bounds.center,bounds.radius,startRadiusCss,options.dpr);this.parent={...parentLevel,region:this.contour.mesh};}
    else this.parent=parentLevel;
    this.parentOpacity=this.parent.lines.material.opacity;this.childOpacity=this.child.lines.material.opacity;
    this.root.name='scale-level-visual-pair';this.root.add(this.parent.lines,this.parent.proxies,this.child.lines,this.child.proxies);if(this.contour)this.root.add(this.contour.mesh);scene.add(this.root);this.setMix(0);
  }
  get mix():number{return this.currentMix;}
  get aperture():CircleAperture{return this.contour?.aperture??{center:[0,0],radius:0,active:false};}
  setView(camera:Camera,width:number,height:number):void{if(this.disposed)return;this.viewCamera=camera;this.viewWidth=width;this.viewHeight=height;this.contour?.updateView(camera,width,height);this.syncAperture();this.updateMixedEdges();}
  /** Mixed outgoing relations appear whole once their new endpoint is revealed. */
  private updateMixedEdges():void {
    const camera=this.viewCamera;if(!camera||!this.mixedEdges.length)return;
    const aperture=this.aperture,positions=this.child.lines.geometry.getAttribute('position'),viewport=this.contour?.mesh.material.uniforms.viewport!.value as Vector2|undefined;
    const width=viewport?.x??this.viewWidth,height=viewport?.y??this.viewHeight;let changed=false;
    for(const {edge,anchor} of this.mixedEdges){
      let visible=1;
      if(aperture.active){this.projectedAnchor.fromBufferAttribute(positions,anchor);const front=this.cameraAnchor.copy(this.projectedAnchor).applyMatrix4(camera.matrixWorldInverse).z<0;this.projectedAnchor.project(camera);visible=front&&Number.isFinite(this.projectedAnchor.x)&&Number.isFinite(this.projectedAnchor.y)&&Math.hypot((this.projectedAnchor.x+1)*width/2-aperture.center[0],(this.projectedAnchor.y+1)*height/2-aperture.center[1])<=aperture.radius?1:0;}
      if(this.childEdgeVisible[edge]!==visible){this.childEdgeVisible[edge]=visible;changed=true;}
    }
    if(!changed)return;
    const index=this.child.lines.geometry.getIndex()!;let count=0;
    for(let edge=0;edge<this.childEdgeVisible.length;edge++)if(this.childEdgeVisible[edge]){index.setX(count++,this.childEdgeIndices[edge*2]!);index.setX(count++,this.childEdgeIndices[edge*2+1]!);}
    index.needsUpdate=true;this.child.lines.geometry.setDrawRange(0,count);
  }
  childLineVisible(line:number):boolean{return this.childEdgeVisible[line]!==0;}
  private syncAperture():void{const aperture=this.aperture;this.clip.apertureActive.value=aperture.active?1:0;this.clip.apertureCenter.value.set(...aperture.center);this.clip.apertureRadius.value=aperture.radius;}
  setMix(value:number):void {
    if(this.disposed)return;
    const mix=clampMix(value);this.currentMix=mix;
    // Complementary stroke weights keep the two representations within one edge budget.
    this.parent.lines.material.opacity=this.parentOpacity*(1-mix);this.child.lines.material.opacity=this.childOpacity*mix;
    this.parent.proxies.material.uniforms.mixLevel!.value=mix;this.child.proxies.material.uniforms.mixLevel!.value=mix;this.child.proxies.material.uniforms.glyphScale!.value=mix;this.child.proxies.material.uniforms.glyphFloor!.value=2*(1-mix);
    this.contour?.setMix(mix);this.syncAperture();this.updateMixedEdges();
  }
  /** Update the existing GPU masks; selection never replaces geometry or Source arrays. */
  setRelevance(parent:Uint8Array,child:Uint8Array,active:boolean):void {
    if(this.disposed)return;
    if(this.contour&&this.contourProxyIndex>=0)this.contour.setEmphasis(active&&!parent[this.contourProxyIndex]? .12 : 1);
    for(const [level,mask] of [[this.parent,parent],[this.child,child]] as const){
      level.proxies.material.uniforms.tapActive!.value=active?1:0;
      const relevance=level.proxies.geometry.getAttribute('tapRelevance');let changed=false;
      for(let index=0;index<relevance.count;index++){const value=mask[index]?1:0;if(relevance.getX(index)!==value){relevance.setX(index,value);changed=true;}}
      if(changed)relevance.needsUpdate=true;
    }
  }
  /** Transfer the SAME contour to Scene; subsequent pair disposal leaves it alive. */
  detachContour():AggregateContour|null{if(this.disposed||this.detached||!this.contour)return null;this.detached=true;this.scene.add(this.contour.mesh);return this.contour;}
  dispose():void {
    if(this.disposed)return;this.disposed=true;this.root.removeFromParent();
    for(const level of [this.parent,this.child])for(const object of [level.lines,level.proxies]){object.removeFromParent();object.geometry.dispose();object.material.dispose();}
    if(!this.detached)this.contour?.dispose();
  }
}
