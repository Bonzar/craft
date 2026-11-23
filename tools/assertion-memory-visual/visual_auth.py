"""Visual signature policy; common code still owns SSH/TAR/activation."""
from pathlib import Path
import re
import subprocess
import tempfile

NAMESPACE = 'assertion-memory-visual'
COMMAND = re.compile(r'deploy-visual (test|prod) ([a-f0-9]{40}) ([a-f0-9]{64})\Z')


def message(command):
    if not isinstance(command, str) or not COMMAND.fullmatch(command):
        raise ValueError('command_denied')
    return ('assertion-memory-visual-v2 ' + command + '\n').encode('ascii')


def sign(command, key, *, directory=None):
    with tempfile.TemporaryDirectory(prefix='visual-sign-',dir=directory) as tmp:
        signing_key=Path(tmp)/'identity'
        signing_key.write_text(key.strip()+'\n',encoding='ascii')
        signing_key.chmod(0o600)
        source=Path(tmp)/'proof'
        source.write_bytes(message(command))
        subprocess.run(['/usr/bin/ssh-keygen','-Y','sign','-f',str(signing_key),'-n',NAMESPACE,str(source)],
                       check=True, stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        proof=source.with_suffix('.sig').read_bytes()
    return f'{len(proof)}\n'.encode()+proof


def verify_frame(signers, command, stream):
    mode=COMMAND.fullmatch(command).group(1)
    length=stream.readline(7)
    if not re.fullmatch(rb'[1-9][0-9]{0,3}\n', length):
        raise ValueError('invalid_signature_framing')
    signature=stream.read(int(length))
    if len(signature)!=int(length):
        raise ValueError('invalid_signature_framing')
    with tempfile.TemporaryDirectory(prefix='visual-proof-') as tmp:
        saved=Path(tmp)/'proof'
        saved.write_bytes(signature)
        verdict=subprocess.run(['/usr/bin/ssh-keygen','-Y','verify','-f',str(signers),
                        '-I','visual-'+mode,'-n',NAMESPACE,'-s',str(saved)],input=message(command),
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10,check=False)
    if verdict.returncode:
        raise ValueError('invalid_release_signature')
