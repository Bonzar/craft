"""Create a NEW explicitly synthetic 50-node/34-edge contrast fixture.

No topics are invented: this deliberately exercises canonical stars when the
source does not publish constellation membership. This supplements, never
replaces, the mandatory full 100k/1M fixture.
"""
import argparse
import json
from pathlib import Path
import sqlite3

from fixture import SCHEMA, JAN1, SCOPE, encode, node_id, edge_id, table_digest


def generate(directory):
    directory=Path(directory).absolute();directory.mkdir(parents=True,exist_ok=False)
    (directory/'.viewer-qa-owned').write_text('SYNTHETIC sparse visual fixture; not scale acceptance\n')
    database=directory/'source.sqlite3'
    with sqlite3.connect(database) as db:
        db.executescript(SCHEMA)
        for index in range(50):
            text=f'Синтетическая контрольная запись {index:08d}. Только пример для проверки видимости звёзд и связей.'
            metadata={'fixture':'graph-viewer-qa-v1','visual_profile':'sparse-50-34','temporal_precision':'day'}
            db.execute('INSERT INTO memory_assertions VALUES(?,?,?,?,?,?,?,?,?)',(node_id(index),text,'synthetic-vlad','synthetic-fixture','qa-sparse-session',JAN1,JAN1,SCOPE,encode(metadata)))
        for index in range(34):
            target=(index*13+7)%50
            if target==index:target=(target+1)%50
            db.execute('INSERT INTO memory_edges VALUES(?,?,?,?,?,?)',(edge_id(index),node_id(index),node_id(target),'supports',JAN1,'{}'))
        counts={'nodes':db.execute('SELECT count(*) FROM memory_assertions').fetchone()[0],'edges':db.execute('SELECT count(*) FROM memory_edges').fetchone()[0]}
        assert counts=={'nodes':50,'edges':34}
        assert db.execute('PRAGMA foreign_key_check').fetchall()==[]
        digests={'assertions':table_digest(db,'memory_assertions','*','assertion_id'),'edges':table_digest(db,'memory_edges','*','edge_id')}
    manifest={'schema':1,'synthetic':True,'fixture_id':'graph-viewer-sparse-50-34','database':str(database),'counts':counts,'digests':digests,'topics':{},'source_topics_supplied':False,'scale_qualified':False,'writable_live_probes':False,'first_id':node_id(0),'last_id':node_id(49),'purpose':'Sparse phone star/edge contrast only, supplements mandatory full-scale source.'}
    (directory/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);args=parser.parse_args()
    result=generate(args.directory);print(json.dumps({'manifest':str(args.directory/'manifest.json'),'counts':result['counts'],'scale_qualified':False}))
