"""Issue once into the user's Keychain; stdout is the nonsecret server fingerprint only."""
import ctypes,hashlib,re,secrets,sys
from pathlib import Path

def issue(service,account):
    if Path('/usr/bin/security').is_file() is False or not all(re.fullmatch(r'[A-Za-z0-9_.-]{1,120}',v) for v in (service,account)):
        raise ValueError('Invalid Keychain reference')
    lib=ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    add=lib.SecKeychainAddGenericPassword
    add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_void_p,ctypes.c_void_p]
    service=service.encode('ascii');account=account.encode('ascii')
    with_raw=bytearray(secrets.token_hex(32).encode('ascii'))
    data=(ctypes.c_ubyte*len(with_raw)).from_buffer(with_raw)
    try:
        if add(None,len(service),service,len(account),account,len(with_raw),data,None)!=0:
            raise ValueError('Keychain rejected new item; no overwrite')
        return hashlib.sha256(with_raw).hexdigest()
    finally:
        for i in range(len(with_raw)): with_raw[i]=0

if __name__=='__main__':
    try: print(issue(*sys.argv[1:]))
    except Exception: sys.exit('Key issuance failed (no overwrite)')
