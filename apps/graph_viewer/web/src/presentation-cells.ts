/** Presentation geometry only: no Source identities, memberships or coordinates are written. */
import type {Bounds} from './spatial.ts';
export interface PresentationCell {id:string;parent:string|null;bounds:Bounds;position:[number,number,number];members:Uint32Array;children:PresentationCell[];coincident:boolean}
export class PresentationCellCache {
  private entries=new Map<string,{members:Uint32Array;root:PresentationCell}>();
  clear():void{this.entries.clear();}
  tree(positions:Float32Array,members:Uint32Array,context:string):PresentationCell|null{
    if(!members.length)return null;
    const old=this.entries.get(context);
    if(old&&old.members.length===members.length&&old.members.every((v,i)=>v===members[i]))return old.root;
    const build=(ordinals:Uint32Array,path:string,parent:string|null,depth:number):PresentationCell=>{
      const min:[number,number,number]=[Infinity,Infinity,Infinity],max:[number,number,number]=[-Infinity,-Infinity,-Infinity];
      for(const ordinal of ordinals)for(let axis=0;axis<3;axis++){const value=positions[ordinal*3+axis]!;min[axis]=Math.min(min[axis]!,value);max[axis]=Math.max(max[axis]!,value);}
      const id=`${context}/${path}`,position=min.map((v,i)=>(v+max[i]!)/2) as [number,number,number];
      const cell:PresentationCell={id,parent,bounds:{min,max},position,members:ordinals,children:[],coincident:min.every((v,i)=>v===max[i])};
      if(ordinals.length<=32||depth>=18||cell.coincident)return cell;
      let axis=0;for(let i=1;i<3;i++)if(max[i]!-min[i]!>max[axis]!-min[axis]!)axis=i;
      const split=(min[axis]!+max[axis]!)/2,lower:number[]=[],upper:number[]=[];
      for(const ordinal of ordinals)((positions[ordinal*3+axis]??0)<split?lower:upper).push(ordinal);
      if(lower.length&&upper.length)cell.children=[build(new Uint32Array(lower),`${path}0`,id,depth+1),build(new Uint32Array(upper),`${path}1`,id,depth+1)];
      return cell;
    };
    const root=build(members,'r',null,0);this.entries.set(context,{members:members.slice(),root});
    // Old navigation contexts are cheap to rebuild; bound retained geometry.
    if(this.entries.size>64)this.entries.delete(this.entries.keys().next().value!);
    return root;
  }
}
