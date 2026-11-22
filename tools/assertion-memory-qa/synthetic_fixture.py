"""Create one new wholly fictional SQLite file; never touches an existing path."""
import os
from pathlib import Path
import sys
from prototypes.assertion_memory.mcp_server import ServiceConfig
from prototypes.assertion_memory.storage import MemoryStore
target=Path(sys.argv[1]).resolve()
fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
os.close(fd)
with MemoryStore(str(target)) as store:
    svc=ServiceConfig(db=str(target),provider='none',processing_session_ids=[]).service(store)
    event=svc.queue.capture_event('fixture-local','one','Ключ 🧩: ёж.',source='fixture://craft-vm',scope='fictional-vm',
        message_id='one',position=0,metadata={'role':'user','message_id':'one'})
    one=svc.thoughts.publish('В вымышленном ключе записано «ёж».',question='Что записано в ключе?',
        origin={'kind':'source_interpretation','event_id':event.id,'scope':'fictional-vm'},
        thought_id='thought-vm-synthetic-one',bases=[{'event_id':event.id,'span':{'start':8,'end':10},'meaning':'ёж'}])
    two=svc.thoughts.publish('Синтетическая загадка содержит ключ.',
        origin={'kind':'source_interpretation','event_id':event.id,'scope':'fictional-vm'},
        thought_id='thought-vm-synthetic-two',bases=[{'thought_id':one.id,'version':one.version}])
    svc.thoughts.connect(two.id,one.id,'Синтетическая связь')
