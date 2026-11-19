"""Constrained semantic responses; technical record identities never belong here."""


def _object(properties):
    return {'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}


def response_schema(task, *, support_count=None, single_thought=False, review_option_count=0):
    text = {'type':'string'}
    question = {'type':['string','null']}
    if task == 'Navigate thought graph':
        return _object({'action':{'type':'string','enum':['inspect','focus','unknown']},
                        'question':{'type':'null'},'quote':question,'text':text})
    if task == 'Construct thought':
        choices={'type':'integer','minimum':0,'maximum':max(0,(support_count or 0)-1)}
        item = _object({'text':text,'question':question,
                        'support_fragments':{'type':'array','items':choices,'minItems':1}})
        detail = _object({'question':text,'text':text})
        if single_thought:
            return _object({'thought':item if support_count else {'type':'null'},
                            'needs_detail':{'type':'null'} if support_count else {'anyOf':[detail,{'type':'null'}]}})
        thoughts = {'type':'array','items':item}
        if not support_count:
            thoughts['maxItems'] = 0
        return _object({'thoughts':thoughts,'needs_detail':{'anyOf':[detail,{'type':'null'}]}})
    if task == 'Check ready thought':
        return _object({'verdict':{'type':'string','enum':['ready','build','drill']},'text':text,'question':question})
    if task == 'Verify thought' and review_option_count:
        option = {'type':'integer','minimum':0,'maximum':review_option_count-1}
        return _object({'verdict':{'type':'string','enum':['accept','reject','drill']}, 'text':text,
                        'open_support':{'anyOf':[option,{'type':'null'}]}})
    if task == 'Find useful shared thought question':
        return _object({'question':question,'text':text})
    return _object({'verdict':{'type':'string','enum':['accept','reject']},'text':text})
