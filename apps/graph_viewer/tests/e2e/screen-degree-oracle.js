// Independent full source-edge recomputation; never reuses viewer weighted CSR.
export function expectedScreenDegree(input){
 const n=input.visible.length,r=input.representativeCount;const externalVisible=new Uint32Array(r),canonicalSelf=new Uint32Array(r),proxyInternal=new Uint32Array(r);let uniqueExternalEdges=0;
 for(let edge=0;edge<input.edges.length/2;edge++){
  if(input.edgeActive&&!input.edgeActive[edge])continue;const from=input.edges[edge*2],to=input.edges[edge*2+1];if(from>=n||to>=n)throw new RangeError('Invalid source endpoint');if(!input.visible[from]||!input.visible[to])continue;const a=input.representatives[from],b=input.representatives[to];if(a<0||b<0||a>=r||b>=r)throw new RangeError('Invalid accepted representative');
  if(a===b){if(a>=n)proxyInternal[a]++;else canonicalSelf[a]++;}
  else if(input.onscreen[a]&&input.onscreen[b]){externalVisible[a]++;externalVisible[b]++;uniqueExternalEdges++;}
 }
 let incidentSum=0;for(const count of externalVisible)incidentSum+=count;if(incidentSum!==uniqueExternalEdges*2)throw new Error('External edge incidence mismatch');return{externalVisible,canonicalSelf,proxyInternal,uniqueExternalEdges,incidentSum};
}
export function projectedCenters(positions,active,matrix,viewport){
 if(matrix.length!==16||positions.length!==active.length*3)throw new RangeError('Projection dimensions');const result=new Uint8Array(active.length),[width,height]=viewport;
 for(let i=0;i<active.length;i++){
  if(!active[i])continue;const x=positions[i*3],y=positions[i*3+1],z=positions[i*3+2];
  const cx=matrix[0]*x+matrix[4]*y+matrix[8]*z+matrix[12],cy=matrix[1]*x+matrix[5]*y+matrix[9]*z+matrix[13],cz=matrix[2]*x+matrix[6]*y+matrix[10]*z+matrix[14],cw=matrix[3]*x+matrix[7]*y+matrix[11]*z+matrix[15];
  if(cw<=0||cz< -cw||cz>cw)continue;const px=(cx/cw*.5+.5)*width,py=(.5-cy/cw*.5)*height;result[i]=Number(px>=0&&px<=width&&py>=0&&py<=height);
 }
 return result;
}
