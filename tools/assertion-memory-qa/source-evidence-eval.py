"""Compare standalone C with one immutable Python Git baseline; fictional QA only."""
import argparse,hashlib,json,re
import os,shutil,subprocess,sys,tempfile
from pathlib import Path
HEAD='1c022bfdd3d728d94cb5ea77a68970ca4d7348e2'
DIALOGUE='prototypes/assertion_memory/dialogue.py'
BATCH='prototypes/assertion_memory/source_model_batch.py'
STOCK_DIALOGUE='2abf284d5b5adc66e8edc8ea7fdbc8b0329537eb7b8242a45eddb44295cee377'
STOCK_GATE='a10f753ac19b99053e62523fd77a04c83a5031e6defe454a513380947cb24a86'
CANDIDATE='7226ea25ce82ad3001445ee4888b1d0a69950d65d191431087a5e05a23b3477f'
HUNK="diff --git a/prototypes/assertion_memory/dialogue.py b/prototypes/assertion_memory/dialogue.py\n--- a/prototypes/assertion_memory/dialogue.py\n+++ b/prototypes/assertion_memory/dialogue.py\n@@ -88,7 +88,12 @@\n     'not endorsement of the proposer\\'s reason or premise and not the user\\'s motive. Preserve explicit '\n     'rejection, disagreement or uncertainty only where it changes the admitted meaning or confidence. Quoting, referring or asking '\n     'about a proposal never by itself adopts it. A sequence or prerequisite is not automatically a reason '\n-    'for a selected value. Retain only material referent closure, not unrelated neighboring assistant '\n+    'for a selected value. Separate the evidence that establishes a claim from context needed only to '\n+    'identify what it refers to. For an explicit choice that is complete in the user’s own words, do not use '\n+    'a proposer’s separate reasoning as supporting grounds or require it as part of the choice. For an actual '\n+    'reference-dependent adoption, retain the selecting user evidence and the specific antecedent needed '\n+    'to identify the chosen option; keep any material proposer-attributed premise distinct from the user’s '\n+    'choice or verified fact. Retain only material referent closure, not unrelated neighboring assistant '\n     'proposals or every contextual sentence. Unknown or ambiguous antecedents remain unresolved; recover '\n     'needed exact context using the existing bounded requests, never guess. ')\n _QUESTION = re.compile(r'^(?:а\\s+)?(?:что|как|когда|где|куда|кто|почему|зачем|сколько|како\\w*|'\n"
def sha(b):return hashlib.sha256(b).hexdigest()
CLOSURE='6ed6d7b29f5fefcbb389133ba54f47c14a19961362b65bd17601a43eff4288d2'
ROOT='prototypes/assertion_memory/'


def git_sources(root):
 root=root.resolve(strict=True)
 env={**os.environ,'GIT_OPTIONAL_LOCKS':'0','GIT_CONFIG_NOSYSTEM':'1',
      'GIT_CONFIG_GLOBAL':'/dev/null','GIT_TERMINAL_PROMPT':'0','GIT_NO_LAZY_FETCH':'1',
      'GIT_NO_REPLACE_OBJECTS':'1'}
 for key in ('GIT_DIR','GIT_WORK_TREE','GIT_COMMON_DIR','GIT_INDEX_FILE','GIT_ALTERNATE_OBJECT_DIRECTORIES',
             'GIT_CONFIG','GIT_CONFIG_SYSTEM','GIT_CONFIG_COUNT','GIT_CONFIG_PARAMETERS'):
    env.pop(key,None)
 def git(*args):
    return subprocess.run(['/usr/bin/git','--no-pager','-C',str(root),*args],env=env,
                          stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,check=True,timeout=10).stdout
 if git('rev-parse','--verify',HEAD+'^{commit}').strip().decode('ascii')!=HEAD:
    raise ValueError('wrong pinned Git revision')
 names={}
 for row in git('ls-tree','-rz',HEAD,'--',ROOT).split(b'\x00'):
    if not row:continue
    meta,raw=row.split(b'\t',1)
    f=raw.decode('ascii')
    parts=f.split('/')
    base=parts[-1]
    if (not f.endswith('.py') or 'tests' in parts or base.startswith('test_')
            or base.endswith('_test.py') or base=='offline_test_process.py'):
       continue
    if not f.startswith(ROOT) or not re.fullmatch(r'[a-zA-Z0-9_/]+\.py',f) or f in names:
       raise ValueError('unexpected source path')
    mode,kind,oid=meta.split(b' ')
    if mode!=b'100644' or kind!=b'blob' or not re.fullmatch(b'[0-9a-f]{40}',oid):
       raise ValueError('unexpected tracked source kind')
    names[f]=oid.decode('ascii')
 if len(names)!=156:raise ValueError('unexpected pinned source count')
 old={}
 for n,oid in sorted(names.items()):
    body=git('cat-file','blob',oid)
    if len(body)>2_000_000:raise ValueError('invalid source blob')
    old[n]=body
 inventory=b''.join(n.encode('ascii')+b'\x00'+sha(body).encode('ascii')+b'\n'
                    for n,body in sorted(old.items()))
 if sha(inventory)!=CLOSURE:raise ValueError('source closure differs from pinned comparison baseline')
 return finalize(old)


