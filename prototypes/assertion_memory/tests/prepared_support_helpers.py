"""Hermetic providers choose supplied fragment options by fixture meaning."""
import json


def record_text(record):
    return ''.join(fragment['text'] for fragment in record['fragments']) if 'fragments' in record else record.get('text','')


def selected_options(request,passages):
    selected=[]
    for passage in passages:
        found=False
        for record in request.records:
            if 'fragments' not in record:continue
            body=record_text(record)
            start=body.find(passage)
            if start<0:continue
            found=True;end=start+len(passage);cursor=0
            for fragment in record['fragments']:
                stop=cursor+len(fragment['text'])
                if cursor<end and start<stop:selected.append(fragment['option'])
                cursor=stop
        if not found:raise AssertionError('Fixture requested unsupported fragment text: '+passage)
    return list(dict.fromkeys(selected))


def drafts(request,*items,question=None):
    rows=[{'text':text,'question':question,'support_fragments':selected_options(request,passages)}
          for text,passages in items]
    if 'thought' in request.response_schema['properties']:
        if len(rows)!=1:raise AssertionError('A scalar construction fixture needs exactly one thought')
        return {'thought':rows[0],'needs_detail':None}
    return {'thoughts':rows,'needs_detail':None}


class PreparedModel:
    def __init__(self,*responses):self.responses=list(responses);self.requests=[]
    def __call__(self,request):
        self.requests.append(request)
        if not self.responses:raise AssertionError('unexpected model call: '+request.task)
        response=self.responses.pop(0)
        return json.dumps(response(request) if callable(response) else response)
