import { test } from 'node:test';
import assert from 'node:assert/strict';
import { edgeSchema, nodeSchema, topicsSchema } from '../src/contracts.ts';
function nodeResponse():unknown{return {id:'source-node',ordinal:0,revision:'r1',text_revision:'t1',text:'Literal <script>😀</script>',text_offset:0,text_total:26,next_text_offset:null,actor:{id:null,display_name:null},source:null,episode:null,scope:null,time:{valid_at:null,valid_precision:'unknown',recorded_at:null,source_recorded_at:null,source_available_at:null,interpreted_at:null},status:{disposition:'unknown',unresolved_conflict:false,replacements:[],basis_edge_ids:[],effective_at:null,effectiveness:'unknown'},sources:[],topic_ids:[],incident_edges:{items:[],total:0,visible_total:0,next_cursor:null,url:''},unknowns:[]};}
test('source card with unknown actor identity remains readable and source markup remains literal text',()=>{
  const card=nodeSchema.parse(nodeResponse());
  assert.equal(card.actor?.id,null);
  assert.equal(card.text,'Literal <script>😀</script>');
});
test('source edge with missing quote is an explicit missing fragment instead of a malformed card',()=>{
  const endpoint={id:'a',text:'Whole assertion',fragment:{quote:null,spans:[],offset_unit:'unicode_codepoints',status:'missing'}};
  const card=edgeSchema.parse({id:'e',revision:'r1',kind:'related',condition:null,recorded_at:null,from:endpoint,to:{...endpoint,id:'b'},unknowns:[]});
  assert.equal(card.from.fragment.status,'missing');
  assert.equal(card.from.fragment.quote,null);
});
test('external graph object with an invalid ordinal is rejected at its boundary',()=>{
  assert.throws(()=>nodeSchema.parse({...nodeResponse() as Record<string,unknown>,ordinal:-1}));
});

test('leaf source topic without a routing revision remains a real source topic with unknown route',()=>{
  const page=topicsSchema.parse({revision:'r1',topics:[{id:'topic:leaf',index:0,label:'Точная тема',full_title:'Точная тема',label_method:'source_title_excerpt',source_ref:{view_id:'leaf',version_id:1,route_revision:null},position:[0,0,0],radius:90,depth:0,member_count:1,freshness:'unknown',available_at:null}],memberships:[{topic_index:0,node_indices:[0]}],links:[],completeness:'complete',unknowns:[]});
  assert.equal(page.topics[0]?.source_ref.route_revision,null);
  assert.equal(page.topics[0]?.full_title,'Точная тема');
});