def finalize(old):
 if len(old)!=156 or sha(old[DIALOGUE])!=STOCK_DIALOGUE or sha(old[BATCH])!=STOCK_GATE:
  raise ValueError('unexpected assembly')
 before='\n'.join(l[1:] for l in HUNK.splitlines() if l.startswith('-') and not l.startswith('---'))
 after='\n'.join(l[1:] for l in HUNK.splitlines() if l.startswith('+') and not l.startswith('+++'))
 s=old[DIALOGUE].decode()
 if s.count(before)!=1:raise ValueError('standalone C hunk no longer applies')
 candidate=s.replace(before,after).encode()
 if sha(candidate)!=CANDIDATE:raise ValueError('unexpected C candidate')
 compile(candidate,DIALOGUE,'exec')
 return old,{**old,DIALOGUE:candidate}

CONTROLS=("direct_user_decision","typed_tool_result","unsupported_assistant_report","scoped_user_adoption","scoped_adoption_needs_referent","scoped_adoption_question_only","wrong_source_report","frozen_rowan")
QA_DIR=Path(__file__).resolve().parent


def isolated_once(sources,case,private_receipt_check=False):
 with tempfile.TemporaryDirectory(prefix='qa-038-standalone-c-',dir='/tmp') as work:
  root=Path(work)
  for n,body in sources.items():
   p=root/n
   p.parent.mkdir(parents=True,exist_ok=True)
   p.write_bytes(body)
  job=root/'synthetic_case.py'
  shutil.copyfile(QA_DIR/'source-evidence-worker.py',job)
  shutil.copyfile(QA_DIR/'source-evidence-fixtures.json',root/'source-evidence-fixtures.json')
  if private_receipt_check:
   shutil.copyfile(QA_DIR/'source-evidence-receipt.py',root/'source_evidence_receipt.py')
  proc=subprocess.run([sys.executable,'-I','-B','-c',
             "import runpy,sys; sys.path.insert(0,sys.argv[1]); sys.argv=sys.argv[2:]; runpy.run_path(sys.argv[0],run_name='__main__')",
             work,str(job),case,*(['--private-receipt-check'] if private_receipt_check else [])],cwd=root,
             env={k:v for k,v in os.environ.items() if k not in ('PYTHONPATH','PYTHONHOME')},
             capture_output=True,timeout=300)
  if proc.returncode:
    return {'outcome':'inconclusive','stop_reason':'fixture_child_nonzero','child_exit':proc.returncode}
  value=json.loads(proc.stdout)
  if value.get('case')!=case:raise ValueError('unbound result')
  return value


def main():
 p=argparse.ArgumentParser()
 p.add_argument('--git-root',type=Path,required=True)
 p.add_argument('--variant',choices=('stock','standalone_C'))
 p.add_argument('--case',choices=CONTROLS)
 p.add_argument('--run-one',action='store_true')
 p.add_argument('--private-receipt-check',action='store_true',help='Validate exact stage binding for two frozen fictional cases, with no raw output')
 opts=p.parse_args()
 if opts.run_one!=(bool(opts.variant) and bool(opts.case)) or bool(opts.variant)!=bool(opts.case):
    p.error('preflight has no case/variant; run-one requires one variant and one case')
 if opts.private_receipt_check and (not opts.run_one or opts.case not in ('typed_tool_result','frozen_rowan')):
    p.error('private evidence is restricted to one explicitly selected frozen case')
 a,b=git_sources(opts.git_root)
 changed=[n for n in a if a[n]!=b[n]]
 if changed!=[DIALOGUE]:raise ValueError('not a standalone prompt delta')
 proof={'release':HEAD,'sources':len(a),'changed_if_candidate':changed,'python_closure_sha256':CLOSURE,
        'candidate_dialogue':CANDIDATE,'source_custody':'immutable_git_source'}
 compile((QA_DIR/'source-evidence-worker.py').read_bytes(),'preflight_contrast','exec')
 fixtures=json.loads((QA_DIR/'source-evidence-fixtures.json').read_bytes())
 if [row['id'] for row in fixtures]!=list(CONTROLS):
    raise ValueError('unexpected synthetic fixture catalog')
 if opts.variant is None:
  print(json.dumps({**proof,'outcome':'preflight_only','model_calls':0,'cases':CONTROLS}))
  return 0
 which=a if opts.variant=='stock' else b
 result=isolated_once(which,opts.case,opts.private_receipt_check)
 print(json.dumps({**proof,'variant':opts.variant ,'provider':'configured-native-codex',**result}))
 return 0 if result['outcome']=='pass' else 2


if __name__=='__main__':
 raise SystemExit(main())
