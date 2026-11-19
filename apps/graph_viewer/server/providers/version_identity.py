"""Canonical transport identity of an immutable Source ViewVersion pair."""
import base64,json,re

PREFIX='topic-version:'

def topic_version_id(view_id,version_id):
    if not isinstance(view_id,str) or not view_id.strip() or type(version_id) is not int or version_id<1:raise ValueError('Invalid Source version pair')
    body=json.dumps([view_id,version_id],ensure_ascii=False,separators=(',',':')).encode()
    return PREFIX+base64.urlsafe_b64encode(body).decode().rstrip('=')

def parse_topic_version_id(value):
    if not isinstance(value,str) or not value.startswith(PREFIX):return None
    encoded=value[len(PREFIX):]
    try:
        if not re.fullmatch('[A-Za-z0-9_-]+',encoded):return None
        pair=json.loads(base64.urlsafe_b64decode(encoded+'='*(-len(encoded)%4)))
        if not isinstance(pair,list) or len(pair)!=2 or topic_version_id(*pair)!=value:return None
        return tuple(pair)
    except (ValueError,TypeError,UnicodeError):return None
