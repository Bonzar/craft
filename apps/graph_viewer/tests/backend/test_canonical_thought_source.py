"""Small canonical fixture through the real disk projection and card functions."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from dataclasses import replace

from apps.graph_viewer.server.cards import edge_card, node_card
from apps.graph_viewer.server.common import selector
from apps.graph_viewer.server.projection import prepare
from apps.graph_viewer.server.providers.canonical_thoughts import source_for_path
from prototypes.assertion_memory.storage import MemoryStore
from prototypes.assertion_memory.thought_store import ThoughtStore


class CanonicalThoughtProjection(unittest.TestCase):
    def test_canonical_only_exact_bases_unknown_link_time_sessions_and_readonly(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); path=root/'memory.sqlite'
            memory=MemoryStore(path); self.addCleanup(memory.close)
            thoughts=ThoughtStore(memory)
            a=thoughts.publish('Original first thought.', thought_id='a', origin={'session_id':'first'})
            thoughts.publish('Second thought.', thought_id='b', origin={'session_id':'second'})
            composite=thoughts.publish('One combined thought.', thought_id='combined',
                metadata={'title':'Сводка по двум записям'}, bases=[{'thought_id':'a','version':1},{'thought_id':'b','version':1}])
            thoughts.publish('Updated first thought.', thought_id='a', expected_version=1, origin=a.origin)
            link=thoughts.connect('a','b','relates to')
            with memory.transaction(write=True) as c:
                thoughts._insert(c,replace(a,version=3,text='Failed legacy audit content.',
                    metadata={'verification':'failed_review'}),activate=False)
                c.execute('CREATE TABLE memory_thought_runtime(singleton INTEGER PRIMARY KEY)')
                c.execute('INSERT INTO memory_thought_runtime VALUES(1)')
                c.execute("INSERT INTO memory_assertions VALUES('legacy','Legacy content','user','test','episode',NULL,'2026-01-01','scope','{}')")
                payload=dict(link); payload.pop('created_at',None)
                c.execute('UPDATE memory_thought_links SET payload=? WHERE id=?',(json.dumps(payload),link['id']))
            with memory.transaction() as c:
                before='\n'.join(c.iterdump())
            source=source_for_path(path,root/'state'); self.addCleanup(source.close)
            graph=prepare(source,root/'cache',selector({}))
            self.assertEqual(graph.manifest['counts']['nodes'],4)
            self.assertEqual(graph.manifest['counts']['edges'],3)
            with graph.connect() as c:
                rows=c.execute('SELECT * FROM nodes ORDER BY ordinal').fetchall()
                cards=[node_card(c,graph,row,{}) for row in rows]
                self.assertEqual({card['entity_type'] for card in cards},{'thought'})
                self.assertNotIn('legacy',{card['id'] for card in cards})
                self.assertNotIn('thought-version:a:3',{card['id'] for card in cards})
                combined=next(card for card in cards if card['thought_ref']['thought_id']==composite.id)
                self.assertEqual(combined['bases'][0]['version'],1)
                self.assertEqual(combined['session_ids'],['first','second'])
                own_topic=next(topic for topic in graph.topics()['topics']
                               if topic['source_ref'].get('source_version_id')==combined['id'])
                self.assertNotIn(own_topic['id'],combined['topic_ids'])
                self.assertEqual(own_topic['full_title'],'Сводка по двум записям')
                original=next(card for card in cards if card['id']=='thought-version:a:1')
                self.assertIn(own_topic['id'],original['topic_ids'])
                edges=[edge_card(c,graph,id=row['id']) for row in c.execute('SELECT id FROM edges')]
                basis=next(edge for edge in edges if edge['relation_type']=='derivation' and edge['target_ref']['thought_id']=='a')
                self.assertEqual(basis['target_ref']['version'],1)
                semantic=next(edge for edge in edges if edge['relation_type']=='semantic')
                self.assertIsNone(semantic['recorded_at'])
                self.assertEqual(semantic['basis'],'relates to')
            historical=prepare(source,root/'cache',selector({'mode':'known_then','at':'2030-01-01T00:00:00Z'}))
            self.assertEqual(historical.manifest['counts']['edges'],2)
            filtered=prepare(source,root/'cache',selector({'session_id':'logical:second'}))
            self.assertEqual(filtered.manifest['counts']['visible_nodes'],3)
            with filtered.connect() as c:
                row=c.execute("SELECT * FROM nodes WHERE id='thought-version:a:1'").fetchone()
                self.assertTrue(node_card(c,filtered,row,{})['session_context_only'])
            with source.connect() as c:
                with self.assertRaises(sqlite3.OperationalError):
                    c.execute('DELETE FROM memory_assertions')
            with memory.transaction() as c:
                self.assertEqual('\n'.join(c.iterdump()),before)
            previous=source.revision()
            thoughts.publish('New canonical thought.', origin={'session_id':'third'})
            self.assertNotEqual(source.revision(),previous)
            updated=prepare(source,root/'cache',selector({}))
            self.assertEqual(updated.manifest['counts']['nodes'],5)
            previous=source.revision()
            thoughts.save_question_check(composite,'Где смотреть сводку?','checked-fixture')
            self.assertNotEqual(source.revision(),previous)
            current=prepare(source,root/'cache',selector({}))
            with current.connect() as c:
                rows=c.execute('SELECT * FROM nodes ORDER BY ordinal').fetchall()
                card=next(node_card(c,current,row,{}) for row in rows
                          if node_card(c,current,row,{})['thought_ref']['thought_id']==composite.id)
                self.assertEqual(card['thought']['questions'],['Где смотреть сводку?'])
            from apps.graph_viewer.server.search import search
            lexical=search(current,{'q':'сводку'})
            self.assertTrue(any(hit['id']==combined['id'] for hit in lexical['hits']))
            from apps.graph_viewer.server import semantic
            class AliasModel:
                identity='fixture:equal-question-inputs'
                def __init__(self):self.calls=[]
                def __call__(self,texts):
                    self.calls.extend(texts)
                    return [[1.,0.] if 'сводку' in text else [0.,1.] for text in texts]
            model=AliasModel()
            semantic.prepare_index(source,current,model)
            self.assertTrue(any('сводку' in text for text in model.calls))
            self.assertEqual(search(current,{'mode':'semantic','q':'сводку'})['hits'][0]['id'],combined['id'])


if __name__=='__main__':
    unittest.main()
