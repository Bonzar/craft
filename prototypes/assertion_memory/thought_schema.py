"""Constrained semantic responses; technical record identities never belong here."""


def _object(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def response_schema(task, *, support_count=None):
    text = {'type':'string'}
    question = {'type':['string','null']}
    if task == 'Navigate thought graph':
        return _object({'action':{'type':'string','enum':['search','inspect','focus','unknown']},
                        'question':question,'quote':question,'text':text})
    if task == 'Construct thought':
        choices={'type':'integer','minimum':0,'maximum':max(0,(support_count or 0)-1)}
        item = _object({'text':text,'question':question,
                        'support_fragments':{'type':'array','items':choices,'minItems':1}})
        detail = _object({'question':text,'text':text})
        return _object({'thoughts':{'type':'array','items':item,**({'maxItems':0} if not support_count else {})},
                        'needs_detail':{'anyOf':[detail,{'type':'null'}]}})
    if task == 'Check ready thought':
        return _object({'verdict':{'type':'string','enum':['ready','build','drill']},'text':text,'question':question})
    if task == 'Find useful shared thought question':
        return _object({'question':question,'text':text})
    return _object({'verdict':{'type':'string','enum':['accept','reject']},'text':text})
