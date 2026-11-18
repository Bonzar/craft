"""Lossless fragment catalog; model selections never generate source addresses."""
from dataclasses import dataclass
import re
from .model import ModelError


@dataclass(frozen=True)
class Fragment:
    option:int
    record_index:int
    start:int
    end:int
    text:str


def _spans(text):
    start=0
    ends=[match.end() for match in re.finditer(r'(?<=[.!?;])\s+|\n+',text)]
    for end in [*ends,len(text)]:
        if end>start and text[start:end].strip():
            yield start,end
            start=end
    if start<len(text):
        yield start,len(text)


class PreparedSupport:
    def __init__(self,records,bases):
        if len(records)!=len(bases):raise ModelError('support material and pinned bindings must align')
        self.records,self.bases=records,bases
        self.fragments=[];self.wire=[]
        for index,(record,basis) in enumerate(zip(records,bases)):
            text=record.get('text')
            eligible=(isinstance(text,str) and record.get('role') not in ('question','whole_refresh_scope')
                and isinstance(basis,dict) and ('event_id' in basis or ('thought_id' in basis and 'version' in basis)))
            if not eligible:
                self.wire.append(dict(record));continue
            fragments=[]
            for start,end in _spans(text):
                fragment=Fragment(len(self.fragments),index,start,end,text[start:end])
                if not fragment.text.strip() and fragments:
                    previous=self.fragments.pop();fragments.pop()
                    fragment=Fragment(previous.option,index,previous.start,end,text[previous.start:end])
                self.fragments.append(fragment)
                fragments.append({'option':fragment.option,'text':fragment.text})
            if ''.join(item['text'] for item in fragments)!=text:
                raise ModelError('prepared support did not preserve every input character')
            self.wire.append({**{key:value for key,value in record.items() if key!='text'},'fragments':fragments})

    def _basis(self,fragment):
        basis=dict(self.bases[fragment.record_index])
        offset=basis.get('span',{}).get('start',0)
        basis.pop('content_hash',None)  # The publisher hashes this newly selected exact span.
        return dict(basis,span={'start':offset+fragment.start,'end':offset+fragment.end},meaning=fragment.text)

    def resolve(self,choices):
        if not isinstance(choices,list) or not choices or any(type(value) is not int or not 0<=value<len(self.fragments) for value in choices):
            raise ModelError('support must select existing prepared fragment options')
        fragments=[self.fragments[value] for value in dict.fromkeys(choices)]
        indexes=list(dict.fromkeys(item.record_index for item in fragments))
        return [self.records[index] for index in indexes],[self._basis(item) for item in fragments]

    def selected_packet(self,selections):
        owners={option:[] for option in range(len(self.fragments))}
        for index,choices in enumerate(selections):
            for option in choices:owners[option].append(index)
        return [dict(record,fragments=[dict(fragment,selected_for_grounding=bool(owners[fragment['option']]),
            selected_by=owners[fragment['option']]) for fragment in record['fragments']])
            if 'fragments' in record else dict(record) for record in self.wire]

    def options_for_bases(self,bases):
        result=[]
        for fragment in self.fragments:
            actual=self._basis(fragment)
            for basis in bases:
                same=all(actual.get(key)==basis.get(key) for key in ('event_id','thought_id','version'))
                span=basis.get('span')
                if same and (span is None or (span['start']<=actual['span']['start'] and actual['span']['end']<=span['end'])):
                    result.append(fragment.option);break
        return result


def prepare_support(records,bases):
    return PreparedSupport(records,bases)
