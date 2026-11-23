/** Only the previous visual cut fades; all interactions use the accepted cut. */
export function transitionMix(now:number,start:number,reducedMotion=false):number{return reducedMotion?1:Math.max(0,Math.min(1,(now-start)/250));}
export function transitionLayers(previous:boolean,mix:number):number{return previous&&mix<1?2:1;}
/** Graph degree is stable when endpoints leave the viewport. Screen metrics stay separate. */
export function degreeIntensity(count:number):number{return Math.min(1,Math.log2(1+count)/16);}
export function sourceDegrees(nodeCount:number,edges:Uint32Array,visibleOrdinals:Uint32Array):Float32Array{const degree=new Uint32Array(nodeCount);for(const ordinal of visibleOrdinals){const from=edges[ordinal*2]!,to=edges[ordinal*2+1]!;degree[from]=(degree[from]??0)+1;if(to!==from)degree[to]=(degree[to]??0)+1;}return Float32Array.from(degree,degreeIntensity);}
