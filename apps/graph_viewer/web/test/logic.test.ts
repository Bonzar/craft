import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeGraph, incidentIndices } from '../src/binary.ts';
import { codepointSegments, temporalSelector, canApplyDelta, pinCard } from '../src/logic.ts';

test('binary rejects truncated transfers and invalid endpoints instead of rendering a partial universe', () => {
  assert.throws(() => decodeGraph(new ArrayBuffer(15), new ArrayBuffer(0), 1, 0, false), /length/i);
  const nodes = new ArrayBuffer(32);
  const edges = new Uint32Array([0, 2]);
  assert.throws(() => decodeGraph(nodes, edges.buffer, 2, 1, false), /endpoint/i);
});
test('history filters old stars and every incident edge; historical slots remain stable', () => {
  const nodes = new ArrayBuffer(48);
  new DataView(nodes).setUint32(28, 1, true);
  const edges = new Uint32Array([0,1,0,2,2,0]);
  const graph = decodeGraph(nodes, edges.buffer, 3, 3, false);
  assert.deepEqual(Array.from(graph.visibleEdges), [0,2,2,0]);
  assert.deepEqual(Array.from(graph.visibleEdgeOrdinals), [1,2]);
  assert.deepEqual(Array.from(incidentIndices(graph,0)), [0,2,2,0]);
  assert.equal(graph.positions.length,9);
});
test('Unicode offsets highlight the exact source occurrence; ambiguity does not guess the first quote', () => {
  assert.deepEqual(codepointSegments('A😀 repeated repeated', [{start:12,end:20}], 'exact'), [{text:'A😀 repeated ',marked:false},{text:'repeated',marked:true}]);
  assert.deepEqual(codepointSegments('same same',[{start:0,end:4}],'ambiguous'),[{text:'same same',marked:false}]);
  assert.throws(() => codepointSegments('x',[{start:0,end:3}],'exact'),/span/i);
});
test('historical selector uses the end of the selected day in the explicit timezone', () => {
  const selected = temporalSelector('known_then', '2026-11-11', 'Europe/Moscow', true, 'separate');
  assert.equal(selected.at,'2026-11-11T20:59:59.999Z');
  assert.equal(selected.history,true);
  assert.equal(temporalSelector('current', '', 'UTC', false, 'only').at,null);
});
test('live guard rejects wrong bases and later knowledge in known-then view', () => {
  assert.equal(canApplyDelta('a',{base_revision:'a',revision:'b'},'current'),true);
  assert.equal(canApplyDelta('a',{base_revision:'z',revision:'b'},'current'),false);
  assert.equal(canApplyDelta('a',{base_revision:'a',revision:'b'},'known_then'),false);
});
test('replacement preserves pinned full text and scroll while status updates independently', () => {
  const card={id:'n',text_revision:'text-a',text:'Old text',scrollTop:800,status:{disposition:'assertion'}};
  const next=pinCard(card,{id:'n',text_revision:'text-b',text:'New text',status:{disposition:'superseded'}});
  assert.equal(next.text,card.text);
  assert.equal(next.text_revision,card.text_revision);
  assert.equal(next.scrollTop,800);
  assert.equal(next.status.disposition,'superseded');
});
