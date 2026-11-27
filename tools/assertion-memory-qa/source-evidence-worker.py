import hashlib,json,sys,tempfile
from pathlib import Path
from uuid import uuid4
from prototypes.assertion_memory.capture_batch import authorized_machine_batch,capture,run_next
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.storage import MemoryStore

import re,zlib
KNOWN_ROLES={'tool','user','assistant'}
ROOTS={'source_1','source_2'}
def checked(blob,digest):
 if blob is None or not digest: return None
 decoder=zlib.decompressobj()
 raw=decoder.decompress(blob,512001)
 return raw if decoder.eof and not decoder.unused_data and len(raw)<=512000 and hashlib.sha256(raw).hexdigest()==digest else None

def safe_reason(text):
 if not isinstance(text,str) or not text.strip(): return {}
 lower=text.casefold()
 keys={'omission':r'\b(?:omit\w*|missing|not retained|incomplete)\b',
       'scope':r'\b(?:scope|staging|production|condition|qualification|qualifier)\b',
       'support':r'\b(?:source|grounds?|support|provenance|citation|evidence)\b',
       'adoption':r'\b(?:adopt\w*|approv\w*|cho(?:se|ice)|authoriz\w*)\b',
       'narration':r'\b(?:report\w*|recap|guess|narrat\w*|assistant)\b',
       'duplicate':r'\b(?:duplicat\w*|repeat\w*|already covered)\b'}
 return {'reason_sha256':hashlib.sha256(text.encode()).hexdigest(),
         'reason_length':min(len(text),20000),
         'reason_keyword_tags':[k for k,x in keys.items() if re.search(x,lower)]}

def safe_packet(records,result=None):
 out=[]
 for row in records:
  label=row.get('source')
  if label not in ROOTS:continue
  fragments={}
  owners={}
  for rec in row['records']:
   role=rec.get('origin',{}).get('role')
   if role not in KNOWN_ROLES:continue
   typ=rec.get('role')
   if typ not in ('source','context'):continue
   for f in rec.get('fragments',[]):
    i=f.get('option')
    if type(i) is int:
     fragments[i]=typ
     owners[i]=role
  body=row.get('proposed_thoughts') if result is None else result.get(label)
  body=body if type(body) is dict else {}
  drafts=body.get('thoughts')
  support=[]
  if type(drafts) is list:
   for d in drafts[:32]:
    options=d.get('support_fragments',[]) if type(d) is dict else []
    support.append({'selected_counts':{k:sum(type(i) is int and fragments.get(i)==k for i in options)
                                      for k in ('source','context')},
                    'selected_roles':sorted({owners[i] for i in options if type(i) is int and i in owners})})
  out.append({'source':label,'source_roles':sorted({r.get('origin',{}).get('role') for r in row['records']
                       if r.get('role')=='source' and r.get('origin',{}).get('role') in KNOWN_ROLES}),
              'source_fragment_count':list(fragments.values()).count('source'),
              'draft_count':len(drafts) if type(drafts) is list else None,'draft_support':support})
 return out

def trace_diagnostic(stage,request_digest,response_digest,request_blob,response_blob,*,frozen_tool_case=False):
 try:
  request=checked(request_blob,request_digest); response=checked(response_blob,response_digest)
  if request is None or response is None:return {'trace_exact':False}
  payload=json.loads(request.split(b'\n\n',1)[1]); result=json.loads(response)
  if type(result) is not dict or type(payload) is not dict:return {'trace_exact':False}
  out={'trace_exact':True}
  if stage=='Construct thoughts for multiple source messages':
   out['sources']=safe_packet(payload['records'],result)
   if frozen_tool_case:
    inspect=out['sources']
    drafts=result.get('source_1',{}).get('thoughts',[])
    if len(inspect)==1 and len(drafts)==1 and inspect[0]['source_roles']==['tool']:
     support=inspect[0]['draft_support'][0]
     own=bool(support['selected_roles']==['tool']
         and support['selected_counts']=={'source':inspect[0]['source_fragment_count'],'context':0}
         and inspect[0]['source_fragment_count']==2)
     out['generated_tool_checks']=tool_checks_for(drafts[0]['text'].casefold(),own)
     out['generated_tool_atom_receipt']=frozen_tool_atom_receipt(drafts[0]['text'],own)
  if stage=='Verify source thought batch':
   out['sources']=safe_packet(payload['records'])
   verdict=result.get('verdict')
   if verdict in ('accept','reject'):out['verdict']=verdict
   d=result.get('discard_drafts')
   if type(d) is list:out['discard_count']=len(d)
   out.update(safe_reason(result.get('text')))
  return out
 except (ValueError,TypeError,AttributeError,IndexError,KeyError,zlib.error):
  return {'trace_exact':False}

