"""Typed user photo occurrences. I attests words/path; bytes come only from R URI.

Local image paths are compared as opaque identifiers and are never opened. The
attachment manifest is generated transport metadata, explicitly not user words.
"""
import json
import re
from .artifacts import data_uri,MAX_IMAGES

OPEN_PATH=re.compile(r'\bpath\s*=\s*([\"\'])(.*?)\1',re.S)


def is_photo_response(row):
    p=row.get('payload',{})
    return (isinstance(p,dict) and row.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='user'
        and isinstance(p.get('content'),list) and any(isinstance(v,dict) and v.get('type')=='input_image' for v in p['content']))


def stage(row,turn,store):
    p=row['payload'];meta=p.get('internal_chat_message_metadata_passthrough');parts=p.get('content')
    if not isinstance(meta,dict) or meta.get('turn_id')!=turn:raise ValueError('photo_turn_missing')
    kinds=meta.get('content_item_kinds');sources=meta.get('content_item_metadata')
    if not isinstance(kinds,list) or not isinstance(sources,list) or len(kinds)!=len(parts) or len(sources)!=len(parts):raise ValueError('photo_metadata_shape')
    for part,kind,source in zip(parts,kinds,sources):
        if not isinstance(part,dict):raise ValueError('photo_content_part_shape')
        expected='user.text' if part.get('type')=='input_text' else 'user.image' if part.get('type')=='input_image' else None
        if expected is None or kind!=expected or not isinstance(source,dict) or source.get('provenance')!={'type':'user'}:
            raise ValueError('photo_provenance_not_user')
    if p.get('id') is not None and (not isinstance(p['id'],str) or len(p['id'])>256):raise ValueError('photo_message_identity')
    components=[];images=0;i=0
    while i<len(parts):
        part=parts[i];text=part.get('text')
        framed=(part.get('type')=='input_text' and isinstance(text,str) and text.lstrip().startswith('<image ')
            and text.rstrip().endswith('>') and i+2<len(parts) and parts[i+1].get('type')=='input_image'
            and parts[i+2].get('type')=='input_text' and parts[i+2].get('text','').strip()=='</image>')
        if framed:
            match=OPEN_PATH.search(text)
            if not match or not match[2]:raise ValueError('photo_image_frame_path_missing')
            images+=1;image=parts[i+1];component={'kind':'image','path':match[2]}
            try:
                if images>MAX_IMAGES:raise ValueError('image_count_limit')
                artifact=data_uri(image.get('image_url'))
            except ValueError as failure:component['omission']=str(failure)
            else:
                store.store_artifact(artifact);component['artifact']=artifact['metadata']
            components.append(component);i+=3;continue
        if part.get('type')!='input_text' or not isinstance(text,str):raise ValueError('photo_image_frame_missing')
        if text.lstrip().startswith(('<environment_context>','# AGENTS.md','<codex_delegation>')):raise ValueError('photo_service_or_delegation_text')
        components.append({'kind':'text','text':text});i+=1
    if not images:raise ValueError('photo_image_frame_missing')
    return {'components':components,'generated_frame_parts':images*2}


def _group(components):
    grouped=[]
    for kind,value in components:
        if kind=='text' and grouped and grouped[-1][0]=='text':grouped[-1]=(kind,grouped[-1][1]+value)
        else:grouped.append((kind,value))
    return [(kind,value) for kind,value in grouped if kind!='text' or value]


def completion_matches(photo,row,session,turn,*,human=False):
    p=row.get('payload',{});item=p.get('item',{})
    if (row.get('type')!='event_msg' or p.get('type')!='item_completed' or p.get('turn_id')!=turn
            or not (p.get('thread_id')==session or human and p.get('thread_id') is None)
            or item.get('type')!='UserMessage' or not isinstance(item.get('content'),list)):return False
    expected=_group([(v['kind'],v['text'] if v['kind']=='text' else v['path']) for v in photo['components']])
    observed=[]
    for part in item['content']:
        if not isinstance(part,dict):return False
        if part.get('type')=='text' and isinstance(part.get('text'),str):observed.append(('text',part['text']))
        elif part.get('type')=='local_image' and isinstance(part.get('path'),str):observed.append(('image',part['path']))
        else:return False
    return expected==_group(observed)


def render(photo):
    text='';layout=[];refs=[];omissions=[]
    for component in photo['components']:
        if component['kind']=='text':
            if text:text+='\n\n[Generated separator between user text parts; not user words]\n'
            start=len(text);text+=component['text'];layout.append({'kind':'actual_user_text','start':start,'end':len(text)})
        elif component.get('artifact'):
            meta=component['artifact'];layout.append({'kind':'artifact','artifact_id':meta['artifact_id'],'not_interpreted':True})
            if not any(r['artifact_id']==meta['artifact_id'] for r in refs):refs.append(meta)
        else:
            reason=component['omission'];omissions.append(reason);layout.append({'kind':'unretained_media','reason':reason,'not_interpreted':True})
    manifest={'format':'native-user-attachments/v1','basis':'generated manifest; not user words',
        'layout':layout,'artifact_refs':refs,'not_interpreted':True}
    if omissions:manifest['media_omissions']=omissions
    text+='\n\n[Generated attachment manifest; not user words]\n'+json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return {'text':text,'artifact_ids':[r['artifact_id'] for r in refs],'omissions':omissions}
