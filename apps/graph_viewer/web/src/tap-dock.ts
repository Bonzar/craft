import { element } from './dom.ts';

/** A selected Source caption; clamping is entirely visual and never changes its text. */
export class TapDock {
  private root:HTMLElement;private body:HTMLElement;private count:HTMLElement;private read:HTMLButtonElement;private dismiss:HTMLButtonElement;
  private token:number|null=null;private disposed=false;
  constructor(root:HTMLElement,onRead:()=>void,onClear:()=>void=()=>{}){
    this.root=element('section','','tap-dock');this.body=element('span','','tap-dock-text');this.count=element('span','','tap-dock-count');
    this.read=element('button','','tap-dock-read');this.read.type='button';this.read.setAttribute('aria-label','Читать выбранный объект');this.read.title='Читать';
    const arrow=element('span','↗','tap-dock-arrow');arrow.setAttribute('aria-hidden','true');this.read.append(this.body,this.count,arrow);
    this.dismiss=element('button','×','tap-dock-clear');this.dismiss.type='button';this.dismiss.setAttribute('aria-label','Снять выделение');this.dismiss.title='Снять выделение';
    this.bindAction(this.read,onRead);this.bindAction(this.dismiss,()=>{this.clear();onClear();},true);
    this.root.setAttribute('aria-label','Выбранный объект карты');this.root.append(this.read,this.dismiss);this.root.hidden=true;root.append(this.root);
  }
  private bindAction(target:HTMLButtonElement,action:()=>void,stop=false):void{
    let pressed:number|null=null,cancelled=false,origin:{x:number;y:number}|null=null;
    target.addEventListener('pointerdown',event=>{pressed=this.token;cancelled=false;origin={x:event.clientX,y:event.clientY};});
    target.addEventListener('pointermove',event=>{if(origin&&Math.hypot(event.clientX-origin.x,event.clientY-origin.y)>6)cancelled=true;});
    target.addEventListener('pointercancel',()=>{cancelled=true;origin=null;});
    target.addEventListener('keydown',event=>{if((event.key==='Enter'||event.key===' ')&&!event.repeat){pressed=this.token;cancelled=false;origin=null;}});
    target.addEventListener('blur',()=>{if(pressed!==null)cancelled=true;});
    target.addEventListener('click',event=>{
      if(stop)event.stopPropagation();const token=pressed,invalid=cancelled;pressed=null;cancelled=false;origin=null;
      if(this.disposed||this.root.hidden||this.token===null||invalid||(token!==null&&token!==this.token))return;action();
    });
  }
  show(token:number,label:string,count:number):void{if(this.disposed)return;this.token=token;this.body.textContent=label;this.count.textContent=count>1?count.toLocaleString('ru'):'';this.count.hidden=count<=1;this.root.dataset.token=String(token);this.root.hidden=false;}
  updateLabel(token:number,text:string):void{if(!this.disposed&&this.token===token)this.body.textContent=text;}
  clear():void{this.token=null;this.root.hidden=true;this.body.textContent='';this.count.textContent='';delete this.root.dataset.token;}
  dispose():void{this.clear();this.disposed=true;this.root.remove();}
}
