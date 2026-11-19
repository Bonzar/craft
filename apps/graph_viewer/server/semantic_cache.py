"""Persist model-bound vector descriptors; validate before restoring a matrix."""
import json
import time
import numpy as np
from .cache_restore import private_file,read_private_json


def save_semantic_descriptor(graph):
    metadata={'schema':1,'model':graph.semantic_info['model'],'dimensions':graph.semantic_info['dimensions'],
              'node_count':graph.manifest['counts']['nodes'],'info':graph.semantic_info}
    temporary=graph.directory/'semantic.json.tmp'
    temporary.write_text(json.dumps(metadata));temporary.chmod(0o600)
    temporary.replace(graph.directory/'semantic.json')


def restore_semantic_index(graph,model):
    start=time.monotonic()
    try:
        metadata=read_private_json(graph.directory/'semantic.json')
        count=graph.manifest['counts']['nodes'];dimension=metadata.get('dimensions')
        if metadata.get('schema')!=1 or metadata.get('model')!=model.identity or metadata.get('node_count')!=count:
            return False
        if type(dimension) is not int or not 0<dimension<=4096:
            return False
        info=metadata.get('info',{})
        if not isinstance(info,dict) or info.get('model')!=model.identity or info.get('dimensions')!=dimension or info.get('status')!='ready':
            return False
        path=graph.directory/'vectors.f32'
        if count and (not private_file(path) or path.stat().st_size!=count*dimension*4):
            return False
        query=np.asarray(model(['query warmup'])[0],dtype='<f4')
        if query.shape!=(dimension,) or not np.isfinite(query).all():
            return False
        matrix=np.memmap(path,mode='r',dtype='<f4',shape=(count,dimension)) if count else np.empty((0,dimension),dtype='<f4')
        for offset in range(0,count,4096):
            if not np.isfinite(matrix[offset:offset+4096]).all():
                return False
        graph.semantic_model=model;graph.semantic_matrix=matrix
        graph.semantic_info=dict(info,restored=True,restore_seconds=time.monotonic()-start)
        graph.manifest['preparation']['semantic']=graph.semantic_info
        return True
    except (OSError,ValueError,TypeError,KeyError,IndexError,AttributeError):
        return False
