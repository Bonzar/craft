"""Only the configured TEST lane; isolated interpreter in the forced command."""
import json
import os
from pathlib import Path
import pwd
import stat
import sys

CONFIG = Path('/etc/assertion-memory-visual/configured')
CORE = Path('/etc/assertion-release/shared')
ROOT = Path('/var/lib/assertion-memory-visual')
STAGES = ('test', 'prod')



def trusted(path, check):
    info = path.lstat()
    if not check(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
        raise ValueError('untrusted_installed_path')


def receive(stage, command, stream, *, verify, deploy, command_pattern, signers, state):
    match = command_pattern.fullmatch(command)
    if not match or match[1] != stage:
        raise ValueError('command_denied')
    verify(signers, command, stream)
    release = deploy(stream, match[3], root=state)
    return dict(environment=stage, revision=match[2], archive_sha256=match[3], release=release)


def main():
    try:
        if (not sys.flags.isolated or len(sys.argv) != 2 or sys.argv[1] not in STAGES
                or Path(__file__) != CONFIG/'receive.py'):
            raise ValueError('wrong_entrypoint')
        stage = sys.argv[1]
        writer = f'assertion-visual-{stage}-deploy'
        caller = f'assertion-cfg-vis-{stage}-deploy'
        product = CONFIG.parent/stage
        policy = CONFIG/stage
        if os.getuid() != os.geteuid() or pwd.getpwuid(os.getuid()).pw_name != writer:
            raise ValueError('wrong_writer')
        if os.environ.get('SUDO_USER') != caller:
            raise ValueError('wrong_configured_caller')
        for p in (CONFIG, CORE, product, policy):
            trusted(p, stat.S_ISDIR)
        trusted(CONFIG/'receive.py', stat.S_ISREG)
        trusted(policy/'allowed-signers', stat.S_ISREG)
        sys.path[:0] = [str(CORE), str(product)]
        from visual_auth import COMMAND, verify_frame
        from visual_policy import deploy_verified
        reply = receive(stage, os.environ.get('SSH_ORIGINAL_COMMAND',''), sys.stdin.buffer,
                        verify=verify_frame, deploy=deploy_verified, command_pattern=COMMAND,
                        signers=policy/'allowed-signers', state=ROOT/stage)
    except ValueError as err:
        if str(err) == 'command_denied':
            print('{"status":"rejected","error":"command_denied"}')
            return 1
        print('{"status":"rejected","error":"configured_visual_receiver_denied"}')
        return 1
    except Exception:
        print('{"status":"rejected","error":"configured_visual_receiver_denied"}')
        return 1
    print(json.dumps(reply,separators=(',',':')))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