def tool_checks_for(meaning,grounds):
 import re
 fields={'staging':bool(re.search(r'\bstaging\b',meaning)),
          'observed_validation':bool(re.search(r'\bvalidat\w*\b',meaning)),
          'rejected':bool(re.search(r'\breject\w*\b',meaning)),
          'count_three':bool(re.search(r'\b(?:3|three)\b',meaning)),
          'schema_qualifier':bool(re.search(r'\bschema\b',meaning)),
          'run_identity_if_claimed':not bool(re.search(r'\brun\s+(?!72\b)\d+',meaning)),
          'no_unsupported_production_outcome':not bool(re.search(r'\bproduction\b',re.sub(
              r'\b(?:no production (?:validation|run)(?: was run)?|production (?:was not|wasn\'t) (?:run|validated|checked)|not (?:run|validated|checked) in production)\b','',meaning))),
          'original_tool_ground':grounds}
 return fields


def frozen_tool_scope_atoms(meaning):
    """Fixture-only lexical evidence, no raw output and not the semantic grade."""
    s=meaning.casefold()
    explicit_negative=bool(re.search(
        r"\b(?:no (?:validation|verification|checks?|tests?) (?:was |were )?(?:run |performed |done )?in production|"
        r"no production (?:validation|verification|checks?|tests?)(?: (?:was|were) (?:run|performed|done))?|"
        r"production (?:was not|wasn't|was never) (?:validated|verified|checked|tested)|"
        r"not (?:run|performed|validated|verified|checked|tested) in production)\b",s))
    scope_only=bool(re.search(r'\b(?:staging only|only in staging|limited to staging|restricted to staging)\b',s))
    stripped=re.sub(
        r"\b(?:no (?:validation|verification|checks?|tests?) (?:was |were )?(?:run |performed |done )?in production|"
        r"no production (?:validation|verification|checks?|tests?)(?: (?:was|were) (?:run|performed|done))?|"
        r"production (?:was not|wasn't|was never) (?:validated|verified|checked|tested)|"
        r"not (?:run|performed|validated|verified|checked|tested) in production)\b",'',s)
    runs={int(x) for x in re.findall(r'\brun\s+(\d+)\b',s)}
    return {
        'run_identifier_state':('exact_72' if runs=={72} else 'other_or_conflicting' if runs else 'not_explicit'),
        'production_not_run_explicit':explicit_negative,
        'explicit_staging_only_boundary':scope_only,
        'other_production_reference':bool(re.search(r'\bproduction\b',stripped)),
        'scope_atom_state':('explicit_no_production_run' if explicit_negative else
                          'staging_boundary_only' if scope_only else 'no_explicit_negative_detected'),
    }


def frozen_tool_atom_receipt(meaning,grounds):
    # Reuse prior fields unchanged; independent qualifier diagnostics must not alter acceptance.
    base=tool_checks_for(meaning,grounds)
    return {
        'fixture_expected':{'environment':'staging','tool_run':72,'outcome':'rejected',
                            'count':3,'qualifier':'schema_validation',
                            'production_execution':'not_run'},
        'lexical_atoms':base,
        'coverage_qualifier':frozen_tool_scope_atoms(meaning),
    }



FIXTURES={x['id']:x for x in json.loads(Path(__file__).with_name('source-evidence-fixtures.json').read_text())}
case=FIXTURES[sys.argv[1]]
want_receipt=sys.argv[2:]==['--private-receipt-check']
if sys.argv[2:] and not want_receipt:raise SystemExit('unexpected QA worker option')
fixture_bytes=Path(__file__).with_name('source-evidence-fixtures.json').read_bytes()
if want_receipt:
 from source_evidence_receipt import EXPECTED,FIXTURE_CATALOG_SHA,sha as receipt_sha
 if case['id'] not in EXPECTED or receipt_sha(fixture_bytes)!=FIXTURE_CATALOG_SHA:
  raise SystemExit('not the fixed synthetic evidence allowlist')
