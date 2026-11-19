export interface PreviewEntry {provider_id:string;revision:string;ordinal:number;id:string;text_revision:string;text:string;text_total:number}
interface PreviewCard {id:string;ordinal:number;text_revision:string;text:string;text_total:number}
function key(provider:string,revision:string,ordinal:number):string{return `${provider}\u0000${revision}\u0000${ordinal}`;}
export function previewFromCard(card:PreviewCard,provider:string,revision:string):PreviewEntry{return{provider_id:provider,revision,ordinal:card.ordinal,id:card.id,text_revision:card.text_revision,text:Array.from(card.text).slice(0,480).join(''),text_total:card.text_total};}
export class PreviewCache{
  private entries=new Map<string,{value:PreviewEntry;bytes:number}>();private used=0;private maximum:number;private budget:number;
  constructor(maximum=256,budget=2097152){this.maximum=maximum;this.budget=budget;}
  get size():number{return this.entries.size;}get bytes():number{return this.used;}
  get(provider:string,revision:string,ordinal:number):PreviewEntry|null{const id=key(provider,revision,ordinal);const found=this.entries.get(id);if(!found)return null;this.entries.delete(id);this.entries.set(id,found);return found.value;}
  put(value:PreviewEntry):boolean{
    const id=key(value.provider_id,value.revision,value.ordinal);const bytes=new TextEncoder().encode(JSON.stringify(value)).byteLength;const old=this.entries.get(id);if(old){this.used-=old.bytes;this.entries.delete(id);}if(bytes>this.budget)return false;
    while(this.entries.size>=this.maximum||this.used+bytes>this.budget){const first=this.entries.keys().next().value;if(first===undefined)break;const removed=this.entries.get(first);if(removed)this.used-=removed.bytes;this.entries.delete(first);}
    this.entries.set(id,{value,bytes});this.used+=bytes;return true;
  }
  clear():void{this.entries.clear();this.used=0;}
}
