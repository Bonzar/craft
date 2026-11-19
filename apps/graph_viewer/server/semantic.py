"""Local FastEmbed query warmup and disk-backed, model-matched vector cache."""
import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path
import numpy as np
from .common import APIError,bounded,cursor_decode,cursor_encode,digest
from .semantic_cache import save_semantic_descriptor
from .search import node_search_documents


class FastEmbedQueryModel:
    def __init__(self,model_name,cache_dir):
        self.model_name=model_name; self.cache_dir=str(cache_dir)
        self.identity='fastembed:'+model_name; self._model=None
        self._lock=threading.Lock()
    def __call__(self,texts):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model=TextEmbedding(model_name=self.model_name,cache_dir=self.cache_dir,threads=2,local_files_only=True)
            return [np.asarray(v,dtype='<f4') for v in self._model.embed(list(texts),batch_size=64)]


def vector_cache(graph):
    path=graph.directory.parent/'semantic-cache.sqlite3'
    c=sqlite3.connect(path,timeout=30)
    c.execute('CREATE TABLE IF NOT EXISTS vectors(backend TEXT,digest TEXT,vector BLOB,PRIMARY KEY(backend,digest))')
    path.chmod(0o600)
    return c


def cached_vectors(source,cache,model,keys):
    found={}
    placeholders=','.join('?' for _ in keys)
    found.update(cache.execute(f'SELECT digest,vector FROM vectors WHERE backend=? AND digest IN ({placeholders})',(model.identity,*keys)))
    missing=[key for key in keys if key not in found]
    if missing:
        with source.connect() as c:
            exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='memory_embedding_cache'").fetchone()
            if exists:
                placeholders=','.join('?' for _ in missing)
                found.update(c.execute(f'SELECT text_digest,vector FROM memory_embedding_cache WHERE backend=? AND text_digest IN ({placeholders})',(model.identity,*missing)))
    return found



def node_vectors(source,cache,model,batch):
    documents=[node_search_documents(row['text'],json.loads(row['body'])['metadata']) for row in batch]
    keys=[digest(items[0]) if len(items)==1 else digest('equal-question-mean/v1:'+json.dumps(items,ensure_ascii=False))
          for items in documents]
    found=cached_vectors(source,cache,model,keys)
    missing=[(key,items) for key,items in zip(keys,documents) if key not in found]
    texts=list(dict.fromkeys(text for _,items in missing for text in items))
    doc_keys=[digest(text) for text in texts]
    vectors=cached_vectors(source,cache,model,doc_keys)
    absent=[(key,text) for key,text in zip(doc_keys,texts) if key not in vectors]
    if absent:
        for (key,_),vector in zip(absent,model([text for _,text in absent])):
            vectors[key]=np.asarray(vector,dtype='<f4').tobytes()
    for key,items in missing:
        values=np.asarray([np.frombuffer(vectors[digest(text)],dtype='<f4') for text in items])
        # Equal contributions keep later questions visible beyond one encoder input window.
        value=values.mean(axis=0).astype('<f4')
        found[key]=value.tobytes()
    cache.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?)',
        ((model.identity,key,value) for key,value in vectors.items()))
    return keys,found,len(missing),len(batch)-len(missing)


def prepare_index(source,graph,model):
    start=time.monotonic()
    warm=np.asarray(model(['query warmup'])[0],dtype='<f4')
    if warm.ndim!=1 or len(warm)==0 or not np.isfinite(warm).all():
        raise ValueError('Invalid embedding query vector')
    count=graph.manifest['counts']['nodes']; dimension=len(warm)
    matrix=np.memmap(graph.directory/'vectors.f32',mode='w+',dtype='<f4',shape=(count,dimension)) if count else np.empty((0,dimension),dtype='<f4')
    reused=0; generated=0
    with graph.connect() as c,vector_cache(graph) as cache:
        rows=c.execute('SELECT ordinal,text,body FROM nodes ORDER BY ordinal')
        while batch:=rows.fetchmany(64):
            keys,found,new,reused_batch=node_vectors(source,cache,model,batch)
            generated+=new; reused+=reused_batch
            for key,row in zip(keys,batch):
                vector=np.frombuffer(found[key],dtype='<f4')
                if len(vector)!=dimension or not np.isfinite(vector).all():
                    raise ValueError('Source vector dimension differs from configured model')
                norm=float(np.linalg.norm(vector))
                matrix[row['ordinal']]=vector/norm if norm else vector
            cache.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?)',((model.identity,key,vector) for key,vector in found.items()))
            graph.manifest['preparation']['semantic']={'status':'preparing','processed':int(batch[-1]['ordinal'])+1,'total':count}
    if count:
        matrix.flush()
    del matrix
    graph.semantic_model=model
    graph.semantic_matrix=np.memmap(graph.directory/'vectors.f32',mode='r',dtype='<f4',shape=(count,dimension)) if count else np.empty((0,dimension),dtype='<f4')
    graph.semantic_info={'status':'ready','ready':True,'model':model.identity,'reused_vectors':reused,'generated_vectors':generated,'dimensions':dimension,'seconds':time.monotonic()-start}
    graph.manifest['preparation']['semantic']=graph.semantic_info
    (graph.directory/'vectors.f32').chmod(0o600) if count else None
    save_semantic_descriptor(graph)
    return graph.semantic_info


