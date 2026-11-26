"""Derive exact nonsecret release targets from private native get_site metadata.

Input is handled via stdin. Output only the six release pin variables.
Never put the raw native response (which may contain sensitive metadata) in Git.
"""
import hashlib
import json
import sys
from urllib.parse import urlsplit

def digest(data):
    return hashlib.sha256(data).hexdigest()

def stable(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')+b'\n'

def origin(url):
    parts=urlsplit(url)
    if (parts.scheme!='https' or parts.hostname is None or parts.port is not None or
        parts.username is not None or parts.password is not None or
        parts.netloc!=parts.hostname or not parts.hostname.endswith('.chatgpt.site')):
        raise ValueError('expected exact native Site origin')
    return 'https://'+parts.netloc

def checked_acl(site):
    a=site['access_policy']
    if (site.get('current_user_role')!='owner' or site.get('id')!=a.get('project_id') or
        a.get('access_mode')!='custom' or site.get('access_mode')!='custom' or
        type(a.get('external_visitor_count')) is not int or a['external_visitor_count']!=0):
        raise ValueError('Site not verified owner-private')
    lists=('allowed_workspace_group_ids','allowed_tenant_group_ids','allowed_groups','allowed_editors')
    if any(a.get(key)!=[] for key in lists):
        raise ValueError('unexpected additional audience or editor')
    users=a.get('allowed_users')
    accounts=a.get('allowed_account_user_ids')
    if type(users) is not list or len(users)!=1 or type(accounts) is not list or len(accounts)!=1:
        raise ValueError('expected one allowed owner')
    u=users[0]
    if (u.get('role')!='owner' or u.get('account_user_id')!=accounts[0] or
            not isinstance(accounts[0],str) or not accounts[0] or
            u.get('pending') not in (None,False) or u.get('is_external') not in (None,False)):
        raise ValueError('owner mismatch')
    return {'version':1,'access_mode':'custom','allowed_account_user_ids':accounts,
            'allowed_users':[{'account_user_id':accounts[0],'role':'owner','pending':False,'is_external':False}],
            **{key:[] for key in lists},'external_visitor_count':0}

def scope(site,lane):
    if lane not in ('test','prod') or not isinstance(site.get('id'),str) or not site['id']:
        raise ValueError('invalid target lane')
    mcp=site['mcp_connection']
    actual=origin(mcp['mcp_url'])
    if actual!=origin(site['current_live_url']) or urlsplit(mcp['mcp_url']).path!='/mcp' or not mcp.get('plugin_id'):
        raise ValueError('native plugin or Site origin mismatch')
    prefix='MEMORY_'+lane.upper()+'_SITE_'
    return {prefix+'PROJECT_SHA256':digest(site['id'].encode('utf-8')),
            prefix+'ORIGIN_SHA256':digest(actual.encode('utf-8')),
            prefix+'ACCESS_SHA256':digest(stable(checked_acl(site)))}

def main():
    data=json.load(sys.stdin)
    if type(data) is not dict or set(data)!={'test','prod'}:raise ValueError('expected two native Site results')
    if (data['test'].get('id')==data['prod'].get('id') or
            origin(data['test']['mcp_connection']['mcp_url'])==origin(data['prod']['mcp_connection']['mcp_url'])):
        raise ValueError('same Site used twice')
    print(stable(scope(data['test'],'test')|scope(data['prod'],'prod')).decode(),end='')
if __name__=='__main__':main()
