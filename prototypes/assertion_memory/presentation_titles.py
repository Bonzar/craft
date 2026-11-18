"""Bounded visual-only pair codec; never part of semantic material identities."""
from __future__ import annotations
import hashlib
import json
from .model import ModelError

PAIR_PROTOCOL='overview-display-pair-v1'
EDITORIAL_POLICY='overview-display-review-v1'
MAX_TITLE_CHARACTERS=80
MAX_TITLE_WORDS=12
MAX_NAMING_SUMMARY_CHARACTERS=6000
CAPTION_POLICY='meaningful-caption-projection-v2'


def label_fits(value):
 """The shared Unicode visual budget; this grants no editorial approval."""
 return (isinstance(value,str) and bool(value.strip()) and len(value)<=MAX_TITLE_CHARACTERS
         and len(value.split())<=MAX_TITLE_WORDS and not any(ord(c)<32 for c in value)
         and '…' not in value and '...' not in value)


def model_projection(context):
 """Bound semantic input independently of reference/source cardinality."""
 from .view_names import _digest
 question=context['question']
 keys=('purpose','target_version_id','semantic_content_sha256','input_definition_sha256',
       'caption_policy','summary','summary_complete','summary_sha256')
 result={key:context[key] for key in keys if key in context}
 if 'summary' in result:result['summary']=result['summary'][:MAX_NAMING_SUMMARY_CHARACTERS]
 result.update(question=question[:8000],question_complete=len(question)<=8000,
     question_sha256=sha(question),context_sha256=_digest(context),input_fingerprint=_digest(context),
     reference_manifest_sha256=_digest(context['references']),reference_count=len(context['references']))
 return result


def sha(text):return hashlib.sha256(text.encode()).hexdigest()
def title(value,question):
 from .view_names import _title
 result=_title(value,question)
 if not label_fits(result):raise ModelError('name must be a short complete visual phrase')
 return result

def unpack(raw,question):
 """A legacy plain body is honest pending; title invalidity cannot lose meaning."""
 candidate=None
 try:value=json.loads(raw)
 except (ValueError,TypeError):
  if isinstance(raw,str) and raw.lstrip().startswith('{"summary"'):raise ValueError('malformed summary/name envelope')
  return raw,None,'legacy_plaintext'
 if not isinstance(value,dict) or not {'summary','display_title'}.intersection(value):return raw,None,'legacy_plaintext'
 if not isinstance(value.get('summary'),str):raise ValueError('pair summary must be text')
 try:candidate=title(value.get('display_title'),question)
 except ModelError:pass
 return value['summary'],candidate,'paired' if candidate else 'title_invalid'

def editorial_record(candidate,summary):
 return {'role':'editorial_candidate','display_title':candidate,'summary_sha256':sha(summary),
         'purpose':'visual_only_not_evidence','protocol':PAIR_PROTOCOL}

def editorial_verdict(value,candidate,summary):
 if not label_fits(candidate) or not isinstance(value,dict) or len(json.dumps(value,ensure_ascii=False))>2400:return None
 if (value.get('accepted') is not True or value.get('display_title')!=candidate
     or value.get('summary_sha256')!=sha(summary) or not isinstance(value.get('reason'),str)
     or not 1<=len(value['reason'])<=2000):return None
 return {**value,'policy':EDITORIAL_POLICY}
