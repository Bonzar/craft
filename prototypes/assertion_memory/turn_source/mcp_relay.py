"""Single conventional stdio server relay to its isolated per-job IPC directory."""
import fcntl
import os
from pathlib import Path
import sys
from transport import read_frame,encode

if __name__=='__main__':
    if len(sys.argv)!=2:
        raise SystemExit(2)
    root=Path(sys.argv[1])
    with (root/'relay.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with os.fdopen(os.open(root/'request',os.O_WRONLY),'wb',buffering=0) as incoming, \
             os.fdopen(os.open(root/'response',os.O_RDONLY),'rb') as output:
            while True:
                try: message=read_frame(sys.stdin.buffer)
                except EOFError:break
                pending=memoryview(encode(message))
                while pending:pending=pending[incoming.write(pending):]
                if 'id' in message:
                    sys.stdout.buffer.write(encode(read_frame(output)))
                    sys.stdout.buffer.flush()
