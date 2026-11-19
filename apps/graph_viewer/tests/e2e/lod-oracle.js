// Independent QA source-partition oracle. It never imports viewer algorithms,
// invents topics or allocates a million edge objects. Per-edge results are bytes.
export function expectedLod(input) {
  const {visible,edges,topics}=input;const n=visible.length;
  if(edges.length%2)throw new RangeError('Unpaired source endpoints');
  const byIndex=new Map(topics.map(topic=>[topic.index,topic]));
  const byId=new Map(topics.map(topic=>[topic.id,topic.index]));
  if(byIndex.size!==topics.length||byId.size!==topics.length)throw new RangeError('Duplicate source topic identity');
  const parents=new Map(topics.map(topic=>[topic.index,new Set()]));
  const children=new Map(topics.map(topic=>[topic.index,new Set()]));
  const resolve=value=>typeof value==='number'?value:byId.get(value);
  for(const link of input.links||[]) {
    const p=resolve(link.parent),c=resolve(link.child);const parent=byIndex.get(p);
    if(link.relation!=='subquestion'||!parent||!byIndex.has(c)||link.provenance?.view_id!==parent.source_ref?.view_id||!Number.isSafeInteger(link.provenance?.route_revision)||link.provenance.route_revision<1)continue;
    parents.get(c).add(p);children.get(p).add(c);
  }
  const indegree=new Map([...parents].map(([key,value])=>[key,value.size]));
  const queue=topics.filter(topic=>!indegree.get(topic.index)).map(topic=>topic.index);const order=[];const depth=new Map(topics.map(topic=>[topic.index,0]));
  for(let at=0;at<queue.length;at++) {
    const parent=queue[at];order.push(parent);
    for(const child of children.get(parent)) {
      depth.set(child,Math.max(depth.get(child),depth.get(parent)+1));indegree.set(child,indegree.get(child)-1);
      if(indegree.get(child)===0)queue.push(child);
    }
  }
  if(order.length!==topics.length)throw new RangeError('Source hierarchy cycle');
  const collapsed=new Set(input.collapsed||[]);const forced=new Set(input.forced||[]);const eligible=new Map();
  for(const index of order)eligible.set(index,parents.get(index).size===0||[...parents.get(index)].some(parent=>eligible.get(parent)&&!collapsed.has(parent)));
  for(const index of collapsed)if(!byIndex.has(index))throw new RangeError('Unknown collapsed source topic');
  for(const index of forced)if(!Number.isSafeInteger(index)||index<0||index>=n)throw new RangeError('Invalid forced source ordinal');
  const representativeByNode=new Int32Array(n);let visibleNodes=0;
  for(let i=0;i<n;i++){representativeByNode[i]=visible[i]?i:-1;visibleNodes+=Boolean(visible[i]);}
  const topicCounts=new Map();
  for(const topic of topics) {
    let sourceMembers=0,visibleMembers=0;const seen=new Set();
    for(const member of topic.members) {
      if(!Number.isSafeInteger(member)||member<0||member>=n)throw new RangeError('Dangling source membership');
      if(seen.has(member))continue;seen.add(member);sourceMembers++;visibleMembers+=Boolean(visible[member]);
    }
    topicCounts.set(topic.index,{index:topic.index,id:topic.id,sourceMembers,visibleMembers,owned:0,internalEdges:0});
  }
  const candidates=topics.filter(topic=>collapsed.has(topic.index)&&eligible.get(topic.index)).sort((a,b)=>depth.get(b.index)-depth.get(a.index)||(a.id<b.id?-1:a.id>b.id?1:0));
  for(const topic of candidates)for(const node of topic.members) {
    if(visible[node]&&!forced.has(node)&&representativeByNode[node]===node){representativeByNode[node]=n+topic.index;topicCounts.get(topic.index).owned++;}
  }
  let canonicalNodes=0,representedNodes=0;
  for(let i=0;i<n;i++)if(visible[i]){if(representativeByNode[i]===i)canonicalNodes++;else representedNodes++;}
  const edgeClass=new Uint8Array(edges.length/2).fill(255);let visibleEdges=0,originalEdges=0,bundledEdges=0,internalEdges=0;
  for(let i=0;i<edgeClass.length;i++) {
    const from=edges[i*2],to=edges[i*2+1];if(from>=n||to>=n)throw new RangeError('Dangling source edge');
    if(input.edgeActive&&!input.edgeActive[i]||!visible[from]||!visible[to])continue;visibleEdges++;
    const a=representativeByNode[from],b=representativeByNode[to];
    if(a<n&&b<n){edgeClass[i]=0;originalEdges++;}
    else if(a===b){edgeClass[i]=2;internalEdges++;topicCounts.get(a-n).internalEdges++;}
    else{edgeClass[i]=1;bundledEdges++;}
  }
  if(canonicalNodes+representedNodes!==visibleNodes||originalEdges+bundledEdges+internalEdges!==visibleEdges)throw new Error('Oracle partition invariant violated');
  return {representativeByNode,edgeClass,topics:topics.map(topic=>topicCounts.get(topic.index)),coverage:{visibleNodes,canonicalNodes,representedNodes,visibleEdges,originalEdges,bundledEdges,internalEdges},encoding:'canonical=ordinal; proxy=nodeCount+sourceTopicIndex; hidden=-1; edgeClass original0/bundled1/internal2/hidden255'};
}
