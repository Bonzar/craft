"""Deterministic text projection of native tool results, with credential omission.

Noncredential values/conditions remain intact. The original native record stays
local; structured/redacted projections explicitly identify their format.
"""
import json
import re

CREDENTIAL_KEYS = {'sourcerepositorycredential','servicebearer','bearer','bearertoken',
    'authorization','proxyauthorization','apikey','xapikey','accesstoken','refreshtoken',
    'clientsecret','password','credentials','cookie','setcookie'}
TEXT_KINDS = {'text','input_text','output_text'}
MARKER = '[REDACTED:credential]'
BEARER = re.compile(r'(?i)^([ \t]*Bearer[ \t]+)[A-Za-z0-9._~+/-]{16,}=*[ \t]*$')
HEADER = re.compile(r'(?im)^(\s*(?:Authorization|Proxy-Authorization|X-Api-Key|Api-Key)\s*:\s*)([^\r\n]+)')
DEFAULT_CREDENTIAL_ENV = 'ASSERTION_MEMORY_TOKEN'


def _credential_fields(env_name):
    names={DEFAULT_CREDENTIAL_ENV}
    if env_name is not None:
        if not isinstance(env_name,str) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',env_name) is None:
            raise ValueError('Invalid credential environment name')
        names.add(env_name)
    keys=CREDENTIAL_KEYS | {re.sub('[^a-z0-9]','',name.lower()) for name in names}
    name_pattern='|'.join(re.escape(name) for name in sorted(names))
    assignment=re.compile(r'(?im)^([ \t]*(?:(?:export|declare[ \t]+-x)[ \t]+)?(?:'
        +name_pattern+r')[ \t]*=[ \t]*)'
        +r'''("(?:[^"\\\r\n]|\\.)+"|'[^'\r\n]+'|[^ \t\r\n'"#;|&<>]+)''')
    quoted_field=re.compile(r'''(?i)(["'](?:'''+name_pattern+r''')["']\s*:\s*)'''
        +r'''("(?:[^"\\\r\n]|\\.)+"|'[^'\r\n]+')''')
    return keys,(assignment,quoted_field)


def _redact(value,depth,credential_keys,patterns):
    if depth>20:raise ValueError('Tool projection nesting exceeds bound')
    count=0
    if isinstance(value,dict):
        result={}
        for key,part in value.items():
            normalized=re.sub('[^a-z0-9]','',str(key).lower())
            if normalized in credential_keys and part not in (None,'',False):
                result[key]=MARKER;count+=1
            else:
                result[key],added=_redact(part,depth+1,credential_keys,patterns);count+=added
        return result,count
    if isinstance(value,list):
        result=[]
        for part in value:
            clean,added=_redact(part,depth+1,credential_keys,patterns);result.append(clean);count+=added
        return result,count
    if isinstance(value,str):
        if value.lstrip().startswith(('{','[')):
            try:parsed=json.loads(value)
            except ValueError:parsed=None
            if isinstance(parsed,(dict,list)):
                clean,added=_redact(parsed,depth+1,credential_keys,patterns)
                if added:return json.dumps(clean,ensure_ascii=False,sort_keys=True,separators=(',',':')),added
        clean,env_fields=patterns[0].subn(lambda match:match[1]+MARKER,value)
        clean,quoted_fields=patterns[1].subn(lambda match:match[1]+match[2][0]+MARKER+match[2][-1],clean)
        clean,headers=HEADER.subn(lambda match:match[1]+MARKER,clean)
        clean,bearers=BEARER.subn(lambda match:match[1]+MARKER,clean)
        return clean,env_fields+quoted_fields+headers+bearers
    if value is None or isinstance(value,(int,float,bool)):return value,0
    raise ValueError('Unsupported native tool value')


def project_result(value,*,credential_env_var=None):
    """Keep text and emit byte-backed descriptors for optional native image blocks."""
    from .artifacts import data_uri,MAX_IMAGES
    artifacts=[];omissions=[];image_count=0
    credential_keys,patterns=_credential_fields(credential_env_var)
    clean,count=_redact(value,0,credential_keys,patterns)
    def transform(node,depth=0):
        nonlocal image_count
        if depth>20:raise ValueError('Tool projection nesting exceeds bound')
        if isinstance(node,dict):
            if node.get('type') in TEXT_KINDS:return node
            if node.get('type') in ('input_image','image'):
                image_count+=1
                try:
                    if image_count>MAX_IMAGES:raise ValueError('image_count_limit')
                    image=data_uri(node.get('image_url'))
                except ValueError as failure:
                    reason=str(failure);omissions.append(reason)
                    return {'type':'unretained_media','source_type':node['type'],'reason':reason,'not_interpreted':True}
                meta=image['metadata']
                if not any(a['metadata']['artifact_id']==meta['artifact_id'] for a in artifacts):artifacts.append(image)
                result={'type':'artifact','source_type':node['type'],**meta}
                if node.get('detail') in ('low','high','auto','original'):result['detail']=node['detail']
                return result
            if node.get('type') in ('audio','input_audio','video','input_video','resource'):
                omissions.append('unsupported_nontext_type')
                return {'type':'unretained_media','reason':'unsupported_nontext_type','not_interpreted':True}
            return {key:transform(part,depth+1) for key,part in node.items()}
        if isinstance(node,list):return [transform(part,depth+1) for part in node]
        return node
    if isinstance(value,list):
        for part in value:
            if not isinstance(part,dict) or part.get('type') not in TEXT_KINDS|{'input_image','image','audio','input_audio','video','input_video','resource'}:
                raise ValueError('Unsupported native tool block')
            if part.get('type') in TEXT_KINDS and (not isinstance(part.get('text'),str) or set(part)-{'type','text','annotations'}):
                raise ValueError('Unsupported native tool text block')
    if not isinstance(value,(str,list,dict)):raise ValueError('Unsupported native tool result')
    if isinstance(value,list):clean=[transform(part) for part in clean]
    if isinstance(value,str) and not count and not artifacts and not omissions:
        return {'text':value,'redactions':0,'artifacts':[],'omissions':[]}
    envelope={'format':'native-tool-output/v1','output':clean,'credential_redactions':count}
    if artifacts:envelope['artifact_refs']=[a['metadata'] for a in artifacts]
    if omissions:envelope['media_omissions']=omissions
    text=json.dumps(envelope,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(text)>1_000_000:raise ValueError('Tool projection exceeds capture size')
    return {'text':text,'redactions':count,'artifacts':artifacts,'omissions':omissions}


def project(value):
    result=project_result(value)
    if result['artifacts'] or result['omissions']:raise ValueError('Native media requires explicit artifact admission')
    return result['text'],result['redactions']
