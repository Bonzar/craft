// Retain presentation only when a verified owner scope AND database epoch agree.
export function preserveOwnerGraphPositions(previous,next){
  const {scope,epoch,ids,positions}=next;
  if(typeof scope!=='string'||!scope||typeof epoch!=='string'||!epoch||
      !Array.isArray(ids)||!(positions instanceof Float32Array)||positions.length!==ids.length*3)
    throw new TypeError('Invalid graph placement checkpoint');
  const nextSeen=new Set();
  for(let i=0;i<ids.length;i++){
    const id=ids[i];
    if(id===null)continue;
    if(typeof id!=='string'||!id||id.length>1024||nextSeen.has(id)||!finite(positions,i))
      throw new TypeError('Invalid graph stable identity');
    nextSeen.add(id);
  }
  if(!previous||previous.scope!==scope||previous.epoch!==epoch)return positions;
  if(!Array.isArray(previous.ids)||!(previous.positions instanceof Float32Array)||
      previous.positions.length!==previous.ids.length*3)
    throw new TypeError('Invalid previous graph placement');
  const retained=new Map();
  for(let i=0;i<previous.ids.length;i++){
    const id=previous.ids[i];
    if(id===null)continue;
    if(typeof id!=='string'||!id||id.length>1024||retained.has(id)||!finite(previous.positions,i))
      throw new TypeError('Invalid previous stable identity');
    retained.set(id,i);
  }
  let output=null;
  for(let i=0;i<ids.length;i++){
    const old=retained.get(ids[i]);
    if(old===undefined)continue;
    output??=positions.slice();
    output.set(previous.positions.subarray(old*3,old*3+3),i*3);
  }
  return output??positions;
}
function finite(positions,ordinal){
  const i=ordinal*3;
  return Number.isFinite(positions[i])&&Number.isFinite(positions[i+1])&&Number.isFinite(positions[i+2]);
}

export function ownerGraphDeltaIds(current,delta){
  const result=current.slice(),seen=new Set();
  for(const node of delta.upsert_nodes){
    if(!Number.isInteger(node.ordinal)||node.ordinal<0||typeof node.id!=='string'||!node.id||seen.has(node.ordinal)||
      (result[node.ordinal]!=null&&result[node.ordinal]!==node.id))throw new Error('Invalid live node identity');
    seen.add(node.ordinal);result[node.ordinal]=node.id;
  }
  for(let i=0;i<result.length;i++)if(result[i]===undefined)throw new Error('Noncontiguous live node identities');
  for(const ordinal of delta.remove_node_ordinals){
    if(!Number.isInteger(ordinal)||ordinal<0||ordinal>=result.length)throw new Error('Invalid live node removal');
    result[ordinal]=null;
  }
  const live=result.filter(x=>x!==null);
  if(new Set(live).size!==live.length)throw new Error('Duplicate live node identity');
  return result;
}

export function extendOwnerGraphPositions(previous,next){
  if(!previous||previous.scope!==next.scope||previous.epoch!==next.epoch)
    return preserveOwnerGraphPositions(null,next);
  const restored=preserveOwnerGraphPositions(previous,next).slice();
  const fixed=new Set(previous.ids.filter(id=>id!==null));
  const weights=new Uint32Array(next.ids.length),centers=new Float64Array(restored.length);
  const accumulate=(newIndex,oldIndex)=>{
    if(next.ids[newIndex]===null||fixed.has(next.ids[newIndex])||!fixed.has(next.ids[oldIndex]))return;
    weights[newIndex]++;
    for(let axis=0;axis<3;axis++)centers[newIndex*3+axis]+=restored[oldIndex*3+axis];
  };
  for(let i=0;i<next.edgeActive.length;i++){
    if(!next.edgeActive[i])continue;
    const a=next.edges[i*2],b=next.edges[i*2+1];
    if(a<next.ids.length&&b<next.ids.length&&a!==b){accumulate(a,b);accumulate(b,a);}
  }
  // Only new, present nodes are placed; no force solver or drift for old ones.
  for(let i=0;i<next.ids.length;i++){
    const id=next.ids[i];
    if(id===null||fixed.has(id))continue;
    let hash=2166136261;
    for(let at=0;at<id.length;at++)hash=Math.imul(hash^id.charCodeAt(at),16777619);
    for(let axis=0;axis<3;axis++){
      hash=Math.imul(hash^(hash>>>13),0x9e3779b1);
      const jitter=((hash>>>0)/0xffffffff-.5)*18;
      restored[i*3+axis]=(weights[i]?centers[i*3+axis]/weights[i]:0)+jitter;
    }
  }
  return restored;
}

export function preserveOwnerCamera(camera,controls){
  const position=camera.position.clone(),quaternion=camera.quaternion.clone(),target=controls.target.clone();
  const {zoom,near,far}=camera,{minDistance,maxDistance}=controls;
  return ()=>{
    camera.position.copy(position);camera.quaternion.copy(quaternion);controls.target.copy(target);
    camera.zoom=zoom;camera.near=near;camera.far=far;controls.minDistance=minDistance;controls.maxDistance=maxDistance;
    camera.updateProjectionMatrix();camera.updateMatrixWorld();
  };
}
