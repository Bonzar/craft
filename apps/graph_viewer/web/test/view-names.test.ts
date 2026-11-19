import {test} from 'node:test';
import assert from 'node:assert/strict';
import {topicsSchema} from '../src/contracts.ts';
import {topicNamingNotice} from '../src/reader.ts';
const title='Условия ремонта и распределение расходов между участниками общего проекта';
const question='Какие условия ремонта и распределения расходов согласованы участниками общего проекта?';
function page(extra:Record<string,unknown>={}){return {revision:'r1',presentation_snapshot:{presentation_cutoff:'2025-03-01T00:00:00Z',name_watermark:3},topics:[{id:'topic:a',index:0,label:title,full_title:title,label_method:'source_editorial_name',source_ref:{view_id:'a',version_id:1,route_revision:null},position:[0,0,0],radius:90,depth:0,member_count:0,freshness:'current',available_at:'2025-01-01T00:00:00Z',display_title:title,naming_status:'accepted',definition_title:'Shared scope overview',query:question,name_record_id:3,name_recorded_at:'2025-02-01T00:00:00Z',...extra}],memberships:[],links:[]};}
test('topic schema retains full accepted name, raw question and separately pinned editorial epoch',()=>{
  const parsed=topicsSchema.parse(page());const topic=parsed.topics[0]!;
  assert.equal(topic.label,title);assert.equal(topic.full_title,title);assert.equal(topic.display_title,title);
  assert.equal(topic.definition_title,'Shared scope overview');assert.equal(topic.query,question);
  assert.equal(topic.name_record_id,3);assert.equal(topic.name_recorded_at,'2025-02-01T00:00:00Z');
  assert.deepEqual(parsed.presentation_snapshot,{presentation_cutoff:'2025-03-01T00:00:00Z',name_watermark:3});
});
test('pending question remains whole with visible unreviewed status, accepted names have no pending notice',()=>{
  const pending=topicsSchema.parse(page({label:question,full_title:question,display_title:null,naming_status:'naming_pending',name_record_id:null,name_recorded_at:null})).topics[0]!;
  assert.equal(pending.label,question);assert.equal(pending.display_title,null);
  assert.equal(topicNamingNotice(pending),'Название ожидает проверки. Показан полный вопрос.');
  assert.equal(topicNamingNotice({naming_status:'legacy_unreviewed'}),'Название источника без редакционной проверки.');
  assert.equal(topicNamingNotice({naming_status:'accepted'}),null);
});
test('old source-neutral topic response stays compatible without editorial metadata',()=>{
  const raw=page();for(const key of ['display_title','naming_status','definition_title','query','name_record_id','name_recorded_at'])delete raw.topics[0]![key as keyof typeof raw.topics[0]];
  const parsed=topicsSchema.parse(raw).topics[0]!;assert.equal(parsed.display_title,null);assert.equal(parsed.naming_status,'legacy_unreviewed');
});
