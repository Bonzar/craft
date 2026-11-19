"""Black-box HTTP audit against an explicitly configured, ready local viewer.

No server is launched and no browser profile is accessed. Direct SQL is limited
to marked synthetic fixtures by source_oracle.fixture_connection. Password is
read from an environment variable and never written into evidence.
"""
from __future__ import annotations

import argparse
import hashlib
from http.cookiejar import CookieJar
import json
import math
import os
from pathlib import Path
import struct
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, quote, urlsplit
from urllib.request import Request, build_opener, HTTPCookieProcessor
import uuid

from source_oracle import expected_slice, fixture_connection


class Client:
    def __init__(self, base):
        self.base = base.rstrip('/')
        parts = urlsplit(self.base)
        self.origin = f'{parts.scheme}://{parts.netloc}'
        self.cookies = CookieJar()
        self.opener = build_opener(HTTPCookieProcessor(self.cookies))
        self.csrf = None

    def request(self, path, *, method='GET', body=None, csrf=True):
        headers = {'Accept':'application/json', 'Origin':self.origin}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if csrf and self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        request = Request(self.base + '/api/v1' + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=headers)
        started = time.perf_counter()
        try:
            response = self.opener.open(request, timeout=60)
        except HTTPError as error:
            response = error
        raw = response.read()
        result = {'status':response.status,'headers':dict(response.headers),'raw':raw,'ms':(time.perf_counter()-started)*1000}
        if response.headers.get_content_type() == 'application/json':
            result['json'] = json.loads(raw)
        return result

    def login(self, password):
        result = self.request('/session', method='POST', body={'password':password}, csrf=False)
        assert result['status'] == 200, f'login HTTP {result["status"]}'
        self.csrf = result['json'].get('csrf_token')
        assert self.csrf, 'CSRF token missing'
        return result


def graph(client, selector):
    query = urlencode(selector)
    deadline = time.monotonic()+60
    while True:
        response = client.request('/graph?' + query)
        if response['status'] != 202:
            assert response['status'] == 200, f'graph HTTP {response["status"]}'
            return response['json']
        if time.monotonic() >= deadline:
            raise AssertionError('prepared graph not ready within harness readiness wait')
        # This waits for backend readiness only, never counts as a viewer timing pass.
        time.sleep(min(0.5,response['json'].get('retry_after_ms',500)/1000))


def assert_equal(actual, expected, label):
    assert actual == expected, f'{label}: expected {expected!r}, observed {actual!r}'