def semantic_search(graph,params):
    if not hasattr(graph,'semantic_matrix'):
        status=graph.manifest['preparation'].get('semantic',{}).get('status')
        message='Semantic index is preparing; keyword search and graph are ready' if status=='preparing' else 'Semantic query model or complete index is unavailable'
        raise APIError('semantic_unavailable',message,503)
    query=params['q']; limit=bounded(params.get('limit'),50,200,1)
    binding=f'semantic:{graph.revision}:{digest(query)}'; offset=cursor_decode(params.get('cursor'),binding)
    try:
        vector=np.asarray(graph.semantic_model([query])[0],dtype='<f4')
        norm=float(np.linalg.norm(vector)); vector=vector/norm if norm else vector
        scores=graph.semantic_matrix@vector
    except (ValueError,RuntimeError,OSError):
        raise APIError('semantic_unavailable','Semantic query model is unavailable',503) from None
    with graph.connect() as c:
        mask=4 if graph.manifest['selector']['history'] else 7
        for row in c.execute('SELECT ordinal FROM nodes WHERE flags&?!=0',(mask,)):
            scores[row[0]]=-np.inf
        ordered=np.argsort(-scores,kind='stable')
        eligible=ordered[np.isfinite(scores[ordered])]
        hits=[]
        for ordinal in eligible[offset:offset+limit]:
            row=c.execute('SELECT id,text FROM nodes WHERE ordinal=?',(int(ordinal),)).fetchone()
            hits.append({'id':row['id'],'ordinal':int(ordinal),'score':float(scores[ordinal]),'snippet':row['text'][:420],'highlights':[],'topic_ids':[]})
    return {'revision':graph.revision,'mode':'semantic','query':query,'hits':hits,'next_cursor':cursor_encode(binding,offset+limit) if offset+limit<len(eligible) else None,'index_revision':graph.revision}


def append_index(source,old,graph,model):
    if not hasattr(old,'semantic_matrix'):
        return prepare_index(source,graph,model)
    from .incremental import clone_file
    start=time.monotonic(); dimension=old.semantic_matrix.shape[1]
    count=graph.manifest['counts']['nodes']; previous=old.manifest['counts']['nodes']
    path=graph.directory/'vectors.f32'
    if previous:
        clone_file(old.directory/'vectors.f32',path)
    else:
        path.touch()
    with path.open('r+b') as file:
        file.truncate(count*dimension*4)
    if count:
        matrix=np.memmap(path,mode='r+',dtype='<f4',shape=(count,dimension))
        with graph.connect() as c,vector_cache(graph) as cache:
            rows=c.execute('SELECT ordinal,text,body FROM nodes WHERE ordinal>=? ORDER BY ordinal',(previous,))
            while batch:=rows.fetchmany(64):
                keys,found,_,_=node_vectors(source,cache,model,batch)
                for key,row in zip(keys,batch):
                    vector=np.frombuffer(found[key],dtype='<f4'); norm=float(np.linalg.norm(vector))
                    if len(vector)!=dimension or not np.isfinite(vector).all():
                        raise ValueError('Invalid append embedding')
                    matrix[row['ordinal']]=vector/norm if norm else vector
                cache.executemany('INSERT OR IGNORE INTO vectors VALUES(?,?,?)',((model.identity,key,value) for key,value in found.items()))
        matrix.flush(); del matrix
    graph.semantic_model=model
    graph.semantic_matrix=np.memmap(path,mode='r',dtype='<f4',shape=(count,dimension)) if count else np.empty((0,dimension),dtype='<f4')
    graph.semantic_info=dict(old.semantic_info,incremental=True,seconds=time.monotonic()-start)
    graph.manifest['preparation']['semantic']=graph.semantic_info
    (graph.directory/'vectors.f32').chmod(0o600) if count else None
    save_semantic_descriptor(graph)
    return graph.semantic_info
