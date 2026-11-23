import {BufferAttribute,BufferGeometry,Group,LineBasicMaterial,LineSegments} from 'three';
import type {Scene,Camera} from 'three';
import {representativeKey,representativeKeyToken} from './lod.ts';
import type {LodResult} from './lod.ts';
import {worldProxyMesh} from './world-glyphs.ts';
import type {WorldGlyphCalibration} from './world-glyphs.ts';
/** Physical counterpart to legacy 2D visual pairing; no clipspace contour or CSS glyph size. */
export class WorldLevelVisualPair {
 readonly root=new Group();readonly parent;readonly child;readonly contour=null;
 readonly aperture={center:[0,0] as [number,number],radius:0,active:false};private disposed=false;private parentBase:number;private childBase:number;
 constructor(scene:Scene,parent:LodResult,child:LodResult,calibration:WorldGlyphCalibration,lineOpacity:(n:number)=>number){const create=(lod:LodResult)=>{const geometry=new BufferGeometry();geometry.setAttribute('position',new BufferAttribute(lod.render_positions,3));geometry.setIndex(new BufferAttribute(lod.render_edges,1));const lines=new LineSegments(geometry,new LineBasicMaterial({color:0x9cbfe5,transparent:true,opacity:lineOpacity(lod.render_edges.length/2),depthTest:true,depthWrite:false}));lines.frustumCulled=false;return{lines,proxies:worldProxyMesh(lod,calibration)};};this.parent=create(parent);this.child=create(child);this.parentBase=this.parent.lines.material.opacity;this.childBase=this.child.lines.material.opacity;this.root.add(this.parent.lines,this.parent.proxies,this.child.lines,this.child.proxies);scene.add(this.root);const keys=(lod:LodResult)=>lod.proxies.map((_,i)=>representativeKeyToken(representativeKey(lod,lod.representative_by_node.length+i))),pk=keys(parent),ck=keys(child),shared=new Set(pk.filter(key=>ck.includes(key)));this.parentCommon=pk.map(key=>shared.has(key));this.childCommon=ck.map(key=>shared.has(key));this.setMix(0);}
 private parentCommon:boolean[]=[];private childCommon:boolean[]=[];
 setView(_camera:Camera,_width:number,_height:number):void{}
 setMix(value:number):void{const m=Math.max(0,Math.min(1,value));this.parent.lines.material.opacity=this.parentBase*(1-m);this.child.lines.material.opacity=this.childBase*m;for(const [level,common,child]of [[this.parent,this.parentCommon,false],[this.child,this.childCommon,true]] as const){const attr=level.proxies.geometry.getAttribute('visible');for(let i=0;i<attr.count;i++)attr.setX(i,common[i]?(child?0:1):(child?m:1-m));attr.needsUpdate=true;}}
 setRelevance(parent:Uint8Array,child:Uint8Array,active:boolean):void{for(const [level,mask]of [[this.parent,parent],[this.child,child]] as const){level.proxies.material.uniforms.tapActive!.value=active?1:0;const attr=level.proxies.geometry.getAttribute('relevance');attr.array.set(mask);attr.needsUpdate=true;}}
 childLineVisible(_line:number):boolean{return true;}
 detachContour():null{return null;}
 dispose():void{if(this.disposed)return;this.disposed=true;this.root.removeFromParent();for(const level of [this.parent,this.child])for(const object of [level.lines,level.proxies]){object.geometry.dispose();object.material.dispose();}}
}
