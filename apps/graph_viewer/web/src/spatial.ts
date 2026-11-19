export type Vec3=readonly[number,number,number];
export interface Bounds {min:[number,number,number];max:[number,number,number]}
interface Branch {bounds:Bounds;children:Branch[];ordinals:Uint32Array}
export interface SpatialIndex {root:Branch;size:number}
export type Plane=readonly[number,number,number,number];
export interface QueryStats {branches:number;points:number}
export function buildSpatialIndex(positions:Float32Array,radii?:Float32Array):SpatialIndex{
  const size=positions.length/3;const indices=Uint32Array.from({length:size},(_,ordinal)=>ordinal);
  const build=(ordinals:Uint32Array,depth:number):Branch=>{
    const min:[number,number,number]=[Infinity,Infinity,Infinity];const max:[number,number,number]=[-Infinity,-Infinity,-Infinity];
    for(const ordinal of ordinals){const radius=radii?.[ordinal]??0;for(let axis=0;axis<3;axis++){const value=positions[ordinal*3+axis]??0;min[axis]=Math.min(min[axis]??Infinity,value-radius);max[axis]=Math.max(max[axis]??-Infinity,value+radius);}}
    if(!ordinals.length)return{bounds:{min:[0,0,0],max:[0,0,0]},children:[],ordinals};
    const bounds={min,max};if(ordinals.length<=32||depth>=18)return{bounds,children:[],ordinals};
    let axis=0;for(let candidate=1;candidate<3;candidate++)if((max[candidate]??0)-(min[candidate]??0)>(max[axis]??0)-(min[axis]??0))axis=candidate;
    const split=((min[axis]??0)+(max[axis]??0))/2;let lower=0;for(const ordinal of ordinals)if((positions[ordinal*3+axis]??0)<split)lower++;
    if(!lower||lower===ordinals.length)return{bounds,children:[],ordinals};
    const ordered=new Uint32Array(ordinals.length);let left=0;let right=lower;for(const ordinal of ordinals){if((positions[ordinal*3+axis]??0)<split)ordered[left++]=ordinal;else ordered[right++]=ordinal;}
    return{bounds,children:[build(ordered.subarray(0,lower),depth+1),build(ordered.subarray(lower),depth+1)],ordinals:new Uint32Array()};
  };
  return{root:build(indices,0),size};
}
export function intersects(bounds:Bounds,planes:readonly Plane[]):boolean{
  for(const plane of planes){let distance=plane[3];for(let axis=0;axis<3;axis++){const normal=plane[axis]??0;distance+=normal*(normal>=0?(bounds.max[axis]??0):(bounds.min[axis]??0));}if(distance<0)return false;}
  return true;
}
export function visitSpatial(index:SpatialIndex,planes:readonly Plane[],visit:(ordinal:number)=>void,stats:QueryStats):void{
  const walk=(branch:Branch):void=>{stats.branches++;if(!intersects(branch.bounds,planes))return;if(branch.children.length){for(const child of branch.children)walk(child);}else for(const ordinal of branch.ordinals){stats.points++;visit(ordinal);}};walk(index.root);
}
export function frustumPlanes(matrix:Float32Array,xmin=-1,xmax=1,ymin=-1,ymax=1):Plane[]{
  const row=(axis:number):[number,number,number,number]=>[matrix[axis]??0,matrix[axis+4]??0,matrix[axis+8]??0,matrix[axis+12]??0];
  const x=row(0),y=row(1),z=row(2),w=row(3);const mix=(a:readonly number[],b:readonly number[],factor:number):Plane=>[(a[0]??0)+(b[0]??0)*factor,(a[1]??0)+(b[1]??0)*factor,(a[2]??0)+(b[2]??0)*factor,(a[3]??0)+(b[3]??0)*factor];
  return[mix(x,w,-xmin),mix(w.map(value=>value*xmax),x,-1),mix(y,w,-ymin),mix(w.map(value=>value*ymax),y,-1),mix(w,z,1),mix(w,z,-1)];
}
export function projectPoint(matrix:Float32Array,x:number,y:number,z:number,width:number,height:number):{x:number;y:number;depth:number;visible:boolean}{
  const clip=(row:number):number=>(matrix[row]??0)*x+(matrix[row+4]??0)*y+(matrix[row+8]??0)*z+(matrix[row+12]??0);
  const depth=clip(3);if(depth<=0)return{x:0,y:0,depth,visible:false};const px=clip(0)/depth,py=clip(1)/depth,pz=clip(2)/depth;
  return{x:(px*.5+.5)*width,y:(.5-py*.5)*height,depth,visible:Math.abs(px)<=1&&Math.abs(py)<=1&&pz>=-1&&pz<=1};
}