def audit(base, password, manifest, selected_cases=None):
    results = []
    def run(key, features, operation):
        if selected_cases is not None and key not in selected_cases:
            return
        started = time.perf_counter()
        try:
            details = operation() or {}
            results.append({'case':key,'features':features,'status':details.pop('_status','PASS'),'details':details,'elapsed_ms':round((time.perf_counter()-started)*1000,3)})
            print(json.dumps({'case':key,'status':results[-1]['status']}),file=sys.stderr,flush=True)
        except Exception as error:
            # Error text contains only labels/counts, never private text or credentials.
            results.append({'case':key,'features':features,'status':'FAIL','error':str(error),'elapsed_ms':round((time.perf_counter()-started)*1000,3)})
            print(json.dumps({'case':key,'status':'FAIL','error':str(error)}),file=sys.stderr,flush=True)
    client = Client(base)
    client.login(password)
    second = Client(base)
    second.login(password)
    capabilities = client.request('/capabilities')['json']
    current = graph(client, {'mode':'current','history':0})
    revision = current['revision']
    path_id = lambda value: quote(value,safe='')
    node_path = lambda value: f'/nodes/{path_id(value)}?revision={quote(revision,safe="")}'
    def readonly_fingerprint():
        with fixture_connection(manifest) as db:
            with Path(manifest['database']).open('rb') as source_file:
                file_digest = hashlib.file_digest(source_file, 'sha256').hexdigest()
            return {'source_file_sha256':file_digest,'version':db.execute('SELECT version FROM memory_state').fetchone()[0],
                    'nodes':db.execute('SELECT count(*) FROM memory_assertions').fetchone()[0],
                    'edges':db.execute('SELECT count(*) FROM memory_edges').fetchone()[0]}
    before = readonly_fingerprint()
    def auth_denial():
        anonymous = Client(base)
        sample_node = manifest['first_id']
        sample_edge = manifest['special_edges']['0']
        paths = ['/capabilities','/graph',f'/graph/{revision}/nodes.bin',f'/graph/{revision}/edges.bin',f'/graph/{revision}/topics',node_path(sample_node),f'/graph/{revision}/nodes/0',f'/nodes/{sample_node}/edges?revision={revision}',f'/edges/{sample_edge}?revision={revision}',f'/graph/{revision}/edges/0',f'/sources/{manifest["sources"]["deep_unicode"]["record_id"]}?revision={revision}','/search?'+urlencode({'q':'контроль','mode':'keyword','revision':revision}),f'/events?revision={revision}','/bookmarks']
        for path in paths:
            response = anonymous.request(path)
            assert response['status'] in {401,403}, f'private route failed auth denial: {path.split("?")[0]} HTTP {response["status"]}'
            assert len(response['raw']) < 2048, 'private route unauthorized body unexpectedly large'
            assert isinstance(response.get('json'),dict) and not ({'text','hits','items','topics','nodes'} & response['json'].keys()), 'unauthorized route exposes private schema/content'
        for path,method,body in [('/bookmarks/qa-denied','PUT',{'object_kind':'node','object_id':sample_node,'provider_id':current['provider_id']}),('/bookmarks/qa-denied','DELETE',None)]:
            assert anonymous.request(path,method=method,body=body)['status'] in {401,403}
        return {'private_route_count':len(paths)+2,'no_private_body':True}
    run('AUTH-ALL', ['access_audience'], auth_denial)
    def binary_complete():
        expected = expected_slice(manifest,mode='current',at=current['captured_at'],captured_at=current['captured_at'],explicit_cancellation=True)
        for key,value in expected['counts'].items():
            assert_equal(current['counts'][key],value,'manifest '+key)
        assert current.get('complete_graph') is True
        nodes = client.request(current['nodes']['url'].removeprefix('/api/v1'))
        edges = client.request(current['edges']['url'].removeprefix('/api/v1'))
        assert_equal(nodes['status'],200,'node binary status')
        assert_equal(edges['status'],200,'edge binary status')
        assert_equal(len(nodes['raw']),16*expected['counts']['nodes'],'node byte length')
        assert_equal(len(edges['raw']),8*expected['counts']['edges'],'edge byte length')
        flagged = 0
        for x,y,z,flags in struct.iter_unpack('<fffI',nodes['raw']):
            assert math.isfinite(x) and math.isfinite(y) and math.isfinite(z), 'nonfinite node position'
            flagged += bool(flags&3)
        assert_equal(flagged,expected['counts']['nodes']-expected['counts']['visible_nodes'],'hidden flags')
        n=expected['counts']['nodes']
        assert all(a<n and b<n for a,b in struct.iter_unpack('<II',edges['raw'])), 'out-of-range edge ordinal'
        return {'counts':expected['counts'],'source_ids_sha256':expected['ids_sha256'],'nodes_binary_sha256':hashlib.sha256(nodes['raw']).hexdigest(),'edges_binary_sha256':hashlib.sha256(edges['raw']).hexdigest(),'all_records_scanned':True,'not_rendering_proof':True}
    run('FULL-BINARY',['entry','graph_authority','scale_and_observable_usability'],binary_complete)
    def all_special_cards():
        tested = []
        with fixture_connection(manifest) as db:
            for key in [manifest['first_id'],manifest['last_id'],*manifest['cases'].values()]:
                result = client.request(node_path(key))
                assert_equal(result['status'],200,'card status')
                card = result['json']; assert_equal(card['id'],key,'card id')
                source = db.execute('SELECT text,valid_at,recorded_at FROM memory_assertions WHERE assertion_id=?',(key,)).fetchone()
                text = card['text']; cursor = card.get('next_text_offset')
                while cursor is not None:
                    page = client.request(node_path(key)+f'&offset={cursor}&limit=12000')['json']
                    assert_equal(page['text_offset'],cursor,'text page offset')
                    text += page['text']; next_cursor=page.get('next_text_offset')
                    assert next_cursor is None or next_cursor>cursor,'nonprogressing text cursor'
                    cursor = next_cursor
                assert_equal(hashlib.sha256(text.encode()).hexdigest(),hashlib.sha256(source[0].encode()).hexdigest(),'full assertion text digest')
                assert_equal(card['time']['valid_at'],source[1],'raw valid_at')
                assert_equal(card['time']['recorded_at'],source[2],'raw recorded_at')
                tested.append({'id':key,'ordinal':card['ordinal'],'text_characters':len(text)})
        assert len({row['ordinal'] for row in tested if row['id'] in {manifest['first_id'],manifest['last_id']}})==2
        return {'cards':tested,'full_text_and_raw_time_verified':True}
    run('CARDS-TIMES',['card','graph_authority'],all_special_cards)
    def card_dispositions():
        expected=expected_slice(manifest,mode='current',at=current['captured_at'],captured_at=current['captured_at'],explicit_cancellation=True)
        checked={}
        for name,key in manifest['cases'].items():
            card=client.request(node_path(key))['json']
            status=card['status']
            if key in expected['superseded_ids']:
                assert_equal(status['disposition'],'superseded','effective replacement status')
            elif key in expected['declared_cancelled_ids']:
                assert_equal(status['disposition'],'cancelled','source-published cancellation')
            elif key in expected['conflict_ids']:
                assert status['unresolved_conflict'] is True,'source conflict warning missing'
            elif name in {'conditional_old','cross_scope_old','proposal_old','inference_old'}:
                assert status['disposition']!='superseded','source exception target prematurely hidden'
            checked[name]=status['disposition']
        day=client.request(node_path(manifest['cases']['day_precision']))['json']
        unknown=client.request(node_path(manifest['cases']['unknown_precision']))['json']
        assert_equal(day['time']['valid_precision'],'day','source day precision')
        assert_equal(unknown['time']['valid_precision'],'unknown','unknown precision')
        interpreted=client.request(node_path(manifest['cases']['late_interpretation']))['json']
        available=client.request(node_path(manifest['cases']['late_available']))['json']
        assert_equal(interpreted['time']['interpreted_at'],'2026-01-15T00:00:00.000000+00:00','raw interpretation time')
        assert_equal(available['time']['source_available_at'],'2026-01-15T00:00:00.000000+00:00','raw source availability time')
        return {'dispositions':checked,'raw_precision_and_component_timestamps':True}
    run('CARD-STATUS',['history_default','unresolved_disagreements','card'],card_dispositions)

    def source_topics():
        response=client.request(f'/graph/{revision}/topics')
        assert_equal(response['status'],200,'topics status'); payload=response['json']
        actual={row['id']:row for row in payload['topics']}
        with fixture_connection(manifest) as db:
            definitions={row[0]:json.loads(row[1]) for row in db.execute('SELECT id,definition FROM memory_views')}
        assert_equal(set(actual),{'topic:'+key for key in definitions},'source topic identities')
        for key,definition in definitions.items():
            topic=actual['topic:'+key]
            assert_equal(topic['full_title'],definition['title'],'exact source topic title')
            assert topic['label'].rstrip('…') in definition['title'], 'invented topic label'
        expected_links={('topic:'+parent,'topic:'+child) for parent,definition in definitions.items() for child in definition['depends_on']}
        by_index={row['index']:row['id'] for row in payload['topics']}
        def topic_id(value):
            return by_index[value] if isinstance(value,int) else value
        actual_links={(topic_id(row['parent']),topic_id(row['child'])) for row in payload['links'] if row['relation']=='subquestion'}
        assert_equal(actual_links,expected_links,'source subquestion DAG')
        node=client.request(node_path(manifest['first_id']))['json']
        memberships=[row for row in payload['memberships'] if node['ordinal'] in row['node_indices']]
        assert_equal(len(memberships),4,'shared star memberships')
        assert all(len(row['node_indices'])==len(set(row['node_indices'])) for row in memberships),'duplicate membership ordinals'
        return {'source_topics':len(actual),'source_subquestion_edges':len(actual_links),'shared_unique_star_memberships':4}
    run('SOURCE-TOPICS',['semantic_zoom','many_to_many','graph_authority'],source_topics)
    def complete_adjacency():
        cursor=None; observed=set(); pages=0; declared=None
        while True:
            args={'revision':revision,'limit':200,'visible':1}
            if cursor is not None:args['cursor']=cursor
            response=client.request(f'/nodes/{manifest["first_id"]}/edges?'+urlencode(args))
            assert_equal(response['status'],200,'adjacency status'); payload=response['json']; pages+=1
            for row in payload['items']:
                key=row.get('id',row.get('edge_id'))
                assert key and key not in observed,'duplicate or unidentified incident edge'
                observed.add(key)
            declared=payload.get('visible_total',payload.get('total'))
            next_cursor=payload.get('next_cursor')
            if next_cursor is None:break
            assert next_cursor!=cursor,'nonprogressing incident cursor'
            cursor=next_cursor
        with fixture_connection(manifest) as db:
            expected=db.execute('SELECT count(*) FROM memory_edges WHERE from_id=? OR to_id=?',(manifest['first_id'],manifest['first_id'])).fetchone()[0]
        assert_equal(len(observed),expected,'complete incident edge count')
        assert_equal(declared,expected,'declared incident total')
        assert pages>1,'high-degree fixture did not exercise pagination'
        return {'pages':pages,'incident_edges':len(observed),'cursor_exhausted':True}
    run('ADJACENCY-ALL',['edges','link_reading'],complete_adjacency)

    def edge_fragments():
        ambiguous = client.request(f'/edges/{manifest["special_edges"]["9"]}?revision={revision}')['json']
        exact = client.request(f'/edges/{manifest["special_edges"]["10"]}?revision={revision}')['json']
        assert_equal(ambiguous['to']['fragment']['status'],'ambiguous','repeated quote status')
        assert_equal(exact['from']['fragment']['status'],'exact','unique Unicode quote status')
        fragment=exact['from']['fragment']
        for span in fragment['spans']:
            assert_equal(exact['from']['text'][span['start']:span['end']],fragment['quote'],'codepoint quote')
        return {'ambiguous_target_honest':True,'unicode_codepoints_exact':True}
    run('EDGE-FRAGMENTS',['link_reading'],edge_fragments)
    def focused_source():
        focus = manifest['sources']['deep_unicode']
        result = client.request(f'/sources/{focus["record_id"]}?'+urlencode({'revision':revision,'focus_start':focus['start'],'focus_end':focus['end'],'limit':12000,'context':1}))
        assert_equal(result['status'],200,'source status'); page=result['json']
        assert page['offset']<=focus['start']<page['offset']+len(page['text']), 'focused evidence omitted from initial page'
        with fixture_connection(manifest) as db:
            text=db.execute('SELECT prompt FROM memory_raw_events WHERE id=?',(focus['record_id'],)).fetchone()[0]
        assert_equal(page['text'],text[page['offset']:page['offset']+len(page['text'])],'saved source page')
        assert focus['quote'] in page['text']
        return {'record_id':focus['record_id'],'focus_start':focus['start'],'offset':page['offset'],'exact_saved_text':True}
    run('SOURCE-FOCUS',['source_context_reading'],focused_source)
    def history_matrix():
        values=[]
        for mode in ['known_then','known_now']:
            for at in manifest['temporal'].values():
                for history in [False,True]:
                    selected=graph(client,{'mode':mode,'at':at,'history':int(history),'undated':'separate'})
                    expected=expected_slice(manifest,mode=mode,at=at,history=history,captured_at=selected['captured_at'],explicit_cancellation=True)
                    for key,value in expected['counts'].items():
                        assert_equal(selected['counts'][key],value,f'{mode} {at} history={history} {key}')
                    values.append({'mode':mode,'at':at,'history':history,'counts':expected['counts']})
        return {'selectors':values}
    run('HISTORY-MATRIX',['history_default','history_date_navigation','history_perspectives'],history_matrix)
    def bookmark_two_sessions():
        created=[]
        try:
            for kind,target in [('node',manifest['first_id']),('topic','topic:qa-shared')]:
                key='qa-'+str(uuid.uuid4()); created.append(key)
                result=client.request('/bookmarks/'+key,method='PUT',body={'object_kind':kind,'object_id':target,'provider_id':current['provider_id']})
                assert result['status'] in {200,201}, f'bookmark PUT HTTP {result["status"]}'
                other=second.request('/bookmarks')['json']['items']
                assert any(row['id']==key and row['object_id']==target for row in other),'bookmark absent second independent session'
            denied=client.request('/bookmarks/qa-csrf-denied',method='PUT',body={'object_kind':'node','object_id':manifest['first_id'],'provider_id':current['provider_id']},csrf=False)
            assert denied['status'] in {401,403},'bookmark CSRF missing not denied'
        finally:
            for key in created:
                client.request('/bookmarks/'+key,method='DELETE')
        return {'independent_owner_sessions':2,'node_and_topic':True,'csrf_denial':True}
    run('BOOKMARK-2-SESSIONS',['return_and_deep_links','reading_editing_boundary'],bookmark_two_sessions)
    def search_checks():
        keyword=client.request('/search?'+urlencode({'q':'ДАЛЁКИЙ УЗЕЛ ВСЕЙ ВСЕЛЕННОЙ','mode':'keyword','revision':revision}))
        assert_equal(keyword['status'],200,'keyword search status')
        assert any(hit['id']==manifest['cases']['unaffiliated_sentinel'] for hit in keyword['json']['hits']), 'distant full-graph sentinel not searchable'
        semantic=client.request('/search?'+urlencode({'q':manifest['semantic_search']['query'],'mode':'semantic','revision':revision,'limit':50}))
        available=capabilities['capabilities'].get('semantic_search')
        if not available:
            assert semantic['status'] in {503,501},'semantic unavailable masquerades as success'
            return {'_status':'LIMITATION','keyword':True,'semantic':'explicit unavailable; semantic-search feature not passed'}
        assert_equal(semantic['status'],200,'semantic search status')
        assert any(hit['id']==manifest['semantic_search']['relevant_id'] for hit in semantic['json']['hits']), 'real paraphrase target absent'
        return {'keyword':True,'semantic_paraphrase':True,'http_ms':{'keyword':round(keyword['ms'],3),'semantic':round(semantic['ms'],3)},'not_browser_paint_timing':True}
    run('SEARCH-PUBLIC',['search','graph_authority'],search_checks)
    def readonly_verbs():
        for path in [node_path(manifest['first_id']),f'/edges/{manifest['special_edges']['0']}?revision={revision}',f'/sources/{manifest['sources']['deep_unicode']['record_id']}?revision={revision}','/graph']:
            for method in ['POST','PUT','PATCH','DELETE']:
                response=client.request(path,method=method,body={'text':'QA mutation must be refused'})
                assert response['status'] in {403,404,405}, f'read-only endpoint accepted {method}: HTTP {response["status"]}'
        return {'write_verbs_refused':16}
    run('READONLY-VERBS',['reading_editing_boundary'],readonly_verbs)
    run('SOURCE-UNCHANGED',['reading_editing_boundary'],lambda: (assert_equal(readonly_fingerprint(),before,'source fingerprint') or {'before_after':before}))
    return {'schema':1,'kind':'public_http_contract_audit','synthetic':True,'base_url':base,'source_counts':manifest['counts'],'fixture_digests':manifest['digests'],'source_revision':current['source_revision'],'viewer_revision':revision,'results':results,'not_proven':['rendering/picking','paint timing/FPS','actual Safari or physical phone','mobile internet','live source-ready-to-painted update','real non-fixture private data (not accessed by this synthetic audit)']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--fixture-manifest',type=Path,required=True)
    parser.add_argument('--password-env',default='GRAPH_VIEWER_QA_PASSWORD')
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--case',action='append',dest='cases',help='Run only named cases; repeated values allowed; omitted cases are not claimed')
    args=parser.parse_args()
    password=os.environ.get(args.password_env)
    if not password:
        parser.error('Owner password environment variable is required; never pass password in command arguments')
    manifest=json.loads(args.fixture_manifest.read_text())
    report=audit(args.base_url,password,manifest,selected_cases=args.cases)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    summary={status:sum(row['status']==status for row in report['results']) for status in ['PASS','FAIL','LIMITATION']}
    print(json.dumps({'output':str(args.output),'results':summary}))
    sys.exit(bool(summary['FAIL']))


if __name__=='__main__':
    main()
