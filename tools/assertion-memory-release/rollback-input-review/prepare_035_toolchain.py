"""Extract only exact pre-hashed tool archives into a new disposable mount."""
from hashlib import file_digest
from pathlib import Path
import stat
import sys
import tarfile

from verify_035_sqlite_provider import PYTHON_SHA, PYTHON_SIZE
from materialize_035_public_runtime import NODE_SHA


def stage(source,destination,pin,size):
    st=source.lstat()
    if not stat.S_ISREG(st.st_mode) or not 0<st.st_size<=128*1024**2 or (size and st.st_size!=size):
        raise ValueError("Wrong runtime archive size")
    with source.open("rb") as f:
        if file_digest(f,"sha256").hexdigest()!=pin:
            raise ValueError("Wrong runtime archive digest")
        f.seek(0)
        destination.mkdir(mode=0o755)
        num,total=0,0
        with tarfile.open(fileobj=f,mode="r:*") as t:
            for m in t:
                num+=1
                total+=m.size
                if num>30000 or total>900*1024**2:
                    raise ValueError("Oversized unpacked runtime")
                # Stdlib data filter rejects members/links outside the isolated mount.
                t.extract(m,destination,filter="data")


if __name__=="__main__":
    source,destination=map(Path,sys.argv[1:])
    destination.mkdir(mode=0o755)
    stage(source/"native-libraries/cpython.tar.gz",destination/"python",PYTHON_SHA,PYTHON_SIZE)
    stage(source/"node.tar.xz",destination/"node",NODE_SHA,None)
