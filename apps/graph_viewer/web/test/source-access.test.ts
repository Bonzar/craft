import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sourceObjectAccess, sourceTransport } from '../src/source-access.ts';
test('matching local object IDs never authorize a bookmark from a different source namespace',()=>{
  assert.equal(sourceObjectAccess('source-B','source-A','available'),'foreign_source');
  assert.equal(sourceObjectAccess('source-B','source-B','available'),'available');
  assert.equal(sourceObjectAccess('source-B','source-B','provider_unavailable'),'source_unavailable');
});
test('new source-bound links reject another provider while legacy unqualified links keep current-source behavior',()=>{
  assert.equal(sourceObjectAccess('source-B','source-A'),'foreign_source');
  assert.equal(sourceObjectAccess('source-B','source-B'),'available');
  assert.equal(sourceObjectAccess('source-B',null),'available');
  assert.equal(sourceObjectAccess(null,null),'source_unavailable');
});
test('a saved snapshot cannot subscribe to live updates or be labelled as an updating universe',()=>{
  const snapshot=sourceTransport(false);
  assert.equal(snapshot.subscribe,false);
  assert.equal(snapshot.label,'Сохранённый срез');
  const live=sourceTransport(true);
  assert.equal(live.subscribe,true);
  assert.match(live.label,/Live/);
});
