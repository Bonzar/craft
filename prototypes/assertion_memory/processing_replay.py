"""Replay consumed model steps within a retried source/link stage only."""
import hashlib
import json

from .model import ModelError, format_prompt


class ProcessingReplay:
    def __init__(self,queue,claim,provider,binding):
        self.queue,self.claim,self.provider=queue,claim,provider
        self.has_policy_identity=binding is not None
        self.binding=json.dumps(binding,sort_keys=True,ensure_ascii=False)
        identity=json.dumps([claim.job.id,claim.job.phase])
        self.prefix='stage-replay:'+hashlib.sha256(identity.encode()).hexdigest()+':'
        self.pending=None
        self.seen=set()

    def __call__(self,request):
        if not self.has_policy_identity:
            return self.provider(request)  # No stable policy identity for a custom source override.
        # Reaching the next model step means the previous response was consumed.
        # The last response stays provisional until the caller validates it.
        wire=format_prompt(request)
        identity=json.dumps([self.binding,wire,request.cache_context],ensure_ascii=False)
        key=self.prefix+hashlib.sha256(identity.encode()).hexdigest()
        if self.pending is not None and self.pending[0]==key:
            self.pending=None  # Repeated repair input must get a fresh attempt.
        else:
            self.confirm()
        cached=None if key in self.seen else self.queue._refresh_generation(key)
        self.seen.add(key)
        if cached is not None:
            self.queue.renew_claim(self.claim)
            return cached
        result=self.provider(request)
        if format_prompt(request)!=wire:
            raise ModelError('source stage request mutated during generation')
        if isinstance(result,str) and result.strip():
            self.pending=(key,result)
        return result

    def confirm(self):
        if self.pending is not None:
            key,result=self.pending
            self.queue._save_refresh_generation(self.claim,key,result)
            self.pending=None

    def clear(self,connection):
        # Caller already checked ownership in the SAME publication transaction.
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='memory_refresh_generations'").fetchone():
            return
        connection.execute('DELETE FROM memory_refresh_generations WHERE request_key LIKE ?',
                           (self.prefix+'%',))
