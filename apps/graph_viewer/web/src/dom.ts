export function element<K extends keyof HTMLElementTagNameMap>(tag:K,text='',className=''):HTMLElementTagNameMap[K]{const result=document.createElement(tag);if(text)result.textContent=text;if(className)result.className=className;return result;}
export function button(label:string,action:()=>void,className=''):HTMLButtonElement{const result=element('button',label,className);result.type='button';result.addEventListener('click',action);return result;}
export function labelledInput(label:string,type:string,value=''): {label:HTMLLabelElement;input:HTMLInputElement}{const wrapper=element('label',label);const input=element('input');input.type=type;input.value=value;wrapper.append(input);return{label:wrapper,input};}
export function detail(label:string,value:string):HTMLElement{const row=element('div','','detail');row.append(element('dt',label),element('dd',value));return row;}
export function note(text:string):HTMLElement{return element('p',text,'note');}
export function setBusy(target:HTMLButtonElement,busy:boolean,label:string):void {target.disabled=busy;target.textContent=busy?'Загружаем…':label;}
