import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sourceHierarchy } from '../src/logic.ts';
import { incidentEdgeLabel, sourceTopicMemberships } from '../src/reader.ts';
test('semantic zoom uses only persisted subquestion hierarchy and never treats prerequisites as child themes',()=>{
  const topics=[{id:'topic:a',index:0},{id:'topic:b',index:1},{id:'topic:shared',index:2}];
  const links=[{parent:0,child:1,relation:'prerequisite'},{parent:0,child:2,relation:'subquestion'},{parent:'topic:b',child:'topic:shared',relation:'subquestion'}];
  assert.deepEqual(sourceHierarchy(topics,links),[{parent:'topic:a',child:'topic:shared'},{parent:'topic:b',child:'topic:shared'}]);
});
test('assertion topics retain exactly source memberships and same-revision literal titles in card order',()=>{
  const card={revision:'r1',topic_ids:['second','missing','first']};
  const topics={revision:'r1',topics:[{id:'first',full_title:'Первый вопрос'},{id:'unrelated',full_title:'Посторонняя тема'},{id:'second',full_title:'Второй вопрос'}]};

  const memberships=sourceTopicMemberships(card,topics);

  assert.deepEqual(memberships,[{id:'second',title:'Второй вопрос'},{id:'missing',title:null},{id:'first',title:'Первый вопрос'}]);
  assert.deepEqual(card.topic_ids,['second','missing','first']);
});
test('an old assertion card never borrows titles from another revision, and absent membership remains absent',()=>{
  const topics={revision:'r2',topics:[{id:'same-id',full_title:'Новая тема'}]};

  assert.deepEqual(sourceTopicMemberships({revision:'r1',topic_ids:['same-id']},topics),[{id:'same-id',title:null}]);
  assert.deepEqual(sourceTopicMemberships({revision:'r1',topic_ids:['same-id']}),[{id:'same-id',title:null}]);
  assert.deepEqual(sourceTopicMemberships({revision:'r2',topic_ids:[]},topics),[]);
});
test('incident relation labels explicitly preserve incoming, outgoing and self directions',()=>{
  const outgoing={from_id:'selected',to_id:'other',kind:'supports'};
  const incoming={from_id:'other',to_id:'selected',kind:'supports'};
  const self={from_id:'selected',to_id:'selected',kind:'custom_source_kind'};

  assert.equal(incidentEdgeLabel(outgoing,'selected'),'Исходящая · Подтверждает · other');
  assert.equal(incidentEdgeLabel(incoming,'selected'),'Входящая · Подтверждает · other');
  assert.equal(incidentEdgeLabel(self,'selected'),'С собой · Связь: custom_source_kind · selected');
  assert.deepEqual(incoming,{from_id:'other',to_id:'selected',kind:'supports'});
});
