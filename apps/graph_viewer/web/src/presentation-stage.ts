/** Only stable source identity may carry an object across immutable revisions. */
export function reboundNodeOrdinal(target:{provider_id:string;revision:string;id:string},provider:string,card:{id:string;revision:string;ordinal:number}):number|null {
  return target.provider_id===provider&&target.revision===card.revision&&target.id===card.id?card.ordinal:null;
}
export function topicIndicesForIds(ids:readonly string[],topics:readonly {id:string;index:number}[]):Uint32Array {
  const wanted=new Set(ids);return new Uint32Array(topics.filter(topic=>wanted.has(topic.id)).map(topic=>topic.index));
}
export function visualStageMatches(target:{revision:string;generation:number},stage:{lod:{revision:string;generation:number};metrics:{revision:string;lod_generation:number};envelopes:{revision:string;lod_generation:number}}):boolean {
  return stage.lod.revision===target.revision&&stage.lod.generation===target.generation&&[stage.metrics,stage.envelopes].every(value=>value.revision===target.revision&&value.lod_generation===target.generation);
}
