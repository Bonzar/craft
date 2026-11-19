"""Editorial labels read only fake in-memory SQLite sources; no engine/provider."""
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from apps.graph_viewer.server.providers.assertion_memory_names import ReadOnlyNames, capture_names, resolve_name
from apps.graph_viewer.server.providers.assertion_memory import AssertionMemorySource
from apps.graph_viewer.server.providers import assertion_memory_topics as topics
from apps.graph_viewer.server.incremental import watermarks, append_compatible

EARLY='2025-01-01T00:00:00.000000+00:00'
LATE='2025-02-01T00:00:00.000000+00:00'
NOW='2025-03-01T00:00:00.000000+00:00'
ACCEPTED='Условия ремонта и распределение расходов между участниками общего проекта'


def fixture(*, journal=True, pending=False, canonical=False, ir=False):
    source=sqlite3.connect(':memory:');source.row_factory=sqlite3.Row
    source.executescript("""CREATE TABLE memory_state(version INTEGER); INSERT INTO memory_state VALUES(7);
        CREATE TABLE memory_assertions(assertion_id TEXT);CREATE TABLE memory_edges(edge_id TEXT);
        CREATE TABLE memory_views(id TEXT,definition TEXT,current_id INTEGER,checked_version INTEGER);
        CREATE TABLE memory_view_versions(id INTEGER,view_id TEXT,snapshot TEXT,created_at TEXT);""")
    for key in ('a','b'):
        query='Какие условия ремонта и распределения расходов согласованы участниками общего проекта?'
        title=ACCEPTED if canonical else query if pending else 'Shared scope overview'
        definition={'id':key,'title':title,'query':query,'scopes':['s'],'depends_on':[]}
        if not ir:definition.update(root_ids=[],max_seeds=8)
        raw=json.dumps(definition,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        source.execute('INSERT INTO memory_views VALUES(?,?,?,?)',(key,raw,1 if key=='a' else 2,7))
        source.execute('INSERT INTO memory_view_versions VALUES(?,?,?,?)',(1 if key=='a' else 2,key,json.dumps({'evidence':[],'created_at':EARLY}),EARLY))
    if journal:
        source.executescript("""CREATE TABLE memory_view_names(id INTEGER PRIMARY KEY,view_id TEXT,definition_sha256 TEXT,title TEXT,recorded_at TEXT,input_fingerprint TEXT,protocol TEXT,provenance TEXT,review TEXT,previous_name_id INTEGER);
            CREATE TABLE memory_view_name_work(view_id TEXT,status TEXT,phase TEXT,attempts INTEGER,error_kind TEXT,error TEXT,available_at TEXT);""")
    return source


def append(source,key,title=ACCEPTED,at=LATE,canonical=False):
    definition=source.execute('SELECT definition FROM memory_views WHERE id=?',(key,)).fetchone()[0]
    sha=hashlib.sha256(definition.encode()).hexdigest()
    provenance={'input_definition_sha256':'provisional' if canonical else sha}
    from prototypes.assertion_memory.view_names import PROTOCOL
    source.execute('INSERT INTO memory_view_names(view_id,definition_sha256,title,recorded_at,'
                   'protocol,provenance,review) VALUES(?,?,?,?,?,?,?)',
                   (key,sha,title,at,PROTOCOL,json.dumps(provenance),json.dumps({'accepted':True})))


def publish(source,mode='current',at=None,**kwargs):
    disk=sqlite3.connect(':memory:')
    disk.executescript('CREATE TABLE memberships(topic INTEGER,node INTEGER,PRIMARY KEY(topic,node));CREATE TABLE overviews(topic INTEGER,id TEXT,text TEXT,text_revision TEXT);')
    try:return topics.publish_topics(source,disk,{}, {'mode':mode,'at':at,'history':True,'undated':'separate'},**kwargs)
    finally:disk.close()


class ViewNamesAdapter(unittest.TestCase):
    def test_current_accepted_name_is_full_and_raw_definition_unchanged(self):
        source=fixture();append(source,'a');before=list(source.iterdump())
        topic=publish(source)['topics'][0]
        self.assertEqual(topic['label'],ACCEPTED)
        self.assertEqual(topic['full_title'],ACCEPTED)
        self.assertEqual(topic['display_title'],ACCEPTED)
        self.assertEqual(topic['definition_title'],'Shared scope overview')
        self.assertEqual(topic['naming_status'],'accepted')
        self.assertEqual(topic['name_recorded_at'],LATE)
        self.assertEqual(topic['name_record_id'],1)
        self.assertEqual(topic['query'],json.loads(source.execute('SELECT definition FROM memory_views LIMIT 1').fetchone()[0])['query'])
        self.assertEqual(list(source.iterdump()),before)

    def test_known_then_obeys_recording_time_before_and_after_name(self):
        source=fixture();append(source,'a')
        before=publish(source,'known_then',EARLY)['topics'][0]
        after=publish(source,'known_then',NOW)['topics'][0]
        self.assertEqual(before['naming_status'],'legacy_unreviewed')
        self.assertEqual(before['label'],'Shared scope overview')
        self.assertEqual(after['label'],ACCEPTED)

    def test_current_old_semantic_version_and_old_valid_date_use_current_name(self):
        source=fixture();append(source,'a')
        for mode,at in [('current',None),('known_now',EARLY)]:
            topic=publish(source,mode,at)['topics'][0]
            self.assertEqual(topic['source_ref']['version_id'],1)
            self.assertEqual(topic['available_at'],EARLY)
            self.assertEqual(topic['label'],ACCEPTED)
            self.assertEqual(topic['name_recorded_at'],LATE)

    def test_single_export_pins_watermark_before_mid_export_append(self):
        source=fixture();append(source,'a');append(source,'b','Первоначальное полное имя')
        original=topics.position;changed=[]
        def rename_during_export(key,*args):
            if not changed:
                append(source,'b','Позднейшее имя',at=LATE);changed.append(True)
            return original(key,*args)
        with patch.object(topics,'position',rename_during_export): result=publish(source)
        self.assertEqual(result['presentation_snapshot']['name_watermark'],2)
        self.assertEqual(result['topics'][1]['label'],'Первоначальное полное имя')
        self.assertEqual(publish(source)['topics'][1]['label'],'Позднейшее имя')

    def test_pending_question_is_full_visible_and_not_accepted(self):
        source=fixture(pending=True)
        result=publish(source);topic=result['topics'][0]
        self.assertEqual(len(result['topics']),2)
        self.assertEqual(topic['label'],topic['query'])
        self.assertEqual(topic['naming_status'],'naming_pending')
        self.assertIsNone(topic['display_title'])
        self.assertIsNone(topic['name_record_id'])

    def test_legacy_without_journal_and_source_neutral_ir_remain_supported(self):
        for ir in (False,True):
            source=fixture(journal=False,ir=ir)
            topic=publish(source)['topics'][0]
            self.assertEqual(topic['naming_status'],'legacy_unreviewed')
            self.assertEqual(topic['full_title'],'Shared scope overview')

    def test_new_canonical_name_does_not_leak_before_record_or_watermark(self):
        source=fixture(canonical=True);append(source,'a',canonical=True)
        for kwargs,mode,at in [({},'known_then',EARLY),({'presentation_snapshot':{'presentation_cutoff':NOW,'name_watermark':0}},'current',None)]:
            topic=publish(source,mode,at,**kwargs)['topics'][0]
            self.assertEqual(topic['label'],topic['query'])
            self.assertIsNone(topic['display_title'])
            self.assertEqual(topic['naming_status'],'naming_pending')

    def test_explicit_saved_as_then_caps_name_but_version_id_alone_does_not(self):
        source=fixture();append(source,'a')
        historical=publish(source,published_at=EARLY)['topics'][0]
        self.assertEqual(historical['naming_status'],'legacy_unreviewed')
        self.assertEqual(publish(source)['topics'][0]['label'],ACCEPTED)

    def test_name_watermark_changes_without_invalidating_semantic_topic_marks(self):
        source=fixture();before=watermarks(source);append(source,'a');after=watermarks(source)
        self.assertEqual(before['state'],after['state'])
        self.assertEqual(before['topics'],after['topics'])
        self.assertEqual((before['presentation'],after['presentation']),(0,1))
        source.execute("INSERT INTO memory_assertions VALUES('new')")
        source.execute('UPDATE memory_state SET version=8')
        changed=watermarks(source)
        before['assertion_membership']=changed['assertion_membership']='same'
        self.assertTrue(append_compatible(before,changed))

    def test_malformed_name_schema_is_reported_instead_of_hidden(self):
        source=fixture(journal=False);source.execute('CREATE TABLE memory_view_names(id INTEGER)')
        with self.assertRaises((sqlite3.DatabaseError,ValueError)):
            publish(source)


    def test_transitional_names_match_reviewed_core_names(self):
        from prototypes.assertion_memory.view_names import presentation
        for canonical in (False,True):
            source=fixture(canonical=canonical);append(source,'a',canonical=canonical)
            append(source,'a','Следующее полное имя',at=LATE,canonical=canonical)
            tables={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            definition=json.loads(source.execute("SELECT definition FROM memory_views WHERE id='a'").fetchone()[0])
            for cutoff,mark in [(LATE,1),(NOW,2)]:
                snapshot={'presentation_cutoff':cutoff,'name_watermark':mark}
                expected,_=presentation(ReadOnlyNames(source),'a',snapshot=snapshot)
                expected={key:expected.get(key) for key in ('display_title','naming_status','name_record_id','name_recorded_at')}
                self.assertEqual(resolve_name(source,tables,'a',definition,snapshot),expected)
                self.assertEqual(resolve_name(source,tables-{'memory_view_name_work'},'a',definition,snapshot),expected)

    def test_transitional_names_ignore_unreviewed_and_unrecognized_protocol_rows(self):
        source=fixture();append(source,'a')
        append(source,'a','Имя без проверки')
        append(source,'a','Имя из другого протокола')
        source.execute("UPDATE memory_view_names SET review='{}' WHERE id=2")
        source.execute("UPDATE memory_view_names SET protocol='old-protocol' WHERE id=3")
        for work in (True,False):
            if not work:
                source.execute('DROP TABLE memory_view_name_work')
            topic=publish(source)['topics'][0]
            self.assertEqual(topic['display_title'],ACCEPTED)
            self.assertEqual(topic['name_record_id'],1)

    def test_definition_mismatch_is_never_used_by_either_schema(self):
        for work in (True,False):
            source=fixture();append(source,'a');source.execute("UPDATE memory_view_names SET definition_sha256='other-definition'")
            if not work:source.execute('DROP TABLE memory_view_name_work')
            self.assertEqual(publish(source)['topics'][0]['naming_status'],'legacy_unreviewed')

    def test_source_revision_changes_on_name_append_without_semantic_version_bump(self):
        source=fixture();source.commit()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.sqlite3'
            with sqlite3.connect(path) as writer:
                source.backup(writer)
                watch=AssertionMemorySource(path)
                try:
                    before=watch.revision()
                    self.assertEqual(watch.revision(),before)
                    append(writer,'a');writer.commit()
                    after=watch.revision()
                    self.assertNotEqual(after,before)
                    self.assertIn(':names:1:',after)
                    self.assertEqual(writer.execute('SELECT version FROM memory_state').fetchone()[0],7)
                finally:
                    watch.close()

    def test_source_reads_work_with_query_only_and_never_construct_engine(self):
        from prototypes.assertion_memory.engine import MemoryEngine
        from prototypes.assertion_memory.storage import MemoryStore
        source=fixture();append(source,'a');source.commit();source.execute('PRAGMA query_only=ON')
        statements=[];source.set_trace_callback(statements.append)
        with patch.object(MemoryEngine,'__init__',side_effect=AssertionError('engine constructor')),patch.object(MemoryStore,'__init__',side_effect=AssertionError('store constructor')):
            self.assertEqual(publish(source)['topics'][0]['label'],ACCEPTED)
        self.assertTrue(all(sql.lstrip().upper().startswith(('SELECT','PRAGMA')) for sql in statements),statements)

    def test_real_source_neutral_ir_has_no_core_dependency_and_preserves_metadata(self):
        from apps.graph_viewer.server.providers.source_ir import populate
        import sys
        import importlib.abc
        class NoCore(importlib.abc.MetaPathFinder):
            def find_spec(self,fullname,path,target=None):
                if fullname.startswith('prototypes.assertion_memory'):
                    raise AssertionError('IR must not import core')
        source=sqlite3.connect(':memory:');source.row_factory=sqlite3.Row
        doc={'schema':'graph-viewer-source/v1','provider_id':'independent','source_revision':'literal','nodes':[],'edges':[],'episodes':[],
             'overviews':[{'id':'external','title':'Полное авторское название источника','question':'Полный вопрос внешнего источника?',
                           'current_version_id':'v1','versions':[{'version_id':'v1','published_at':EARLY,'member_ids':[]}]}]}
        populate(source,doc,'fake-fingerprint')
        guard=NoCore();sys.meta_path.insert(0,guard)
        try:
            with patch.dict(sys.modules,{key:None for key in sys.modules if key.startswith('prototypes.assertion_memory')}):
                topic=publish(source)['topics'][0]
        finally:sys.meta_path.remove(guard)
        self.assertEqual(topic['naming_status'],'legacy_unreviewed')
        self.assertEqual(topic['label'],'Полное авторское название источника')
        self.assertEqual(topic['query'],'Полный вопрос внешнего источника?')
        self.assertEqual(topic['source_ref']['source_version_id'],'v1')



    def assert_corrupt_audit_rejected(self, field):
        from prototypes.assertion_memory.view_names import presentation
        source=fixture();append(source,'a')
        source.execute('UPDATE memory_view_names SET '+field+'=?',('{broken',))
        snapshot={'presentation_cutoff':NOW,'name_watermark':1}
        with self.assertRaises(json.JSONDecodeError):
            presentation(ReadOnlyNames(source),'a',snapshot=snapshot)
        with self.assertRaises(json.JSONDecodeError):
            publish(source,presentation_snapshot=snapshot)
        source.execute('DROP TABLE memory_view_name_work')
        with self.assertRaises(json.JSONDecodeError):
            publish(source,presentation_snapshot=snapshot)

    def test_malformed_accepted_review_fails_in_both_schemas_like_core(self):
        self.assert_corrupt_audit_rejected('review')

    def test_malformed_accepted_provenance_fails_in_both_schemas_like_core(self):
        self.assert_corrupt_audit_rejected('provenance')


if __name__=='__main__':unittest.main()