with tempfile.TemporaryDirectory(prefix='fictional-evidence-policy-') as scratch:
 path=Path(scratch)/'fixture.sqlite3'
 config=ServiceConfig(db=str(path),provider='codex',model='gpt-6.2-sol',review_model='gpt-6.2-sol',
       reasoning_effort='medium',review_reasoning_effort='low',timeout=100,processing_session_ids=())
 with MemoryStore(str(path)) as store:
  service=config.service(store)
  session=str(uuid4())
  stream=service.ingest_register('fictional-evidence-qa',session)
  messages=[{'session_id':session,'message_id':f'fictional-evidence-{n}',
             'actor_id':('fictional-person' if row['role']=='user' else row['role']),
             'role':row['role'],'text':row['text'],'source':f'fixture://evidence/{n}',
             'delivery':{k:stream[k] for k in ('stream_id','epoch')}|{'seq':n}}
            for n,row in enumerate(case['rows'],1)]
  with authorized_machine_batch():
   accepted=capture(service,'a'*64,messages)
  ids=[x['event']['id'] for x in accepted['receipts']]
  handled=run_next(config,store,lambda:False)
  heads=service.thoughts.list_thoughts()
  p=case['positive']
  recall=0
  prohibited=0
  tool_checks=[]
  for node in heads:
   meaning=node.text.casefold()
   expected_ids=({ids[p['i']]} | ({ids[p['required_ref']]} if 'required_ref' in p else set())) if p else set()
   grounds=bool(p and node.bases and {b.get('event_id') for b in node.bases}==expected_ids
                and all(b.get('thought_id') is None for b in node.bases))
   qualifying=bool(grounds and all(t in meaning for t in p['terms'])
       and all(any(t in meaning for t in choice) for choice in p.get('any',[])))
   forbidden=any(w in meaning for w in case['forbid'])
   if case['id']=='typed_tool_result':
    fields=tool_checks_for(meaning,grounds)
    qualifying=all(fields.values())
    tool_checks.append(fields)
   if qualifying and not forbidden:recall+=1
   else:prohibited+=1
  with store.transaction() as db:
   state,code=db.execute('SELECT state,error_code FROM memory_capture_batches').fetchone()
   trace=db.execute('SELECT c.stage,c.status,c.request_hash,c.response_hash,q.body,a.body '
           'FROM memory_thought_model_calls c '
           'LEFT JOIN memory_thought_model_payloads q ON q.hash=c.request_hash '
           'LEFT JOIN memory_thought_model_payloads a ON a.hash=c.response_hash '
           'ORDER BY c.started_at,c.rowid LIMIT 33').fetchall()
  def digest(x):
   return x if isinstance(x,str) and len(x)==64 and all(y in '0123456789abcdef' for y in x) else None
  allow={'Construct thoughts for multiple source messages','Verify source thought batch','Verify thought'}
  stages=[{'stage':stage if stage in allow else 'other','succeeded':status=='succeeded',
       'request_sha256':digest(q),'response_sha256':digest(a),**trace_diagnostic(stage,q,a,qb,ab,frozen_tool_case=case['id']=='typed_tool_result')}
          for stage,status,q,a,qb,ab in trace[:32]]
  good_trace=bool(stages and len(trace)<=32 and all(x['succeeded'] and x['request_sha256'] and x['response_sha256'] and x['trace_exact'] for x in stages)
          and sum(x['stage']=='Construct thoughts for multiple source messages' for x in stages)==1)
  value={'case':case['id'],'package_state':state if state in ('completed','blocked') else 'other',
         'provider_trace_complete':good_trace,'source_count':len(ids),
         'expected_recall':int(bool(p)),'recall_count':recall,'false_save_count':prohibited,
         'published_count':len(heads),'recall_pass':state=='completed' and recall==int(bool(p)),
         'false_save_pass':prohibited==0,'stages':stages}
  if case['id']=='typed_tool_result':value['published_tool_checks']=tool_checks
  value['outcome']=('pass' if good_trace and value['recall_pass'] and value['false_save_pass']
                    else 'fail' if good_trace else 'inconclusive')
  if want_receipt:
   from source_evidence_receipt import validate_worker_receipt
   from prototypes.assertion_memory.model import ModelRequest,format_prompt
   try:
    exact_rows=[(stage,status,q,a,checked(qb,q),checked(ab,a))
                for stage,status,q,a,qb,ab in trace[:32]]
    value['private_receipt_check']=validate_worker_receipt(case['id'],fixture_bytes,exact_rows,ModelRequest,format_prompt)
   except (ValueError,TypeError,KeyError,IndexError,zlib.error):
    value['private_receipt_check']={'exact_replay_validated':False,'raw_receipt_retained':False}
    value['outcome']='inconclusive'
  print(json.dumps(value))
