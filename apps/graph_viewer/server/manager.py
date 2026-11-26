"""Visible asynchronous preparation and bounded live replay journal."""
import asyncio
from collections import deque
import hashlib
import json
import time
import logging
from urllib.parse import urlencode
from .common import APIError,now
from .database_generation import LEGACY

_UNSET=object()
from .bridge_auth import stream_authorized
from .cache_restore import restore_graph
from .live_profile import wall_ms,emitted,first_sse_yield
from .projection import prepare
from .providers.base import membership_checked
from .incremental import incremental
from .layout import VERSION
from .cache_retention import prune_cache, INTERVAL_SECONDS
from .presentation_refresh import policy_current,inherit_semantic
from .providers.assertion_memory_topics import topic_policy_current


class GraphManager:
    def __init__(self, source, cache, semantic_model=None):
        self.source,self.cache=source,cache
        self._generation=self.read_generation(); self._epoch_tasks=set()
        self.semantic_model=semantic_model
        self.semantic_jobs=set(); self.semantic_gate=asyncio.Semaphore(1)
        self.graphs={}; self.preparing={}; self.errors={}; self.latest={}; self.observed={}
        self.live_profiles=deque(maxlen=16)
        self.journal=deque(maxlen=256); self.cursor=0; self.closed=False
        self.last_observed_at=None; self.available=True; self._poller=None; self._loop=None; self._cleaner=None

    def key(self, selection):
        return json.dumps(selection,sort_keys=True)

    def read_generation(self):
        reader=getattr(self.source,'database_generation',None)
        if reader is None:
            return None
        try:
            return reader()
        except Exception as error:
            raise APIError('source_unavailable','Source generation could not be verified',503) from error

    def generation_cache(self,generation):
        if generation is None or generation[2]==LEGACY:
            return self.cache
        name=hashlib.sha256(json.dumps(generation).encode()).hexdigest()
        directory=self.cache/'database-epochs'/name
        for path in (self.cache,self.cache/'database-epochs',directory):
            path.mkdir(mode=0o700,exist_ok=True)
            if path.is_symlink() or not path.is_dir():
                raise ValueError('Untrusted Graph cache directory')
        return directory

    def sync_generation(self):
        generation=self.read_generation()
        if generation!=self._generation:
            old=self._generation
            if old is not None and old[2]!=LEGACY and generation[2]==LEGACY:
                raise APIError('source_unavailable','Database reset fence disappeared',503)
            self._generation=generation
            self.graphs.clear(); self.latest.clear(); self.errors.clear(); self.observed.clear()
            # Threads already executing cannot be canceled safely. Their epoch
            # paths remain unreachable and their publication checks will fail.
            self.preparing.clear(); self.journal.clear(); self.live_profiles.clear()
            if old is not None:
                self.emit('reset',{'revision':'','reason':'database_cleared','manifest_url':'/api/v1/graph'})
        return generation

    def generation_matches(self,generation):
        return self.read_generation()==generation

    def schedule(self,key,work):
        task=asyncio.create_task(work)
        self.preparing[key]=task
        self._epoch_tasks.add(task)
        task.add_done_callback(self._epoch_tasks.discard)

    def finish(self,key):
        if self.preparing.get(key) is asyncio.current_task():
            self.preparing.pop(key,None)

    def get(self, revision):
        generation=self._generation
        if not self.generation_matches(generation):
            raise APIError('revision_expired','Database generation changed; reload the graph',409)
        if revision not in self.graphs:
            graph=restore_graph(self.generation_cache(generation),revision,self.source.provider_id)
            if graph is None:
                raise APIError('revision_expired','Snapshot revision is unavailable; reload the graph',409)
            graph.manifest['live_cursor']=str(self.cursor)
            if not self.generation_matches(generation):
                raise APIError('revision_expired','Database generation changed; reload the graph',409)
            self.graphs.setdefault(revision,graph)
            if self._loop is not None and not self.closed:
                self._loop.call_soon_threadsafe(self.schedule_semantic,self.graphs[revision])
        return self.graphs[revision]

    async def ensure(self,selection):
        generation=self.sync_generation()
        key=self.key(selection)
        if key in self.latest:
            return self.graphs[self.latest[key]]
        if key in self.errors:
            if isinstance(self.errors[key],APIError):
                raise self.errors[key]
            raise APIError('source_unavailable','Source snapshot could not be prepared',503)
        if key not in self.preparing:
            self.schedule(key,self.build(selection,key,generation))
        return None

    async def build(self,selection,key,generation):
        try:
            graph=await asyncio.to_thread(prepare,self.source,self.generation_cache(generation),selection)
            if self.sync_generation()!=generation:
                return None
            self.graphs[graph.revision]=graph; self.latest[key]=graph.revision
            self.schedule_semantic(graph)
            graph.manifest['live_cursor']=str(self.cursor)
            self.errors.pop(key,None)
            return graph
        except Exception as error:
            logging.getLogger(__name__).warning('snapshot_preparation_failed mode=%s error_type=%s',selection['mode'],type(error).__name__)
            if generation==self._generation:
                self.errors[key]=error if isinstance(error,APIError) else True
            return None
        finally:
            self.finish(key)

    def schedule_semantic(self,graph,old=None):
        if self.graphs.get(graph.revision) is not graph:
            return
        if graph.manifest['selector'].get('session_id') is not None:
            # A viewer filter never triggers embedding work. Share only an exact
            # unfiltered snapshot family whose canonical ordinal identities match.
            family={key:value for key,value in graph.manifest['selector'].items() if key not in ('session_id','run_id','output_snapshot_id')}
            for parent in self.graphs.values():
                if parent.manifest['selector']!=family or parent.manifest['source_revision']!=graph.manifest['source_revision'] or parent.manifest['counts']['nodes']!=graph.manifest['counts']['nodes']:
                    continue
                with parent.connect() as left,graph.connect() as right:
                    same=all(a[0]==b[0] for a,b in zip(left.execute('SELECT id FROM nodes ORDER BY ordinal'),right.execute('SELECT id FROM nodes ORDER BY ordinal')))
                if same:
                    inherit_semantic(graph,parent)
                    break
            graph.manifest['session_filter']['semantic_search_available']=hasattr(graph,'semantic_matrix')
            return
        if self.semantic_model is None:
            return
        graph.manifest['preparation']['semantic']={'status':'preparing','processed':0,'total':graph.manifest['counts']['nodes']}
        task=asyncio.create_task(self.semantic(graph,old))
        self.semantic_jobs.add(task)
        task.add_done_callback(self.semantic_jobs.discard)

    async def semantic(self,graph,old=None):
        if self.semantic_model is None or graph.manifest['selector'].get('session_id') is not None:
            return
        profile=getattr(graph,'live_profile',None);started=time.monotonic()
        if profile is not None:
            profile['semantic_started_at_ms']=wall_ms()
        try:
            from .semantic import prepare_index,append_index
            from .semantic_cache import restore_semantic_index
            async with self.semantic_gate:
                if old is None and await asyncio.to_thread(restore_semantic_index,graph,self.semantic_model):
                    return
                if old is not None:
                    await asyncio.to_thread(append_index,self.source,old,graph,self.semantic_model)
                else:
                    await asyncio.to_thread(prepare_index,self.source,graph,self.semantic_model)
        except Exception as error:
            logging.getLogger(__name__).warning('semantic_preparation_failed error_type=%s',type(error).__name__)
            graph.manifest['readiness'].append('semantic_index_unavailable')
            graph.manifest['preparation']['semantic']={'status':'unavailable'}
        finally:
            for child in list(self.graphs.values()):
                if getattr(child,'semantic_parent',None) is graph:
                    inherit_semantic(child,graph)
                    if child.manifest['selector'].get('session_id') is not None:
                        child.manifest['session_filter']['semantic_search_available']=hasattr(child,'semantic_matrix')
            if profile is not None:
                profile['semantic_completed_at_ms']=wall_ms()
                profile['semantic_async_ms']=round((time.monotonic()-started)*1000,3)

    def emit(self,event,payload):
        self.cursor+=1
        payload=dict(payload,cursor=str(self.cursor))
        self.journal.append((self.cursor,event,payload))

    async def poll(self):
        while not self.closed:
            await asyncio.sleep(.35)
            try:
                generation=self.sync_generation()
                revision=await asyncio.to_thread(self.source.revision)
                if self.sync_generation()!=generation:
                    continue
                self.last_observed_at=now()
                if not self.available:
                    self.available=True
                    self.emit('heartbeat',{'source_available':True,'last_observed_at':self.last_observed_at})
                for key,old in list(self.latest.items()):
                    selection=json.loads(key)
                    clock=self.graphs[old].manifest.get('next_status_at')
                    due=clock is not None and clock<=now()
                    if selection['mode']=='known_then' or key in self.preparing or (self.observed.get(key,self.graphs[old].manifest['source_revision'])==revision and not due and not self.graphs[old].manifest.get('preparation',{}).get('presentation_pending') and self.graphs[old].manifest.get('layout',{}).get('version')==VERSION and policy_current(self.graphs[old].manifest) and topic_policy_current(self.graphs[old].manifest) and membership_checked(self.graphs[old].manifest,getattr(self.graphs[old],'watermarks',None))):
                        continue
                    self.schedule(key,self.refresh(selection,key,old,wall_ms(),generation))
            except Exception:
                if self.available:
                    self.available=False
                    self.emit('unavailable',{'last_good_revision':next(iter(self.latest.values()),None),'since':now(),'message':'Source generation unavailable; retained snapshots cannot be verified'})

    async def refresh(self,selection,key,old,detected_at=None,generation=_UNSET):
        if generation is _UNSET:
            generation=self._generation
        profile={'detected_at_ms':detected_at if detected_at is not None else wall_ms()}
        self.live_profiles.append(profile);started=time.monotonic()
        try:
            result=await asyncio.to_thread(incremental,self.source,self.graphs[old],self.generation_cache(generation),selection)
            if self.sync_generation()!=generation:
                return
            profile['incremental_worker_ms']=round((time.monotonic()-started)*1000,3)
            if result:
                graph,payload=result
                if graph is None:
                    self.observed[key]=payload['observed_source_revision'];return
                graph.live_profile=profile;profile['phases_ms']=graph.manifest['preparation'].get('phases_ms',{})
                self.graphs[graph.revision]=graph; self.latest[key]=graph.revision
                if not graph.manifest['preparation'].get('presentation_only'):self.schedule_semantic(graph,self.graphs[old])
                self.emit('delta',dict(payload,base_revision=old,revision=graph.revision,source_revision=graph.manifest['source_revision'],captured_at=graph.manifest['captured_at']))
                graph.manifest['live_cursor']=str(self.cursor)
                emitted(profile,self.cursor)
            else:
                graph=await self.build(selection,key,generation)
                if graph:
                    graph.live_profile=profile
                    self.emit('reset',{'revision':graph.revision,'reason':'history_changed','manifest_url':manifest_url(selection)})
                    graph.manifest['live_cursor']=str(self.cursor)
                    emitted(profile,self.cursor)
        finally:
            profile['refresh_total_ms']=round((time.monotonic()-started)*1000,3)
            self.finish(key)

    async def events(self,request):
        raw=request.headers.get('last-event-id') or request.query_params.get('after','0')
        try:
            after=int(raw)
        except ValueError:
            raise APIError('invalid_cursor','Invalid live cursor') from None
        revision=request.query_params.get('revision')
        selection=self.get(revision).manifest['selector'] if revision else None
        generation=self._generation
        yield self.encode('hello',{'cursor':str(self.cursor),'revision':revision,'captured_at':now()})
        heartbeat=time.monotonic()
        while not self.closed:
            if await request.is_disconnected():
                break
            if hasattr(request,'app') and not await asyncio.to_thread(stream_authorized,request):
                break
            try:
                if self.sync_generation()!=generation:
                    yield self.encode('reset',{'cursor':str(self.cursor),'revision':revision or '','reason':'database_cleared','manifest_url':manifest_url(selection) if selection else '/api/v1/graph'})
                    break
            except APIError:
                break
            if self.journal and after<self.journal[0][0]-1 and (not selection or selection['mode']!='known_then'):
                yield self.encode('reset',{'cursor':str(self.cursor),'revision':revision,'reason':'cursor_expired','manifest_url':'/api/v1/graph'})
                after=self.cursor
            for cursor,event,payload in list(self.journal):
                if cursor>after:
                    after=cursor
                    if self.applies(event,payload,selection):
                        first_sse_yield(self.live_profiles,cursor)
                        yield self.encode(event,payload)
            if time.monotonic()-heartbeat>5:
                yield self.encode('heartbeat',{'cursor':str(self.cursor),'source_available':self.available,'last_observed_at':self.last_observed_at})
                heartbeat=time.monotonic()
            await asyncio.sleep(.15)

    def applies(self,event,payload,selection):
        if event=='reset' and payload.get('reason')=='database_cleared':
            return True
        if event not in {'delta','reset'} or selection is None:
            return True
        if selection['mode']=='known_then':
            return False
        target=self.graphs.get(payload.get('revision'))
        return target is not None and target.manifest['selector']==selection

    @staticmethod
    def encode(event,payload):
        return f'id: {payload["cursor"]}\nevent: {event}\ndata: {json.dumps(payload)}\n\n'

    async def clean_cache(self):
        while not self.closed:
            try:
                # Pending builds and embeddings can still use previous generations.
                if not self._epoch_tasks and not self.semantic_jobs:
                    retained=set(self.latest.values())
                    for _,_,payload in self.journal:
                        retained.update(payload.get(k) for k in ('revision','base_revision') if payload.get(k))
                    paths={graph.directory for revision,graph in self.graphs.items() if revision in retained}
                    paths.add(self.source.path.parent)
                    prune_cache(self.cache, paths)
                    self.graphs={revision:graph for revision,graph in self.graphs.items()
                                 if revision in retained or graph.directory.exists()}
            except Exception:
                logging.getLogger(__name__).exception('cache_expiry_failed')
            await asyncio.sleep(INTERVAL_SECONDS)

    async def start(self):
        self.sync_generation()
        self._loop=asyncio.get_running_loop()
        self._poller=asyncio.create_task(self.poll())
        self._cleaner=asyncio.create_task(self.clean_cache())

    async def stop(self):
        self.closed=True
        if self._cleaner:
            self._cleaner.cancel()
            await asyncio.gather(self._cleaner,return_exceptions=True)
        if self._poller:
            self._poller.cancel()
            try:
                await self._poller
            except asyncio.CancelledError:
                pass
        await asyncio.gather(*list(self._epoch_tasks),return_exceptions=True)
        await asyncio.gather(*list(self.semantic_jobs),return_exceptions=True)
        self.source.close()


def manifest_url(selection):
    values={k:('1' if v else '0') if isinstance(v,bool) else v for k,v in selection.items() if v is not None}
    return '/api/v1/graph?'+urlencode(values)


def parse_replay_cursor(request):
    import re
    raw=request.headers.get('last-event-id') or request.query_params.get('after','0')
    if not re.fullmatch(r'0|[1-9][0-9]{0,19}',raw):
        raise APIError('invalid_cursor','Invalid live replay cursor')
    return int(raw)
